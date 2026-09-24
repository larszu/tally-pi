# Bezzera Duo / Matrix: Protokoll zwischen Mainboard und Display

Arbeitsstand, um die Verbindung zwischen Mainboard (7661047.xx) und dem
3,5"-Touchdisplay (5963201.xx) der Bezzera Duo DE/MN und der baugleichen
Matrix zu entschlüsseln. Ziel ist dasselbe wie beim Reddit-Projekt von
*Vivid-Ad-2039*: ein ESP32, der Register liest und schreibt.

> Dieser Ordner hat mit tally-pi nichts zu tun. Er liegt hier nur, weil hier
> gearbeitet wurde, und gehört auf Dauer in ein eigenes Repo.

## 1. Recherche (Stand 2026-09-24)

### Gibt es schon Code? Nein.

| Suche | Ergebnis |
|---|---|
| GitHub-Code `setwarmup bezzera`, `"/set/espresso"` | 0 Treffer |
| GitHub-Nutzer `Vivid-Ad-2039` | existiert nicht |
| GitHub-Repos `bezzera` | nur [BB005-Mühlentimer](https://github.com/hellgelino/bezzera-bb005-digital-timer) und [bezzi-tank](https://github.com/hcrohland/bezzi-tank) (Ultraschall-Füllstand für die Duo, fasst die Elektronik nicht an) |
| GitHub-Code `bezzera duo esp32` | zusätzlich nur [brewos-io/firmware](https://github.com/brewos-io/firmware). Das *ersetzt* die Steuerung komplett und listet Duo/Matrix nur als ungetestet, ohne Protokoll |
| Reddit-Profil | von hier aus nicht erreichbar (reddit gesperrt). Bitte selbst ansehen: `old.reddit.com/user/Vivid-Ad-2039` |

### Der eigentliche Fund: Das Mainboard ist von Gicar

Händler beschreiben die Steuerung der Duo/Matrix MN als „Gicar PID controller“
([Whole Latte Love, Matrix MN](https://www.wholelattelove.com/products/bezzera-matrix-mn-dual-boiler-espresso-machine)).
Für Gicar-Steuerungen gibt es zwei sauber dokumentierte Protokolle. Eines
davon passt sehr wahrscheinlich, oder beide in Varianten:

**A) Gicar-ASCII-Registerprotokoll**
([antondlr/gicar-serial](https://github.com/antondlr/gicar-serial), Ascaso Baby T, Gicar „3d5 Maestro“)

```
115200 Baud, 8N1, reiner ASCII-Text
Lesen:     r OOOO LLLL CC            r000500D7xx  -> 0xD7 Bytes ab Offset 5
Antwort:   r OOOO LLLL <hex-daten> CC
Schreiben: w OOOO LLLL <hex-daten> CC   w005600010164  (Dampfkessel an)
Quittung:  w OOOO LLLL OK CC            w00560001OK9D
CC = Summe aller Zeichen davor, mod 256, als zwei Hex-Zeichen
Temperaturen als u16 little-endian, Wert x 10 (93,5 °C = 935 = A7 03)
```

Das passt auffällig gut zur Featureliste von Vivid-Ad-2039 („reading/writing
registers“, „looks quite simple“) und zu den Bereichen 89–96 °C / 100–130 °C.
**Wichtige Einschränkung** aus demselben Repo: Diese Schnittstelle trägt dort
*nur Einstellungen*, keine Live-Temperaturen und keinen Shot-Timer. Die liefen
bei Ascaso über eine zweite serielle Verbindung. Bei der Duo zeigt das Display
aber live Temperatur und Chrono, also muss über das Display-Kabel mehr laufen.

**B) Gicar-Binärprotokoll Display ↔ Steuerplatine**
([magnusnordlander/lelit-bianca-protocol](https://github.com/magnusnordlander/lelit-bianca-protocol), Lelit Bianca V2)

```
9600 Baud, 8N1, INVERTIERT (Ruhepegel low!)
6-poliges Kabel: 12 V, TX 3,3 V, RX 5 V, GND, 3,3 V, 3,3 V
Display ist Master, sendet 80 ii jj bb zz   (Relais-Bits, Tasten, Prüfsumme)
Platine antwortet 81 ... zz, 18 Byte        (ADC-Rohwerte der Fühler, Füllstände, Hebel/Microswitch)
zz = Summe mod 128
```

Die Aufteilung ähnelt der Duo. Bei der Bianca ist das Display der eigentliche
Regler (PID), die Platine schaltet nur Relais und misst. Wenn die Duo auch so
arbeitet, liegen die Sollwerte im **Display**, und das Mainboard kennt gar
keine Temperaturgrenzen. Dann wäre Vivid-Ad-2039s „Passthrough“ ein ESP32,
der das Display ersetzt oder die Pakete umschreibt.

Die Werte 1901/1906 als Werks-Passwörter nach Mainboard-Reset sprechen
eher dafür, dass Einstellungen (auch) auf dem Mainboard liegen, also für A
oder eine Mischform. Klären lässt sich das nur mit einem Mitschnitt.

## 2. Messen: So geht es weiter

> ⚠️ In der Maschine liegen 230 V. Stecker ziehen, bevor du etwas anklemmst.
> Mit laufender Maschine nur an die bereits verlegten Messleitungen gehen.
> Kessel und Dampf sind heiß.

### 2.1 Stecker finden und Pegel messen

1. Stecker zwischen Mainboard und Displayplatine finden, Adern zählen, Foto machen.
2. Maschine an, Multimeter gegen GND (Gehäuse ist *nicht* sicher GND, das
   Minus der Elektronik nehmen, meist die Ader mit der größten Kupferfläche):
   - Adern mit festen 12 V / 5 V / 3,3 V sind Versorgung.
   - Datenleitungen zeigen einen „krummen“ Mittelwert, der leicht zappelt.
   - Ruhepegel nahe Versorgung = normales UART. Nahe 0 V = invertiert (wie Lelit).
   - Zwei Datenadern, die sich spiegeln (A = 2,8 V, B = 2,2 V, gegenläufig
     zappelnd) = differenziell, RS-485. Dann einen MAX485-/SP3485-Wandler
     davor (nur RO benutzen, DE/RE fest auf GND) statt direkt an den ESP32.

### 2.2 Sniffer anklemmen

Firmware: [`sniffer/duo_sniffer/duo_sniffer.ino`](sniffer/duo_sniffer/duo_sniffer.ino),
Arduino-IDE, Board „ESP32 Dev Module“. Der Sniffer sendet nichts, das Original-
Display bleibt angeschlossen und läuft normal weiter.

```
Datenleitung 1 ──[10k]──┬── GPIO16 (Kanal A)      Spannungsteiler nur bei 5-V-Pegel:
                        └──[20k]── GND            5 V · 20/(10+20) = 3,3 V
Datenleitung 2 ──[10k]──┬── GPIO17 (Kanal B)
                        └──[20k]── GND
GND Maschine ──────────────── GND ESP32
```

Bei 3,3-V-Pegel reichen 1-kΩ-Schutzwiderstände in Reihe. Den ESP32 per USB
vom Laptop versorgen, nicht aus der Maschine: Laptop am Akku, damit keine
Masseschleife über das Netzteil entsteht.

### 2.3 Mitschneiden

Seriellen Monitor mit **921600 Baud** öffnen (oder `pio device monitor -b 921600`,
`screen /dev/ttyUSB0 921600`, auf Windows PuTTY). Dann:

1. `scan` eingeben. Das zeigt je Kanal Ruhepegel, kürzesten Puls und die
   nächstliegende Baudrate. Erwartung: 9600 invertiert (B) oder 115200 normal (A).
2. `b 9600` bzw. `b 115200`, bei Bedarf `i` (invertiert). Die Zeilen müssen
   nun regelmäßig und gleich lang kommen. Wenn nicht, `p 8E1` probieren.
3. Log in eine Datei laufen lassen. Bei jeder Aktion am Display eine Markierung
   setzen, **vorher**:

| Markierung (`m ...`) | Aktion |
|---|---|
| `m ruhe` | 30 s nichts tun |
| `m bruehtemp 93.0 -> 93.5` | Brühtemperatur einen Schritt hoch |
| `m bruehtemp 93.5 -> 93.0` | und wieder runter |
| `m dampf 125 -> 126` | Dampftemperatur ändern |
| `m hebel hoch` / `m hebel runter` | Bezug starten/stoppen (Chrono) |
| `m standby an` / `m standby aus` | Standby |
| `m kalt eingeschaltet` | Mitschnitt schon *vor* dem Einschalten starten: Bootsequenz |

### 2.4 Auswerten

[`tools/duo_sniff.py`](tools/duo_sniff.py), nur Python-Standardbibliothek:

```bash
python3 tools/duo_sniff.py stats mitschnitt.log   # ASCII oder binär? Welche Prüfsumme passt?
python3 tools/duo_sniff.py gicar mitschnitt.log   # falls Protokoll A: jede Registeränderung mit Adresse
python3 tools/duo_sniff.py diff  mitschnitt.log --dir B   # falls binär: welche Bytes sich wann ändern
```

`stats` probiert zehn Prüfsummenverfahren durch (Summe mod 256/128, XOR,
Zweierkomplement, CRC-16/Modbus, Gicar-ASCII) und zeigt, welches bei den
Frames passt. `gicar` und `diff` geben die Markierungen aus dem Log mit aus,
so sieht man direkt, welche Adresse oder welches Byte nach `m bruehtemp ...`
umspringt.

Tests: `python3 -m unittest tests.test_bezzera_duo_sniff` (im Repo-Wurzelverzeichnis).

## 3. Danach: Schreiben

Erst wenn Richtung, Prüfsumme und die Adressen für Temperaturen feststehen:

- **Protokoll A:** Ein zweiter ESP32-UART *mit* TX hängt sich parallel an die
  Leitung. Er sendet nur in den Pausen zwischen den Abfragen des Displays
  einen einzelnen `w`-Befehl. Danach sofort per `r` zurücklesen, genau so
  macht es gicar-serial. Nicht dauerhaft pollen, das Display ist Master.
- **Protokoll B:** Man-in-the-Middle. Leitung auftrennen, der ESP32 empfängt
  auf einer Seite und gibt auf der anderen weiter („MCU passthrough“), beim
  Weitergeben werden einzelne Bytes ersetzt. Aus dem Lelit-Projekt: Die
  Platine fällt **nicht** in einen sicheren Zustand, wenn keine Pakete mehr
  kommen. Vor dem Abschalten des ESP32 also ein Paket mit allen Relais aus
  senden.

Grenzen (89–96 °C, 100–130 °C) nie über das hinaus schreiben, was das Display
selbst anbietet. gicar-serial meldet, dass die Ascaso bei Werten außerhalb
ihres Bereichs in einen Fehler geht und Einstellungen zurücksetzt.

## Quellen

- [antondlr/gicar-serial](https://github.com/antondlr/gicar-serial): Gicar-ASCII-Protokoll, ESPHome-Komponente
- [magnusnordlander/lelit-bianca-protocol](https://github.com/magnusnordlander/lelit-bianca-protocol): Gicar-Binärprotokoll Display ↔ Platine
- [magnusnordlander/smart-lcc](https://github.com/magnusnordlander/smart-lcc): Ersatz-Display auf dieser Basis (Vorbild für einen Passthrough)
- [Whole Latte Love: Matrix MN](https://www.wholelattelove.com/products/bezzera-matrix-mn-dual-boiler-espresso-machine): „Gicar PID controller“
- [1st-line: Matrix/Duo Software-Kompatibilität](https://www.1st-line.com/technical-support/bezzera-technical-support/bezzera-matrix-duo-software-compatibility-changes/): Versionspaare Mainboard/Display
- [espresso.co.nz: Display V2.2 5963201.01R](https://www.espresso.co.nz/parts/display-assembly-with-integrated-touchscreen-v2-2-duo-matrix-bezzera-5963201-01r/) und [V1.1 5963202R](https://www.espresso.co.nz/parts-care/display-assembly-with-integrated-touchscreen-v1-1-duo-matrix-bezzera-5963202r/)
