"""Die Hardware-Doku existiert, ihre Schaltplaene sind da und wohlgeformt.

Ein README-Satz „siehe docs/hardware" ist keine Pruefung. Hier wird gefragt:
gibt es die Datei, zeigt sie auf Bilder, die es gibt, sind die Bilder gueltiges
SVG, und nennt das Wurzel-README sie. Mehr nicht — der Inhalt eines
Schaltplans laesst sich nicht testen, nur lesen.
"""
import re
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOKU = ROOT / "docs" / "hardware" / "README.md"


class HardwareDoku(unittest.TestCase):
    def test_doku_und_schaltplaene_da(self):
        self.assertTrue(DOKU.exists(), DOKU)
        text = DOKU.read_text(encoding="utf-8")
        bilder = set(re.findall(r"\]\(([^)]+\.svg)\)", text))
        self.assertGreaterEqual(len(bilder), 4)
        for b in bilder:
            pfad = DOKU.parent / b
            self.assertTrue(pfad.exists(), pfad)
            wurzel = ET.parse(pfad).getroot()
            self.assertTrue(wurzel.tag.endswith("svg"), pfad)
            # Jeder Plan hat einen Titel — die Seite baut daraus den alt-Text.
            self.assertTrue(any(k.tag.endswith("title") for k in wurzel), pfad)

    def test_wurzel_readme_verweist(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("docs/hardware/README.md", readme)

    def test_treiberregel_steht_in_der_doku(self):
        text = DOKU.read_text(encoding="utf-8")
        self.assertIn("open-drain", text)
        self.assertIn("push-pull", text)
        self.assertIn("4 mA", text)


if __name__ == "__main__":
    unittest.main()
