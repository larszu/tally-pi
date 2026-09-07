"""Waechter fuer die Oberflaechen-Regeln (ADR-007 der av-planner-suite).

Nutzer-Rueckmeldung 2026-09-07: "die ui ist nicht konsistent. lege globale ui
regeln fest die fuer alle repos gelten."

WARUM DIE WERTE HIER EIN ZWEITES MAL STEHEN: sie stehen maschinenlesbar in
`@avplan/ui` (`src/brand.ts`), aber dieses Repo ist Python und haengt an
keinem npm-Paket. Ohne diesen Test waere der Rueckweg in die alte
Slate-Blau-Welt eine Zeile, die niemandem auffaellt.

WAS ER NICHT PRUEFT: die Vollbild-Tallyanzeige in `guide_server.py`. Die ist
kein Chrome, sondern eine Lampe — ihre Toene sind dort mit Begruendung
gewaehlt (Erkennbarkeit aus Entfernung, Unterscheidbarkeit von "offline"),
und eine Regel fuer Planungswerkzeuge ist kein Grund, an einem Signalgeber zu
drehen.
"""
import re
import unittest
from pathlib import Path

SEITE = Path(__file__).resolve().parent.parent / "setup-guide.html"
CSS = SEITE.read_text(encoding="utf-8")


def token(name: str) -> str:
    treffer = re.search(rf"{name}:\s*([^;]+);", CSS)
    return treffer.group(1).strip() if treffer else ""


class MarkenPalette(unittest.TestCase):
    def test_grund_und_flaeche(self):
        self.assertEqual(token("--bg"), "#132040", "Grund ist Deep Navy")
        self.assertEqual(token("--surface-2"), "#1D324F", "Flaeche ist Zumpe Navy")

    def test_text(self):
        self.assertEqual(token("--fg"), "#E1ECEF", "Fliesstext ist Eisblau")
        self.assertEqual(token("--fg-strong"), "#F6F5F0", "Ueberschrift ist Off-White")
        self.assertEqual(token("--fg-muted"), "#8C9CB3", "Gedaempft ist Stahlblau")

    def test_akzent_ist_die_aktionsflaeche(self):
        self.assertEqual(token("--accent"), "#F6F5F0")
        self.assertEqual(token("--accent-text"), "#132040", "darauf steht Navy")

    def test_status_ist_nicht_signal(self):
        self.assertEqual(token("--ok"), "#2F7D5C")
        self.assertEqual(token("--warn"), "#C8892B")
        self.assertEqual(token("--err"), "#B04A3F")
        self.assertEqual(token("--signal"), "#D6402E")
        self.assertNotEqual(token("--err"), token("--signal"), "zwei Toene, zwei Zwecke")


class Form(unittest.TestCase):
    def test_keine_rundungen(self):
        for name in ("--r-sm", "--r-md", "--r-lg", "--r-xl", "--r-pill"):
            self.assertEqual(token(name), "0", f"{name} ist nicht null")

    def test_kein_harter_radius(self):
        rest = re.findall(r"border-radius:\s*(50%|[1-9][^;\"]*)", CSS)
        self.assertEqual(rest, [], f"harte Radien: {rest}")

    def test_keine_schatten(self):
        for name in ("--shadow-sm", "--shadow-md", "--shadow-lg"):
            self.assertEqual(token(name), "none", f"{name} traegt noch einen Schatten")

    def test_keine_verlaeufe(self):
        """Genau eine Ausnahme, und die ist keine Dekoration.

        Der Pfeil des Auswahlfelds wird aus zwei 45-Grad-Kanten GEZEICHNET
        (`--chevron`). Ohne ihn hat das Feld keinen Pfeil. Ein Test, der
        pauschal jeden Verlauf verbietet, haette entweder den Pfeil gekostet
        oder waere abgeschaltet worden — beides schlechter als die benannte
        Ausnahme.
        """
        self.assertNotIn("radial-gradient", CSS)
        zeilen = [z.strip() for z in CSS.split("\n") if "linear-gradient" in z]
        self.assertEqual(len(zeilen), 2, f"unerwartete Verlaeufe: {zeilen}")
        for z in zeilen:
            self.assertIn("transparent 50%", z, f"kein Chevron: {z}")


class Bewegung(unittest.TestCase):
    def test_kurve_und_dauer(self):
        self.assertIn("cubic-bezier(.2,.8,.2,1)", token("--tx"))
        dauer = int(re.match(r"(\d+)ms", token("--tx")).group(1))
        self.assertLessEqual(dauer, 450, "nichts laenger als 450 ms")


if __name__ == "__main__":
    unittest.main()
