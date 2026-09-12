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
from tempfile import TemporaryDirectory


def temp_verzeichnis(**kw):
    if sys.version_info >= (3, 10):
        kw.setdefault("ignore_cleanup_errors", True)
    return TemporaryDirectory(**kw)
