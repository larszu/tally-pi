#!/usr/bin/env python3
"""
Ein Numato-USB-GPIO-Board ansprechen — auf Linux, macOS und Windows gleich.

─── WOZU DIESE DATEI ───────────────────────────────────────────────────────

Auf dem Raspberry Pi sind die GPIO-Leitungen der 40-polige Stecker, angesprochen
ueber libgpiod. Ein gewoehnlicher Mac oder Windows-Rechner hat diesen Stecker
nicht — wohl aber einen USB-Anschluss, und daran ein Numato-USB-GPIO-Board:
32 (oder 8/16/64) Kanaele, die sich als Ein- ODER Ausgang schalten lassen. So
laufen dieselben Funktionen — Taster lesen, Tally-Lampen setzen — auch dort,
wo es keinen Pi-Stecker gibt.

Diese Datei ist NUR der Draht zum Board: Geraet finden, oeffnen, die schlichten
Textbefehle sprechen. Was daraus eine Tally-Anlage macht (Kanaele als Ein-/
Ausgang belegen, Flanken zu ATEM-Befehlen, Lampen setzen), steht in
`numato_watcher.py`. Zwei Schichten, damit die untere ohne Board pruefbar ist:
`NumatoBoard` spricht mit einem beliebigen serienschnittstellen-aehnlichen
Objekt, ein Test schiebt ihm ein nachgebautes unter.

─── DER BEFEHLSSATZ (Numato) ───────────────────────────────────────────────

Menschenlesbare Zeilen, mit `\\r` abgeschlossen, Antwort endet auf `>`:

    ver                     Firmware-Version — dient als Lebenszeichen/Probe.
    gpio readall            alle Kanaele auf einmal, als Hex (LSB = Kanal 0).
    gpio writeall <hex>     alle Ausgaenge auf einmal (achtet auf iomask).
    gpio iodir <hex>        Richtung je Bit: 0 = Ausgang, 1 = Eingang.
    gpio iomask <hex>       welche Bits `writeall`/`iodir` ueberhaupt anfassen.
    gpio set <k>            einen Ausgang auf HIGH.  k = 0..9, A.. (ein Zeichen)
    gpio clear <k>          einen Ausgang auf LOW.
    adc read <k>            Analogwert eines Kanals (nicht ueberall vorhanden).

Die Hex-Breite haengt an der Kanalzahl: 32 Kanaele -> 8 Hex-Zeichen. Sie wird
aus der ersten `readall`-Antwort abgeleitet, statt sie zu raten — ein 8- oder
16-Kanal-Board antwortet kuerzer, und ein festgenagelter Wert waere dort falsch.

─── pyserial: da oder nicht da, und das wird GESAGT ────────────────────────

Wie im uebrigen Repo bricht ein fehlendes pyserial hier NICHT beim Import ab —
sonst waere das Modul auf einem Rechner ohne pyserial auch fuer einen Test
unerreichbar. `PYSERIAL_GRUND` traegt den Grund; `finde_geraete`/`oeffne`
melden ihn dann statt still nichts zu finden.
"""
import glob
import re
import sys
import time
from pathlib import Path

try:
    import serial
    from serial.tools import list_ports
    PYSERIAL_DA = True
    PYSERIAL_GRUND = ""
except Exception as _e:  # ImportError, auf Fremdsystemen auch OSError
    serial = None
    list_ports = None
    PYSERIAL_DA = False
    PYSERIAL_GRUND = f"pyserial nicht verfuegbar: {_e}"

#: Numato-Boards melden sich als USB-CDC-ACM. 19200 Baud ist der Wert, mit dem
#: dieses Repo die Boards am Pi seit jeher anspricht — beibehalten, damit ein
#: bestehendes Board unveraendert antwortet.
BAUD = 19200

#: Numato Lab USB Vendor-ID (0x2A19). Passt sie, ist es mit grosser Sicherheit
#: ein Numato — dann muss nicht jeder serielle Anschluss angesprocht werden.
#: Fehlt sie (aeltere Boards melden eine generische ID), wird trotzdem geprobt.
NUMATO_VID = 0x2A19

#: Feste Linux-Symlinks, die `99-numato.rules` anlegt. Zuerst nachsehen.
BEVORZUGTE_PFADE = ("/dev/numato0", "/dev/numato1")


def _kanal_zeichen(kanal: int) -> str:
    """Ein Kanal als EIN Zeichen fuer `gpio set/clear/read`: 0..9, dann A..

    Numato zaehlt oberhalb von 9 mit Buchstaben weiter (10 -> 'A', 31 -> 'V').
    """
    if kanal < 0 or kanal > 31:
        raise ValueError(f"Kanal {kanal} ausserhalb 0..31")
    if kanal < 10:
        return str(kanal)
    return chr(ord("A") + kanal - 10)


def finde_geraete():
    """Wahrscheinliche Numato-Anschluesse, beste zuerst — als Liste von Namen.

    Reihenfolge: die festen Linux-Symlinks, dann Anschluesse mit Numatos
    Vendor-ID, dann alle uebrigen seriellen Anschluesse (ein aelteres Board
    ohne kenntliche ID faellt sonst durch). Doppelte fliegen raus, die
    Reihenfolge bleibt.
    """
    namen = []

    def dazu(n):
        if n and n not in namen:
            namen.append(n)

    for p in BEVORZUGTE_PFADE:
        if Path(p).exists():
            dazu(p)

    if list_ports is not None:
        anschluesse = list(list_ports.comports())
        for info in anschluesse:
            if getattr(info, "vid", None) == NUMATO_VID:
                dazu(info.device)
        for info in anschluesse:
            dazu(info.device)

    # Linux-Rueckfall, falls list_ports nichts kennt: die ttyACM-Knoten, an
    # denen CDC-ACM-Geraete (wie Numato) auftauchen.
    if sys.platform.startswith("linux"):
        for c in sorted(glob.glob("/dev/ttyACM*")):
            dazu(c)

    return namen


class NumatoBoard:
    """Ein geoeffnetes Board. Alle Befehle laufen ueber `_cmd`.

    Kein Zustand ausser der offenen Schnittstelle und der einmal ermittelten
    Kanalbreite. Wer mehrere Befehle hintereinander schickt (Poll-Schleife und
    Ausgabe-Kommandos), serialisiert das aussen — ein serieller Anschluss
    vertraegt nur einen Sprecher zur Zeit (siehe `numato_watcher.py`).
    """

    def __init__(self, ser):
        self._ser = ser
        self.breite = None  # Zahl der Kanaele, aus der ersten readall-Antwort

    # ── Draht ──────────────────────────────────────────────────────────────
    def _cmd(self, zeile: str, warten: float = 0.05) -> str:
        """Eine Befehlszeile schicken, die Nutzlast zurueck (ohne Echo/Prompt)."""
        self._ser.reset_input_buffer()
        self._ser.write((zeile + "\r").encode())
        time.sleep(warten)
        roh = self._ser.read(512).decode(errors="replace")
        # Numato spiegelt die Befehlszeile und haengt ein '>' als Prompt an.
        return roh.replace(zeile, "").replace(">", "").strip("\r\n >")

    # ── Lebenszeichen ────────────────────────────────────────────────────────
    def ver(self, warten: float = 0.2) -> str:
        return self._cmd("ver", warten=warten)

    # ── Lesen ────────────────────────────────────────────────────────────────
    def read_all(self):
        """Alle Kanaele als Bitmaske (Kanal 0 = Bit 0), oder None.

        Setzt beim ersten Erfolg `self.breite` aus der Laenge der Hex-Antwort.
        """
        out = self._cmd("gpio readall")
        m = re.search(r"[0-9a-fA-F]+", out)
        if not m:
            return None
        hexziffern = m.group(0)
        self.breite = len(hexziffern) * 4
        return int(hexziffern, 16)

    def read_adc(self, kanal: int):
        out = self._cmd(f"adc read {kanal}")
        m = re.search(r"\d+", out)
        return int(m.group(0)) if m else None

    # ── Schreiben ─────────────────────────────────────────────────────────────
    def _hexbreite(self) -> int:
        """Zahl der Hex-Zeichen fuer writeall/iodir/iomask (Kanalbreite/4)."""
        bits = self.breite or 32
        return max(1, (bits + 3) // 4)

    def _hex(self, wert: int) -> str:
        return format(wert & ((1 << (self._hexbreite() * 4)) - 1),
                      "0{}x".format(self._hexbreite()))

    def set_channel(self, kanal: int) -> None:
        self._cmd(f"gpio set {_kanal_zeichen(kanal)}")

    def clear_channel(self, kanal: int) -> None:
        self._cmd(f"gpio clear {_kanal_zeichen(kanal)}")

    def write_all(self, maske: int) -> None:
        self._cmd(f"gpio writeall {self._hex(maske)}")

    def set_iomask(self, maske: int) -> None:
        self._cmd(f"gpio iomask {self._hex(maske)}")

    def set_iodir(self, maske: int) -> None:
        """Richtung je Bit: 0 = Ausgang, 1 = Eingang (achtet auf iomask)."""
        self._cmd(f"gpio iodir {self._hex(maske)}")


def probe(name: str, warten: float = 0.15) -> bool:
    """Antwortet der Anschluss auf `ver`? Dann ist es plausibel ein Numato."""
    if not PYSERIAL_DA:
        return False
    try:
        with serial.Serial(name, BAUD, timeout=0.3) as s:
            s.reset_input_buffer()
            s.write(b"ver\r")
            time.sleep(warten)
            return len(s.read(128)) > 0
    except Exception:
        return False


def oeffne(name: str):
    """Ein `NumatoBoard` am genannten Anschluss — oder RuntimeError mit Grund."""
    if not PYSERIAL_DA:
        raise RuntimeError(PYSERIAL_GRUND)
    ser = serial.Serial(name, BAUD, timeout=0.3)
    return NumatoBoard(ser)
