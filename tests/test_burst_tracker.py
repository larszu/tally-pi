"""Tests fuer den `BurstTracker` in `gpio_watcher.py`.

WARUM DIESE DATEI EXISTIERT (tally-pi#2):

  > Wenn nur pi-tally-out laeuft laeuft das Interface stabil.
  > Ueberlastet wenn Buttons dazu kommen?

Die Frage im Titel des Issues ist mit Ja zu beantworten, und die Ursache steht
im Tracker selbst. Seine eigene Dokumentation sagt, wofuer er da ist: eine
verrauschte Leitung — Lichtwellenleiter-Wandler, lange ungeschirmte Strippe —
liefert je Tastendruck „dozens" Flanken. Die erste Fassung legte fuer JEDE
dieser Flanken einen `threading.Timer` an, und ein Timer ist ein ganzer
Thread. Dutzende Thread-Erzeugungen je Druck, mal der Zahl der Taster: mit
reiner Tally-Ausgabe ist die Maschine ruhig, mit Tastern geht sie in die Knie.

Deshalb misst der erste Test nicht ein Verhalten, sondern eine RESSOURCE. Das
ist ungewoehnlich und hier richtig: das gemeldete Symptom IST der
Ressourcenverbrauch. Ein Test, der nur prueft, dass Druck und Freigabe je
einmal feuern, waere auch mit der alten Fassung gruen gewesen — sie war
funktional in Ordnung und trotzdem der Defekt.

Der zweite Grund fuer den Umbau ist eine Wettlaufsituation, die man im Betrieb
als „der Taster loest mitten im Druecken aus" sieht. Ein Timer, der bereits
abgelaufen ist und vor dem Lock wartet, laesst sich nicht mehr abbestellen —
`cancel()` ist dann wirkungslos, und die Freigabe feuert, obwohl gerade eine
neue Flanke kam. `test_frist_verschieben_verhindert_fruehe_freigabe` haelt
fest, dass eine verschobene Frist gewinnt.

Lauf: `python3 -m unittest discover -s tests -v`  (keine Abhaengigkeiten).
"""

import sys
import threading
import time
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# `gpio_watcher` importiert `gpiod`, das es nur auf dem Pi gibt. Der Tracker
# selbst braucht davon nichts — er bekommt seine Rueckfrage auf den Pegel als
# Callback herein. Ein Platzhalter-Modul genuegt, damit der Import durchlaeuft.
if "gpiod" not in sys.modules:
    fake = types.ModuleType("gpiod")
    line = types.ModuleType("gpiod.line")
    for name in ("Bias", "Direction", "Edge", "Value"):
        setattr(line, name, type(name, (), {}))
    fake.line = line
    sys.modules["gpiod"] = fake
    sys.modules["gpiod.line"] = line

from gpio_watcher import BurstTracker  # noqa: E402


class TrackerFall:
    """Ein Tracker samt Protokoll seiner Rueckrufe."""

    def __init__(self, **kw):
        self.presses = 0
        self.releases = []
        kw.setdefault("label", "T")
        kw.setdefault("release_ms", 40)
        self.tracker = BurstTracker(
            run_press=self._press,
            run_release=self._release,
            **kw,
        )

    def _press(self):
        self.presses += 1

    def _release(self, count):
        self.releases.append(count)

    def close(self):
        self.tracker.cancel()


class BurstTrackerFaeden(unittest.TestCase):
    """Der eigentliche Befund aus tally-pi#2."""

    def test_ein_tracker_kostet_genau_einen_faden(self):
        vorher = threading.active_count()
        fall = TrackerFall(release_ms=200)
        self.addCleanup(fall.close)
        self.assertEqual(threading.active_count(), vorher + 1)

    def test_eine_flut_von_flanken_erzeugt_keine_flut_von_threads(self):
        # Gezaehlt werden THREAD-ERZEUGUNGEN, nicht gleichzeitig laufende
        # Threads. Das ist der Unterschied, auf den es hier ankommt: die alte
        # Fassung bestellte jeden Timer sofort wieder ab, jeder Thread war
        # also kurzlebig — nur eben einer je Flanke. Wer den Hoechststand an
        # gleichzeitigen Threads misst, sieht davon fast nichts (in einer
        # engen Python-Schleife drei statt einem) und haelt den Defekt fuer
        # harmlos. Die Kosten stehen im Anlegen: Stack anfordern, Thread beim
        # Kernel registrieren, gleich wieder abbauen — zweihundert Mal je
        # Tastendruck, mal der Zahl der Taster, auf einem Pi.
        erzeugt = []
        echtes_start = threading.Thread.start

        def zaehlendes_start(self_thread, *a, **kw):
            erzeugt.append(self_thread.name)
            return echtes_start(self_thread, *a, **kw)

        threading.Thread.start = zaehlendes_start
        try:
            fall = TrackerFall(release_ms=200)
            self.addCleanup(fall.close)
            del erzeugt[:]        # der Wartefaden zaehlt nicht mit
            for _ in range(200):
                fall.tracker.on_edge("falling")
        finally:
            threading.Thread.start = echtes_start

        self.assertEqual(
            erzeugt, [],
            f"200 Flanken haben {len(erzeugt)} Thread(s) erzeugt — "
            "eine Flanke darf keinen eigenen Thread kosten",
        )

    def test_cancel_beendet_den_wartefaden(self):
        vorher = threading.active_count()
        fall = TrackerFall()
        fall.tracker.on_edge("falling")
        fall.close()
        for _ in range(100):
            if threading.active_count() == vorher:
                break
            time.sleep(0.01)
        self.assertEqual(threading.active_count(), vorher,
                         "der Wartefaden muss beim Abbestellen enden")


class BurstTrackerVerhalten(unittest.TestCase):
    """Was der Tracker leisten soll — unveraendert durch den Umbau."""

    def test_ein_druck_je_burst_und_eine_freigabe_mit_zaehler(self):
        fall = TrackerFall(release_ms=30)
        self.addCleanup(fall.close)
        for _ in range(12):
            fall.tracker.on_edge("falling")
            time.sleep(0.002)
        self.assertEqual(fall.presses, 1, "Druck feuert genau einmal je Burst")
        time.sleep(0.2)
        self.assertEqual(fall.releases, [12],
                         "Freigabe feuert einmal und meldet die Flankenzahl")

    def test_zu_kurzer_stoerimpuls_wird_verschluckt(self):
        # min_edges filtert Uebersprechen: ein oder zwei Flanken sind kein
        # Tastendruck. Wichtig ist, dass dann AUCH keine Freigabe kommt —
        # sonst schickte eine Stoerung eine Freigabe an den Mischer, ohne
        # dass je ein Druck da war.
        fall = TrackerFall(release_ms=30, min_edges=5)
        self.addCleanup(fall.close)
        fall.tracker.on_edge("falling")
        fall.tracker.on_edge("rising")
        time.sleep(0.2)
        self.assertEqual(fall.presses, 0)
        self.assertEqual(fall.releases, [])

    def test_frist_verschieben_verhindert_fruehe_freigabe(self):
        # Eine Luecke KNAPP unter der Freigabezeit darf den Burst nicht
        # beenden. Das ist der Fall, den die alte Timer-Fassung verlieren
        # konnte: der abgelaufene Timer wartete schon vor dem Lock, und
        # `cancel()` kam zu spaet.
        fall = TrackerFall(release_ms=80)
        self.addCleanup(fall.close)
        for _ in range(6):
            fall.tracker.on_edge("falling")
            time.sleep(0.05)
        self.assertEqual(fall.presses, 1)
        self.assertEqual(fall.releases, [],
                         "solange Flanken nachkommen, gibt es keine Freigabe")
        time.sleep(0.25)
        self.assertEqual(fall.releases, [6])

    def test_gehaltene_leitung_verschiebt_die_freigabe(self):
        # `is_at_idle() == False` heisst: die Leitung liegt noch auf dem
        # aktiven Pegel — eine stille Luecke im Burst, kein Loslassen.
        gehalten = {"wert": True}
        fall = TrackerFall(release_ms=30, is_at_idle=lambda: not gehalten["wert"])
        self.addCleanup(fall.close)
        fall.tracker.on_edge("falling")
        time.sleep(0.2)
        self.assertEqual(fall.releases, [], "gehaltene Leitung gibt nicht frei")
        gehalten["wert"] = False
        time.sleep(0.2)
        self.assertEqual(len(fall.releases), 1, "losgelassen wird freigegeben")

    def test_zwei_bursts_sind_zwei_druecke(self):
        fall = TrackerFall(release_ms=30)
        self.addCleanup(fall.close)
        for _ in range(4):
            fall.tracker.on_edge("falling")
        time.sleep(0.2)
        for _ in range(3):
            fall.tracker.on_edge("falling")
        time.sleep(0.2)
        self.assertEqual(fall.presses, 2)
        self.assertEqual(fall.releases, [4, 3])


if __name__ == "__main__":
    unittest.main()
