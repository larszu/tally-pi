"""
Zeigt die Projektseite die Anwendung — oder nur eine Seite, die so aussieht?

─── DIE MELDUNG DAHINTER (Nutzer, 2026-09-12) ──────────────────────────────

„Die GitHub page zeigt quasi nur das readme. Das ist unnötig. Starte dort
die gesamte Anwendung"

─── WAS HIER GEPRUEFT WIRD UND WARUM GERADE DAS ────────────────────────────

Die Vorfuehrung unter `/demo/` hat eine Eigenschaft, die sie gefaehrlich
macht: sie sieht aus wie das Werkzeug, mit dem jemand Kameras schaltet. Zwei
Dinge muessen deshalb halten, und beide sind hier festgenagelt:

  1. SIE RECHNET NICHT SELBST. Die Frage „ist diese Kamera rot?" beantwortet
     `guide_server.tally_state_for_device` — eine Funktion mit eigenen Tests,
     an der eine Kamera-Umschaltung haengt. In der Vorfuehrung steht ihre
     ANTWORT, nicht eine zweite Fassung davon. Der Test hier rechnet die
     Stichprobe noch einmal mit der echten Funktion nach und vergleicht.

  2. SIE GIBT SICH ZU ERKENNEN. Auf jeder Seite, ohne Ausnahme, steht das
     Kopfband mit „kein Pi, kein Mischer". Eine Tally-Seite, die gruen
     leuchtet und dabei verschweigt, dass sie niemanden fragt, ist genau die
     Verwechslung, gegen die `B-86` die echte Seite abgedichtet hat.

Dazu die Zusicherung, die die Vorfuehrung am Leben haelt: was die Seiten
abfragen, muss die Vorfuehrung beantworten. Faellt das auseinander, bleibt
auf der Projektseite ein Feld leer — und zwar still.
"""

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from hilfe import temp_verzeichnis  # noqa: E402


def baue(ziel: Path):
    """Die Vorfuehrung wirklich bauen — der Test fragt das Ergebnis, nicht den Quelltext."""
    raus = subprocess.run([sys.executable, str(ROOT / "scripts" / "build-demo.py"), str(ziel)],
                          cwd=str(ROOT), capture_output=True, text=True, timeout=180)
    if raus.returncode != 0:
        raise AssertionError(f"build-demo.py scheiterte:\n{raus.stdout}\n{raus.stderr}")
    return json.loads((ziel / "daten.json").read_text(encoding="utf-8"))


class DieVorfuehrungWirdGebaut(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = temp_verzeichnis()
        cls.ziel = Path(cls._tmp.__enter__()) / "demo"
        cls.daten = baue(cls.ziel)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.__exit__(None, None, None)

    def test_alle_seiten_der_anwendung_sind_da(self):
        # „die gesamte Anwendung" — nicht nur die Setup-Oberflaeche.
        erwartet = [
            "index.html",                 # Setup-Oberflaeche
            "tally/cam1/index.html",      # Browser-Tally je Geraet
            "tally/cam2/index.html",
            "tally/cam3/index.html",
            "cue/index.html",             # Cue-Anzeige (Buehne)
            "cue/control/index.html",     # Cue-Regie
            "daten.json", "demo.js", "demo.css",
        ]
        fehlt = [p for p in erwartet if not (self.ziel / p).exists()]
        self.assertEqual(fehlt, [], f"nicht gebaut: {fehlt}")

    def test_jede_seite_sagt_dass_sie_eine_vorfuehrung_ist(self):
        # Ohne Ausnahme. Eine Tally-Seite ohne diesen Hinweis ist eine
        # Farbflaeche, die eine Aussage ueber ein Studio zu machen scheint.
        for p in sorted(self.ziel.rglob("*.html")):
            with self.subTest(seite=p.relative_to(self.ziel).as_posix()):
                text = p.read_text(encoding="utf-8")
                self.assertIn("demo.js", text, "kein Nachschlagewerk eingehaengt")
                self.assertIn("demo.css", text, "kein Kopfband-Stil eingehaengt")
                self.assertIn("__demoWurzel", text)

    def test_jede_seite_findet_ihr_nachschlagewerk(self):
        # Der Fehler, den dieser Test gefunden hat (2026-09-12): die
        # eingehaengte Tiefe war um eins zu gross. Im Wurzelverzeichnis
        # ausprobiert faellt das NICHT auf — ein Browser klemmt `../` an der
        # Wurzel ab und findet die Datei trotzdem. Auf der Projektseite liegt
        # die Vorfuehrung aber unter `/demo/`, und dort zeigte der Pfad
        # daneben: keine Antworten, kein Kopfband, eine tote Seite.
        for seite in sorted(self.ziel.rglob("*.html")):
            rel = seite.relative_to(self.ziel)
            with self.subTest(seite=rel.as_posix()):
                text = seite.read_text(encoding="utf-8")
                hoch = text.split('window.__demoWurzel = "', 1)[1].split('"', 1)[0]
                gezaehlt = (seite.parent / hoch).resolve()
                self.assertEqual(gezaehlt, self.ziel.resolve(),
                                 f"`{hoch}` fuehrt von {rel.as_posix()} nach "
                                 f"{gezaehlt}, nicht zur Vorfuehrung")
                for datei in ("demo.js", "demo.css", "daten.json"):
                    self.assertTrue((gezaehlt / datei).exists(),
                                    f"{datei} liegt nicht dort, wo {rel.as_posix()} sie sucht")

    def test_die_oberflaeche_ist_die_echte_datei(self):
        # Keine Abschrift: was auf der Projektseite liegt, ist Zeichen fuer
        # Zeichen die Datei, die auch auf dem Pi liegt — plus dem Einhang.
        echt = (ROOT / "setup-guide.html").read_text(encoding="utf-8")
        gebaut = (self.ziel / "index.html").read_text(encoding="utf-8")
        kern = echt.split("</head>", 1)[1]
        self.assertIn(kern, gebaut,
                      "die ausgelieferte Oberflaeche weicht von setup-guide.html ab")

    def test_die_zustaende_kommen_von_der_echten_funktion(self):
        # Die eigentliche Zusicherung dieser Datei: nachgerechnet mit
        # `guide_server.tally_state_for_device`, verglichen mit dem, was in
        # der Vorfuehrung steht. Weicht auch nur ein Feld ab, ist irgendwo
        # eine zweite Fassung der Entscheidung entstanden.
        umgebung = dict(os.environ)
        umgebung["PI_GUIDE_CONF"] = str(self.ziel / "_pruef-conf")
        umgebung["PI_GUIDE_STATE"] = str(self.ziel / "_pruef-state")
        programm = (
            "import json, sys\n"
            "import guide_server as gs\n"
            "auftrag = json.load(sys.stdin)\n"
            "raus = []\n"
            "for fall in auftrag:\n"
            "    cfg = {'devices': [fall['geraet']]}\n"
            "    raus.append(gs.tally_state_for_device(\n"
            "        cfg, fall['geraet']['id'], atem_state=fall['atem']))\n"
            "print(json.dumps(raus))\n"
        )
        faelle, erwartet = [], []
        for lage in self.daten["lagen"]:
            atem = lage["antworten"]["/atem"]
            # Eine Stichprobe quer durch die Tabelle, und dazu die drei
            # Geraete der Beispiel-Anlage.
            proben = list(lage["zustaende"].items())[::37]
            for schluessel, zustand in proben:
                inp, me, aux = schluessel.split("|")
                faelle.append({
                    "atem": atem,
                    "geraet": {"id": "x", "input": int(inp), "me": int(me),
                               "aux": [int(a) for a in aux.split(",") if a]},
                })
                erwartet.append(zustand)
            for d in lage["antworten"]["/tally-diagnostics"]["devices"]:
                konf = next(k for k in lage["antworten"]["/tally-config"]["devices"]
                            if k["id"] == d["id"])
                faelle.append({"atem": atem, "geraet": dict(konf, id="x")})
                erwartet.append(d["state"])

        raus = subprocess.run([sys.executable, "-c", programm], input=json.dumps(faelle),
                              cwd=str(ROOT), env=umgebung, capture_output=True,
                              text=True, timeout=120)
        self.assertEqual(raus.returncode, 0, raus.stderr[-600:])
        self.assertEqual(json.loads(raus.stdout), erwartet,
                         "die Vorfuehrung zeigt einen anderen Tally-Zustand als "
                         "guide_server.tally_state_for_device berechnet")
        self.assertGreater(len(faelle), 20, "die Stichprobe ist zu klein")

    def test_die_aux_regel_ist_wirklich_vorgefuehrt(self):
        # Nicht Kosmetik: die Lage `pgm2` ist der Fall, an dem man sieht,
        # warum die Entscheidung im Server steht — Kamera 3 ist ROT, obwohl
        # sie weder auf PGM noch auf PVW liegt, weil AUX 1 sie fuehrt.
        lagen = {l["id"]: l for l in self.daten["lagen"]}
        zust = {d["id"]: d["state"]
                for d in lagen["pgm2"]["antworten"]["/tally-diagnostics"]["devices"]}
        self.assertEqual(zust["cam3"], "pgm",
                         "die Aux-Regel wird nicht mehr vorgefuehrt")
        zust1 = {d["id"]: d["state"]
                 for d in lagen["pgm1"]["antworten"]["/tally-diagnostics"]["devices"]}
        self.assertEqual([zust1["cam1"], zust1["cam2"], zust1["cam3"]],
                         ["pgm", "pvw", "safe"])
        zust0 = {d["id"]: d["state"]
                 for d in lagen["offline"]["antworten"]["/tally-diagnostics"]["devices"]}
        self.assertEqual(set(zust0.values()), {"offline"},
                         "ohne Mischer darf nichts nach `safe` aussehen")

    def test_nichts_behauptet_eine_verbindung_die_es_nicht_gibt(self):
        for lage in self.daten["lagen"]:
            with self.subTest(lage=lage["id"]):
                atem = lage["antworten"]["/atem"]
                self.assertTrue(atem.get("demo"),
                                "die Lage gibt sich nicht als Beispiel zu erkennen")
        # Und die Maschine, die es nicht gibt, hat auch keine Adresse.
        for iface in self.daten["ipconfig"]["interfaces"]:
            self.assertEqual(iface.get("addresses"), [],
                             "die Vorfuehrung nennt eine Netzadresse")

    def test_die_cue_tabelle_stammt_von_cue_view(self):
        tab = self.daten["cue_zustaende"]
        umgebung = dict(os.environ)
        umgebung["PI_GUIDE_CONF"] = str(self.ziel / "_pruef-conf")
        umgebung["PI_GUIDE_STATE"] = str(self.ziel / "_pruef-state")
        raus = subprocess.run(
            [sys.executable, "-c",
             "import json, guide_server as gs\n"
             "jetzt = 1000000.0\n"
             "print(json.dumps([gs.cue_view({'text':'x','kind':'info','at':jetzt-a,\n"
             "    'ttl_s':gs.CUE_DEFAULT_TTL_S}, jetzt)['state']\n"
             "    for a in range(int(gs.CUE_DEFAULT_TTL_S)+6)]))\n"],
            cwd=str(ROOT), env=umgebung, capture_output=True, text=True, timeout=60)
        self.assertEqual(raus.returncode, 0, raus.stderr[-400:])
        self.assertEqual(tab, json.loads(raus.stdout))


class DerWaechterHaeltDieVorfuehrungAktuell(unittest.TestCase):
    """Ein neuer Weg in der Oberflaeche darf nicht still ins Leere laufen."""

    def test_ein_unbedienter_weg_laesst_den_bau_fallen(self):
        quelle = ROOT / "setup-guide.html"
        vorher = quelle.read_text(encoding="utf-8")
        veraendert = vorher.replace("fetch('/atem'", "fetch('/erfundener-weg'", 1)
        self.assertNotEqual(vorher, veraendert, "der Anker im Test stimmt nicht mehr")
        with temp_verzeichnis() as tmp:
            try:
                quelle.write_text(veraendert, encoding="utf-8")
                raus = subprocess.run(
                    [sys.executable, str(ROOT / "scripts" / "build-demo.py"),
                     str(Path(tmp) / "demo")],
                    cwd=str(ROOT), capture_output=True, text=True, timeout=180)
            finally:
                quelle.write_text(vorher, encoding="utf-8")
        self.assertNotEqual(raus.returncode, 0,
                            "der Bau laeuft durch, obwohl die Vorfuehrung einen "
                            "abgefragten Weg nicht beantwortet")
        self.assertIn("/erfundener-weg", raus.stdout + raus.stderr)


class DieNahtFuerDieAdresse(unittest.TestCase):
    """Ohne sie zeigen QR-Codes und Links der Vorfuehrung ins Leere."""

    def test_baseurl_fragt_zuerst_die_vorfuehrung(self):
        # Die Projektseite liegt unter einem Unterpfad (`/tally-pi/demo/`).
        # `location.origin` waere dort der Wurzelpfad von github.io — jeder
        # QR-Code zeigte auf eine Seite, die es nicht gibt.
        text = (ROOT / "setup-guide.html").read_text(encoding="utf-8")
        self.assertIn("window.__tallyBase", text,
                      "die Naht in baseUrl() ist weg — die Vorfuehrung verlinkt ins Leere")
        self.assertIn("window.__tallyBase", (ROOT / "web" / "demo.js").read_text(encoding="utf-8"))

    def test_auf_dem_pi_aendert_sich_nichts(self):
        # Die Naht darf nur greifen, wenn die Vorfuehrung sie setzt.
        text = (ROOT / "setup-guide.html").read_text(encoding="utf-8")
        stelle = text[text.index("function baseUrl()"):]
        stelle = stelle[:stelle.index("}")]
        self.assertIn("if (window.__tallyBase) return window.__tallyBase;", stelle)
        self.assertIn("location.origin", text[text.index("function baseUrl()"):][:900],
                      "der alte Weg ist weg — auf dem Pi zeigte dann nichts mehr")


if __name__ == "__main__":
    unittest.main()
