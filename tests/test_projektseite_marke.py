"""
Auch die PROJEKTSEITE traegt die Marke — nicht nur das Werkzeug.

`tests/test_brand_tokens.py` haelt die Palette in `setup-guide.html` fest.
Die oeffentliche Projektseite (`scripts/build-site.py`) ist eine zweite
Oberflaeche mit eigenem `<style>` — und sie stand bis 2026-09-16 noch in der
alten Slate-Blau-Welt (`#0f1419`, `#4ea1ff`, runde Ecken). Ohne diesen Test
waere der Rueckweg dorthin eine Zeile, die niemandem auffaellt.

Geprueft wird die VORLAGE im Quelltext des Bauskripts, nicht die gebaute
Seite — so braucht der Test kein `markdown` und kein Rendern.
"""
import re
import unittest
from pathlib import Path

QUELLE = (Path(__file__).resolve().parent.parent
          / "scripts" / "build-site.py").read_text(encoding="utf-8")


class MarkenPalette(unittest.TestCase):
    def test_marke_ist_da(self):
        for wert, wofuer in (
            ("#132040", "Deep Navy als Grund"),
            ("#1D324F", "Zumpe Navy als Flaeche/Kopf"),
            ("#E1ECEF", "Eisblau als Fliesstext"),
            ("#F6F5F0", "Off-White als Ueberschrift/Akzent"),
            ("#8C9CB3", "Stahlblau als Meta"),
        ):
            self.assertIn(wert, QUELLE, f"{wert} fehlt ({wofuer})")

    def test_die_alte_slate_welt_ist_weg(self):
        for alt in ("#0f1419", "#171d24", "#2a323c", "#e6edf3",
                    "#9aa7b4", "#4ea1ff"):
            self.assertNotIn(alt, QUELLE,
                             f"alter Slate-Blau-Wert {alt} wieder da")


class Form(unittest.TestCase):
    def test_keine_runden_ecken(self):
        # Jede border-radius-Angabe muss 0 sein — die Marke ist kantig.
        rest = re.findall(r"border-radius:\s*([^;}}]+)", QUELLE)
        harte = [r.strip() for r in rest if r.strip() not in ("0", "0px")]
        self.assertEqual(harte, [], f"runde Ecken auf der Projektseite: {harte}")


if __name__ == "__main__":
    unittest.main()
