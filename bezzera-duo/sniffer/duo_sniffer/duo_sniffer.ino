// Passiver Zwei-Kanal-UART-Sniffer fuer die Leitung Mainboard <-> Display
// der Bezzera Duo / Matrix. Nur EMPFANG: Kein Pin des ESP32 treibt die
// Leitung, das Original-Display arbeitet unveraendert weiter.
//
// Board: jedes klassische ESP32-Devkit (Arduino-ESP32 2.x oder 3.x).
// USB-Seriell zum Rechner: 921600 Baud.
//
// Verdrahtung (Details in ../../README.md):
//   Leitung 1 --[Spannungsteiler bei 5 V]--> GPIO16 (Kanal A)
//   Leitung 2 --[Spannungsteiler bei 5 V]--> GPIO17 (Kanal B)
//   GND Maschine ------------------------> GND ESP32
//   GPIO25 und GPIO26 NICHT anschliessen (formale TX-Pins, siehe unten).
//
// Ausgabe, eine Zeile je Frame (Frame = Bytes ohne Pause dazwischen):
//   <millis> <A|B> <hex hex ...>
// Das ist genau das Eingabeformat von ../../tools/duo_sniff.py.
//
// Befehle ueber den seriellen Monitor (Zeile mit Enter abschliessen):
//   scan         2 s lang Flanken messen: Ruhepegel und kuerzester Puls
//                -> Baudrate und ob die Leitung invertiert ist
//   b <baud>     Baudrate setzen, z. B. "b 9600"
//   i            Invertierung umschalten
//   p <8N1|8E1>  Paritaet
//   g <ms>       Pause, ab der ein neuer Frame beginnt (Standard: auto)
//   m <text>     Markierung ins Log schreiben, z. B. "m Bruehtemp 93 -> 94"
//   x            Ausgabe anhalten / fortsetzen
//   ?            Zustand anzeigen

#include <Arduino.h>

static const int PIN_A = 16;
static const int PIN_B = 17;
// TX braucht der UART-Treiber formal. Diese beiden Pins bleiben UNBESCHALTET.
// Nicht -1: je nach Arduino-ESP32-Version faellt Serial1 dann auf seinen
// Standard-TX GPIO10 zurueck, und der gehoert auf den meisten Devkits zum Flash.
static const int PIN_TX_FREI_A = 25;
static const int PIN_TX_FREI_B = 26;

static uint32_t baud = 9600;
static bool invertiert = false;
static uint32_t paritaet = SERIAL_8N1;
static uint32_t pauseMsFest = 0;  // 0 = automatisch aus der Baudrate
static bool pausiert = false;

struct Kanal {
  HardwareSerial *uart;
  int pin;
  int txFrei;
  char name;
  uint8_t puffer[512];
  size_t n;
  uint32_t letztesByteUs;
  uint32_t startMs;
};

static Kanal kanaele[2] = {
    {&Serial1, PIN_A, PIN_TX_FREI_A, 'A', {}, 0, 0, 0},
    {&Serial2, PIN_B, PIN_TX_FREI_B, 'B', {}, 0, 0, 0},
};

static uint32_t pauseUs() {
  if (pauseMsFest) return pauseMsFest * 1000;
  // 3 Zeichenzeiten (je 10 Bit), mindestens 2 ms: USB-Latenz und das
  // FIFO-Timeout des ESP32 wuerden kuerzere Pausen ohnehin verschlucken.
  uint32_t us = 30UL * 1000000UL / baud;
  return us < 2000 ? 2000 : us;
}

static void uartsStarten() {
  for (auto &k : kanaele) {
    k.uart->end();
    k.uart->setRxBufferSize(4096);
    // Gesendet wird nie etwas; txFrei ist nirgends angeschlossen.
    k.uart->begin(baud, paritaet, k.pin, k.txFrei, invertiert);
    k.n = 0;
  }
}

static void zustand() {
  Serial.printf("# baud=%lu invert=%d paritaet=%s pause=%lu us pins A=%d B=%d\n",
                (unsigned long)baud, invertiert, paritaet == SERIAL_8E1 ? "8E1" : "8N1",
                (unsigned long)pauseUs(), PIN_A, PIN_B);
}

static void frameAusgeben(Kanal &k) {
  if (!k.n) return;
  if (!pausiert) {
    Serial.printf("%lu %c", (unsigned long)k.startMs, k.name);
    for (size_t i = 0; i < k.n; i++) Serial.printf(" %02x", k.puffer[i]);
    Serial.print('\n');
  }
  k.n = 0;
}

// ─── scan: Baudrate und Polaritaet aus den Flanken ─────────────────────────

static volatile uint32_t flankeUs[2];
static volatile uint32_t kuerzesterPuls[2];
static volatile uint32_t flanken[2];

static void IRAM_ATTR flanke(int i) {
  uint32_t jetzt = micros();
  uint32_t d = jetzt - flankeUs[i];
  flankeUs[i] = jetzt;
  flanken[i]++;
  if (d >= 2 && d < kuerzesterPuls[i]) kuerzesterPuls[i] = d;  // < 2 us: Stoerung
}
static void IRAM_ATTR flankeA() { flanke(0); }
static void IRAM_ATTR flankeB() { flanke(1); }

static uint32_t naechsteBaud(uint32_t pulsUs) {
  static const uint32_t raten[] = {1200, 2400, 4800, 9600, 14400, 19200, 28800,
                                   38400, 57600, 76800, 115200, 230400, 250000};
  float roh = 1e6f / pulsUs;
  uint32_t best = raten[0];
  for (uint32_t r : raten)
    if (fabsf(r - roh) < fabsf(best - roh)) best = r;
  return best;
}

static void scan() {
  for (auto &k : kanaele) k.uart->end();
  for (int i = 0; i < 2; i++) {
    pinMode(kanaele[i].pin, INPUT);
    kuerzesterPuls[i] = UINT32_MAX;
    flanken[i] = 0;
    flankeUs[i] = micros();
  }
  // Ruhepegel: 200 Stichproben ueber 200 ms, Mehrheit gewinnt.
  int hoch[2] = {0, 0};
  for (int s = 0; s < 200; s++) {
    for (int i = 0; i < 2; i++) hoch[i] += digitalRead(kanaele[i].pin);
    delay(1);
  }
  attachInterrupt(PIN_A, flankeA, CHANGE);
  attachInterrupt(PIN_B, flankeB, CHANGE);
  delay(2000);
  detachInterrupt(PIN_A);
  detachInterrupt(PIN_B);

  for (int i = 0; i < 2; i++) {
    Serial.printf("# scan %c: Ruhepegel %s (%d%% high), %lu Flanken", kanaele[i].name,
                  hoch[i] > 100 ? "HIGH" : "LOW", hoch[i] / 2, (unsigned long)flanken[i]);
    if (flanken[i] < 20) {
      Serial.print(" -> kaum Verkehr. Richtiger Draht? GND verbunden?\n");
      continue;
    }
    uint32_t p = kuerzesterPuls[i];
    Serial.printf(", kuerzester Puls %lu us -> ~%lu Baud, naechste Standardrate %lu, %s\n",
                  (unsigned long)p, (unsigned long)(1000000UL / p), (unsigned long)naechsteBaud(p),
                  hoch[i] > 100 ? "normal (nicht invertiert)" : "INVERTIERT (Befehl i)");
  }
  Serial.print("# Hinweis: ab ~100 kBaud ist micros() zu grob, dann Logic Analyzer nehmen.\n");
  uartsStarten();
}

// ─── Befehle ───────────────────────────────────────────────────────────────

static String eingabe;

static void befehl(String z) {
  z.trim();
  if (!z.length()) return;
  char c = z.charAt(0);
  String arg = z.substring(1);
  arg.trim();
  switch (c) {
    case 'b': {
      long v = arg.toInt();
      if (v >= 300 && v <= 2000000) {
        baud = v;
        uartsStarten();
      }
      break;
    }
    case 'i':
      invertiert = !invertiert;
      uartsStarten();
      break;
    case 'p':
      paritaet = arg.equalsIgnoreCase("8E1") ? SERIAL_8E1 : SERIAL_8N1;
      uartsStarten();
      break;
    case 'g':
      pauseMsFest = arg.toInt();
      break;
    case 'm':
      Serial.printf("# %s\n", arg.c_str());
      return;
    case 'x':
      pausiert = !pausiert;
      break;
    case 's':
      scan();
      break;
  }
  zustand();
}

void setup() {
  Serial.begin(921600);
  delay(300);
  Serial.print("# Bezzera Duo Sniffer - passiv, sendet nichts auf die Maschinenleitung\n");
  uartsStarten();
  zustand();
  Serial.print("# Erst 'scan' eingeben, dann mit 'b <baud>' und ggf. 'i' einstellen.\n");
}

void loop() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n' || c == '\r') {
      befehl(eingabe);
      eingabe = "";
    } else if (eingabe.length() < 120) {
      eingabe += c;
    }
  }

  uint32_t jetzt = micros();
  for (auto &k : kanaele) {
    while (k.uart->available()) {
      if (k.n && jetzt - k.letztesByteUs > pauseUs()) frameAusgeben(k);
      if (!k.n) k.startMs = millis();
      uint8_t b = k.uart->read();
      if (k.n < sizeof(k.puffer)) k.puffer[k.n++] = b;
      else frameAusgeben(k);
      k.letztesByteUs = jetzt = micros();
    }
    if (k.n && micros() - k.letztesByteUs > pauseUs()) frameAusgeben(k);
  }
}
