"""
Kleinkram, den mehr als ein Test braucht.

Genau eine Sache steht hier, und sie steht hier wegen Windows: ein
temporaeres Verzeichnis, das sich auch dann aufraeumen laesst, wenn ein
gerade beendeter Kindprozess seine Zustandsdatei noch eine Zehntelsekunde
offen haelt. Unter POSIX ist das Loeschen einer offenen Datei erlaubt und
die Frage stellt sich nicht; unter Windows scheitert es mit „Zugriff
verweigert" — und dann waere ein gruener Test rot geworden, ohne dass am
geprueften Programm etwas falsch ist.

Das ist NICHT dasselbe wie einen Fehler wegzuwerfen: was der Test prueft,
steht im Test. Ob das Betriebssystem danach ein Verzeichnis sofort oder
erst beim naechsten Aufraeumen loswird, gehoert nicht dazu.
"""
import sys
import threading
import time
from tempfile import TemporaryDirectory


def temp_verzeichnis(**kw):
    if sys.version_info >= (3, 10):
        kw.setdefault("ignore_cleanup_errors", True)
    return TemporaryDirectory(**kw)


class NumatoFake:
    """Ein nachgebautes Numato-Board hinter der pyserial-Schnittstelle.

    Genug davon, dass `numato_io.NumatoBoard` und `numato_watcher` es fuer
    echt halten: `ver`, `gpio readall/writeall/set/clear/iodir/iomask` und
    `adc read`. Es fuehrt einen Zustand von `breite` Kanaelen (Vorgabe 32) und
    antwortet im Numato-Format — Echo der Zeile, dann die Nutzlast, dann der
    `>`-Prompt.

    Eingaenge lassen sich von aussen schalten (`set_input`), damit ein Test
    einen Tastendruck nachstellen kann; die letzten `writeall`/`set`/`clear`
    stehen in `outputs` zum Nachpruefen.
    """

    def __init__(self, breite: int = 32, version: str = "00000008"):
        self.breite = breite
        self.version = version
        self._zustand = 0            # Bitmaske aller Kanaele
        self.iodir = (1 << breite) - 1   # alles Eingang bis gesetzt
        self.iomask = (1 << breite) - 1
        self.outputs = {}            # kanal -> bool, letzte gesetzte Ausgaenge
        self._eingang = threading.Lock()
        self._puffer = b""           # noch nicht gelesene Antwort

    # ── von aussen: Eingang schalten ────────────────────────────────────────
    def set_input(self, kanal: int, hoch: bool) -> None:
        with self._eingang:
            if hoch:
                self._zustand |= (1 << kanal)
            else:
                self._zustand &= ~(1 << kanal)

    def get_output(self, kanal: int) -> bool:
        return bool(self.outputs.get(kanal, False))

    # ── pyserial-Schnittstelle ───────────────────────────────────────────────
    def reset_input_buffer(self):
        self._puffer = b""

    def _hexbreite(self):
        return max(1, (self.breite + 3) // 4)

    def write(self, roh: bytes):
        zeile = roh.decode(errors="replace").strip("\r\n ")
        antwort = self._antwort(zeile)
        # Numato spiegelt die Zeile, dann die Nutzlast, dann den Prompt.
        self._puffer = (zeile + "\n" + antwort + "\n>").encode()

    def read(self, n: int = 512) -> bytes:
        out, self._puffer = self._puffer[:n], self._puffer[n:]
        return out

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    # ── die Befehle ──────────────────────────────────────────────────────────
    def _antwort(self, zeile: str) -> str:
        teile = zeile.split()
        if not teile:
            return ""
        if teile[0] == "ver":
            return self.version
        if teile[0] == "adc" and len(teile) >= 3 and teile[1] == "read":
            return "512"
        if teile[0] != "gpio" or len(teile) < 2:
            return ""
        unter = teile[1]
        with self._eingang:
            if unter == "readall":
                return format(self._zustand, "0{}x".format(self._hexbreite()))
            if unter == "writeall" and len(teile) >= 3:
                wert = int(teile[2], 16)
                # Nur maskierte Ausgangsbits uebernehmen.
                for k in range(self.breite):
                    if (self.iomask >> k) & 1 and not ((self.iodir >> k) & 1):
                        an = bool((wert >> k) & 1)
                        self.outputs[k] = an
                        if an:
                            self._zustand |= (1 << k)
                        else:
                            self._zustand &= ~(1 << k)
                return ""
            if unter in ("set", "clear") and len(teile) >= 3:
                kanal = _kanal_aus_zeichen(teile[2])
                an = (unter == "set")
                self.outputs[kanal] = an
                if an:
                    self._zustand |= (1 << kanal)
                else:
                    self._zustand &= ~(1 << kanal)
                return ""
            if unter == "iodir" and len(teile) >= 3:
                self.iodir = int(teile[2], 16)
                return ""
            if unter == "iomask" and len(teile) >= 3:
                self.iomask = int(teile[2], 16)
                return ""
        return ""


def _kanal_aus_zeichen(z: str) -> int:
    z = z.strip()
    if z.isdigit():
        return int(z)
    return 10 + (ord(z.upper()) - ord("A"))
