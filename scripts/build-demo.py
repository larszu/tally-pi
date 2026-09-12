#!/usr/bin/env python3
# ---------------------------------------------------------------------------
# Die Anwendung auf die Projektseite stellen — nicht ein Bild davon.
#
# ─── WAS GEMELDET WURDE (Nutzer, 2026-09-12) ───────────────────────────────
#
#   „Die GitHub page zeigt quasi nur das readme. Das ist unnötig. Starte
#    dort die gesamte Anwendung"
#
# ─── WAS GITHUB PAGES KANN UND WAS NICHT ───────────────────────────────────
#
# Pages liefert Dateien aus. Es fuehrt kein Python aus, es oeffnet keinen
# Port, es spricht kein UDP mit einem Mischer. `guide_server.py` kann dort
# nicht laufen — das ist keine Einstellung, die man umlegt.
#
# Was dort laufen kann, ist die OBERFLAECHE, und zwar die echte Datei
# `setup-guide.html`, Zeichen fuer Zeichen dieselbe, die auf dem Pi liegt.
# Ihr fehlt dann nur eines: der Server, der ihre Anfragen beantwortet
# (`/atem`, `/tally-config`, `/tally-diagnostics`, `/logs` …).
#
# ─── DIE EINE REGEL, DIE DIESES PROGRAMM EINHAELT ──────────────────────────
#
# KEINE ZWEITE FASSUNG EINER ENTSCHEIDUNG. Die Frage „ist diese Kamera rot?"
# beantwortet `guide_server.tally_state_for_device` — eine Funktion, die
# `tests/test_tally_state.py` prueft und an der eine Kamera-Umschaltung
# haengt. Sie in JavaScript nachzubauen, damit die Projektseite huebsch
# aussieht, waere genau der Fehler, vor dem dieses Repo an mehreren Stellen
# warnt: zwei Fassungen, von denen die zweite beim naechsten Feature nicht
# nachgezogen wird — und die falsche Farbe faellt im Zweifel auf einer Buehne
# auf.
#
# Deshalb RECHNET HIER PYTHON, beim Bauen, mit den echten Funktionen:
# `build_tally_diagnostics()`, `tally_state_for_device()`, `get_atem_state()`,
# `render_tally_page()` und die uebrigen werden aufgerufen, ihre Antworten
# landen als JSON neben der Seite, und im Browser liegt nur noch ein
# Nachschlagewerk davor (`web/demo.js`). Was die Projektseite zeigt, hat
# `guide_server.py` ausgerechnet.
#
# ─── WAS DIE VORFUEHRUNG IST UND WAS SIE NICHT IST ─────────────────────────
#
# Sie zeigt die Anwendung so, wie sie auf einem Rechner OHNE Pi-Hardware
# laeuft — also genau das, was `run-local.py` zeigt. Kein erfundener
# Mischer, keine erfundenen Pin-Pegel: wo Hardware fehlt, steht der Grund,
# den das Programm selbst nennt. Ein Kopfband sagt auf jeder Seite, dass hier
# nichts geschaltet wird.
#
# Drei Lagen sind vorberechnet, zwischen denen der Besucher umschaltet:
# PGM 1 / PVW 2, PGM 2 / PVW 3, und „Mischer nicht verbunden". Jede ist eine
# echte Rechnung des echten Programms ueber einen echten Zustand.
# ---------------------------------------------------------------------------
import json
import os
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path

WURZEL = Path(__file__).resolve().parent.parent
ZIEL = Path(sys.argv[1]) if len(sys.argv) > 1 else WURZEL / "_site" / "demo"
WEB = WURZEL / "web"

# Die Umgebung MUSS vor dem Import stehen: `paths.py` liest sie beim Laden,
# und ein spaeter gesetzter Wert traefe auf schon berechnete Pfade.
ARBEIT = Path(tempfile.mkdtemp(prefix="tally-demo-"))
os.environ["PI_GUIDE_CONF"] = str(ARBEIT / "conf")
os.environ["PI_GUIDE_STATE"] = str(ARBEIT / "state")
sys.path.insert(0, str(WURZEL))

import paths  # noqa: E402
import guide_server as gs  # noqa: E402

# ── Die Beispiel-Anlage ────────────────────────────────────────────────────
#
# Dieselben drei Kameras wie in `run-local.py --demo` und in den
# README-Bildern. Ein einziger Beispielbestand fuer alles, was dieses Repo
# vorzeigt — zwei verschiedene waeren zwei Dinge zum Nachziehen.
KONFIGURATION = {
    "atem_ip": "192.0.2.240",          # TEST-NET-1: sichtbar kein echtes Geraet
    "devices": [
        {"id": "cam1", "name": "Kamera 1", "input": 1, "me": 1, "aux": [],
         "out_gpio": 17, "out_trigger": "pgm", "out_active_high": False,
         "in_gpio": 27, "in_edge": "falling", "in_debounce_ms": 20,
         "in_action_type": "atem_aux", "in_atem_aux": 1, "in_atem_source": 5},
        {"id": "cam2", "name": "Kamera 2", "input": 2, "me": 1, "aux": [],
         "out_gpio": 22, "out_trigger": "pgm", "out_active_high": False},
        {"id": "cam3", "name": "Kamera 3 (Handheld)", "input": 3, "me": 1,
         "aux": [1], "out_gpio": 23, "out_trigger": "pgm_pvw",
         "out_active_high": True},
    ],
}

EINGAENGE = {"1": "Kamera 1", "2": "Kamera 2", "3": "Kamera 3",
             "4": "Laptop", "5": "Playback", "10010": "MP 1"}

# Drei Lagen. `demo: true` steht in jeder — die Oberflaeche zeigt es an, und
# eine Lage, die sich nicht als solche zu erkennen gibt, waere die Luege,
# gegen die `run-local.py` schon abgedichtet ist.
LAGEN = [
    {
        "id": "pgm1",
        "titel": "PGM 1 / PVW 2",
        "erklaerung": "Kamera 1 ist auf Sendung, Kamera 2 in der Vorschau. "
                      "AUX 1 fuehrt Playback — Kamera 3 beobachtet AUX 1 und "
                      "bleibt deshalb frei.",
        "atem": {"connected": True, "demo": True, "pgm": {"0": 1}, "pvw": {"0": 2},
                 "aux": {"1": 5}, "inputs": EINGAENGE},
    },
    {
        "id": "pgm2",
        "titel": "PGM 2 / PVW 3, AUX 1 auf Kamera 3",
        "erklaerung": "Umgeschaltet: Kamera 2 ist auf Sendung, Kamera 3 in der "
                      "Vorschau — und weil AUX 1 jetzt Kamera 3 fuehrt, ist "
                      "Kamera 3 trotzdem ROT. Das ist die Aux-Regel, und sie "
                      "ist der Grund, warum diese Entscheidung im Server steht.",
        "atem": {"connected": True, "demo": True, "pgm": {"0": 2}, "pvw": {"0": 3},
                 "aux": {"1": 3}, "inputs": EINGAENGE},
    },
    {
        "id": "offline",
        "titel": "Mischer nicht verbunden",
        "erklaerung": "Ohne Gegenstelle wird keine Verbindung behauptet. Alle "
                      "Geraete stehen auf `offline` — und `offline` sieht auf "
                      "der Tally-Seite ausdruecklich NICHT aus wie `safe`.",
        "atem": {"connected": False, "demo": True, "pgm": {}, "pvw": {}, "aux": {},
                 "inputs": {}, "error": "no ATEM IP configured (set atem_ip in tally.json)"},
    },
]

# Das Raster fuer die Zustandstabelle. Es deckt ab, was ein Besucher an einer
# Geraetekarte verstellen kann; alles darueber hinaus beantwortet die
# Vorfuehrung mit „unknown" und sagt es — raten waere hier das Schlimmste.
EINGANG_BIS = 16
ME_BIS = 4
AUX_VARIANTEN = [[], [1], [2], [1, 2]]


def schluessel(inp, me, aux):
    return f"{inp}|{me}|{','.join(str(a) for a in sorted(aux))}"


def zustandstabelle(atem):
    """Fuer jede Kombination: was sagt die ECHTE Funktion?

    Gebaut wird eine Konfiguration mit einem Geraet je Kombination, und dann
    wird `tally_state_for_device` gefragt — dieselbe Funktion, die auf dem Pi
    entscheidet, ob eine Lampe rot wird.
    """
    geraete, schluessel_zu_id = [], {}
    for inp in range(1, EINGANG_BIS + 1):
        for me in range(1, ME_BIS + 1):
            for aux in AUX_VARIANTEN:
                k = schluessel(inp, me, aux)
                gid = f"raster-{len(geraete)}"
                schluessel_zu_id[k] = gid
                geraete.append({"id": gid, "name": k, "input": inp, "me": me,
                                "aux": list(aux)})
    cfg = {"devices": geraete}
    return {k: gs.tally_state_for_device(cfg, gid, atem_state=atem)
            for k, gid in schluessel_zu_id.items()}


def cue_tabelle():
    """Was `cue_view` fuer jedes Alter in Sekunden sagt — vom Original.

    Auch das ist eine Entscheidung („gilt diese Nachricht noch?"), und auch
    sie wird nicht nachgebaut, sondern abgefragt und nachgeschlagen.
    """
    jetzt = 1_000_000.0
    tabelle = []
    for alter in range(0, int(gs.CUE_DEFAULT_TTL_S) + 6):
        cue = {"text": "x", "kind": "info", "at": jetzt - alter,
               "ttl_s": gs.CUE_DEFAULT_TTL_S}
        tabelle.append(gs.cue_view(cue, jetzt)["state"])
    return tabelle


def schreibe_konfiguration():
    paths.ensure_dirs()
    paths.atomic_write_json(paths.TALLY_FILE, KONFIGURATION, indent=2)
    paths.atomic_write_json(paths.BINDINGS_FILE, [], indent=2)


def lage_bauen(lage):
    """Eine Lage ablegen und das ECHTE Programm dazu befragen."""
    paths.atomic_write_json(paths.ATEM_STATE, lage["atem"])
    atem = gs.get_atem_state()
    return {
        "id": lage["id"],
        "titel": lage["titel"],
        "erklaerung": lage["erklaerung"],
        "antworten": {
            "/atem": atem,
            "/tally-config": gs.load_tally_config(),
            "/tally-diagnostics": gs.build_tally_diagnostics(),
            "/gpio": gs.parse_gpio_state(),
            "/bindings": gs.load_bindings(),
            "/numato": gs.get_numato_state(),
            "/tally-out": gs.TALLY_OUTPUTS.state(),
            "/service/pi-gpio-watcher": gs.watcher_status(),
        },
        "zustaende": zustandstabelle(atem),
    }


def seite_mit_vorfuehrung(html: str, tiefe: int, titel_zusatz: str,
                          kompakt: bool = False) -> str:
    """Kopfband und Nachschlagewerk in eine echte Seite haengen.

    Eingehaengt wird im `<head>`, VOR den Skripten der Seite: `demo.js`
    ersetzt `fetch` und `EventSource`, und das muss stehen, bevor die erste
    Anfrage der Seite laeuft.
    """
    hoch = "../" * tiefe
    einhang = (
        f'<link rel="stylesheet" href="{hoch}demo.css">\n'
        f'<script>window.__demoWurzel = {json.dumps(hoch or "./")};'
        f'window.__demoSeite = {json.dumps(titel_zusatz)};'
        f'window.__demoKompakt = {"true" if kompakt else "false"};</script>\n'
        f'<script src="{hoch}demo.js"></script>\n'
    )
    if "</head>" in html:
        return html.replace("</head>", einhang + "</head>", 1)
    return einhang + html


# ── Der Waechter ───────────────────────────────────────────────────────────
#
# WAS HIER SCHIEFGEHEN WUERDE, OHNE DASS ES JEMAND MERKT: Die Oberflaeche
# bekommt eine neue Abfrage — sagen wir `/audio` —, niemand denkt an die
# Vorfuehrung, und auf der Projektseite bleibt ein Feld leer oder eine Karte
# zeigt einen Fehler. Nicht am Tag der Aenderung, sondern irgendwann, wenn
# jemand die Seite oeffnet und einen kaputten Eindruck mitnimmt.
#
# Deshalb liest dieser Bauer BEIDE Seiten: welche Wege die echten Seiten
# abfragen, und welche `web/demo.js` beantwortet. Fehlt einer, bricht der
# Bau ab — mit dem Namen des Weges. Das ist die einzige Stelle, an der
# dieser Abgleich moeglich ist, und er kostet nichts.
WEG_IM_JS = re.compile(r'weg === "(/[^"]+)"|weg\.indexOf\("(/[^"]+)"\)')
WEG_IN_SEITE = re.compile(r"""(?:fetch|EventSource)\(\s*['"`](/[^'"`?]*)""")


def bedient_von_demo_js() -> set:
    text = (WEB / "demo.js").read_text(encoding="utf-8")
    return {a or b for a, b in WEG_IM_JS.findall(text)}


def bedient(lagen) -> set:
    """Alles, was die Vorfuehrung beantwortet — aus BEIDEN Quellen.

    `demo.js` beantwortet, was es selbst zusammenstellt (die Konfiguration,
    das Protokoll, den Tally-Zustand). Alles Uebrige kommt unveraendert aus
    der vorberechneten Antworttabelle, und deren Schluessel sind die Wege.
    """
    aus_tabelle = set(lagen[0]["antworten"]) if lagen else set()
    return bedient_von_demo_js() | aus_tabelle


def gefordert_von(seiten: dict) -> dict:
    """Weg -> woher die Forderung kommt."""
    raus = {}
    for name, html in seiten.items():
        for weg in WEG_IN_SEITE.findall(html):
            raus.setdefault(weg, set()).add(name)
    return raus


def waechter(seiten: dict, lagen) -> None:
    beantwortet = bedient(lagen)
    fehlend = []
    for weg, woher in sorted(gefordert_von(seiten).items()):
        if weg in beantwortet:
            continue
        # Praefix-Wege (`/tally-out/17/on`) stehen im JS als Anfang da.
        if any(weg.startswith(b) for b in beantwortet):
            continue
        fehlend.append(f"{weg}  (gefragt von: {', '.join(sorted(woher))})")
    if fehlend:
        sys.exit(
            "Die Vorfuehrung beantwortet nicht alles, was die Seiten fragen.\n"
            "  " + "\n  ".join(fehlend) + "\n"
            "Entweder `web/demo.js` ergaenzen oder — wenn der Weg in der "
            "Vorfuehrung nichts zu suchen hat — dort ausdruecklich mit einem "
            "Grund beantworten."
        )



# ── Der Weg von der Startseite in die Anwendung ────────────────────────────
#
# `build-site.py` rendert das README nach `_site/index.html` und weiss von
# dieser Vorfuehrung nichts — es ist dieselbe Datei in vier Repos und soll
# das bleiben. Den Verweis haengt deshalb dieser Bauer ein, unmittelbar
# unter der Ueberschrift: wer die Projektseite oeffnet, soll die Anwendung
# sehen und nicht erst einen Absatz darueber lesen muessen. Genau das war
# die Meldung.
VERWEIS = """
<a class="demo-einstieg" href="demo/index.html">
  <span class="demo-einstieg-marke">▶ Live ausprobieren</span>
  <span class="demo-einstieg-text">Die <strong>echte Oberflaeche</strong> von Tally Pi
  im Browser — Geraetekarten, Tally-Diagnose, Browser-Tally, Cue-Anzeige.
  Die Zustaende rechnet <code>guide_server.py</code> beim Bauen aus. Kein Pi
  noetig, nichts wird geschaltet.</span>
</a>
<style>
  .demo-einstieg { display:block; margin:0 0 2rem; padding:1rem 1.2rem;
    border:1px solid #7c5cc4; border-radius:10px; background:#2a1550;
    color:#f4f0ff; text-decoration:none; }
  .demo-einstieg:hover { background:#3b1d6e; }
  .demo-einstieg-marke { display:block; font-weight:700; margin-bottom:.3rem; }
  .demo-einstieg-text { display:block; font-size:.92rem; opacity:.92; }
  .demo-einstieg code { background:rgba(255,255,255,.12); padding:0 4px; border-radius:3px; }
</style>
"""


def verweis_einhaengen() -> bool:
    """Den Knopf in die gerenderte Startseite setzen. Fehlt sie, ist das kein
    Fehler: dieser Bauer laeuft auch allein, etwa zum Ansehen vor Ort."""
    start = ZIEL.parent / "index.html"
    if not start.exists():
        return False
    text = start.read_text(encoding="utf-8")
    if "demo-einstieg" in text:
        return True
    # Ganz oben, vor allem anderen. Ein Knopf unter drei Absaetzen Text
    # beantwortet die Meldung nicht — sie lautete, dass die Seite die
    # Anwendung nicht zeigt.
    marke = "<main>"
    i = text.find(marke)
    if i < 0:
        return False
    i += len(marke)
    start.write_text(text[:i] + "\n" + VERWEIS + text[i:], encoding="utf-8")
    return True


def main() -> int:
    fehlend = [d for d in (WEB / "demo.js", WEB / "demo.css") if not d.exists()]
    if fehlend:
        sys.exit(f"Fehlt: {', '.join(str(d) for d in fehlend)}")

    ZIEL.mkdir(parents=True, exist_ok=True)
    schreibe_konfiguration()

    lagen = [lage_bauen(l) for l in LAGEN]

    daten = {
        "gebaut_am": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
        "lagen": lagen,
        "cue_zustaende": cue_tabelle(),
        "cue": {"ttl_s": gs.CUE_DEFAULT_TTL_S, "max_zeichen": gs.CUE_MAX_CHARS,
                "arten": list(gs.CUE_KINDS)},
        "tally": {"herzschlag_ms": int(gs.TALLY_HEARTBEAT_S * 1000),
                  "alt_ms": int(gs.TALLY_STALE_S * 1000)},
        "geraete": [{"id": d["id"], "name": d["name"]}
                    for d in KONFIGURATION["devices"]],
        # Was die Vorfuehrung NICHT vom Programm hat, steht hier beisammen
        # und nirgends sonst: eine Maschine gibt es nicht, also gibt es auch
        # keine Adressen. TEST-NET-1 (192.0.2.0/24) ist der Bereich, den die
        # RFCs genau dafuer reservieren — er sieht aus wie eine Adresse und
        # ist nachweislich keine.
        "ipconfig": {
            "hostname": "tally-pi (Vorfuehrung)",
            "interfaces": [{"name": "(kein Geraet)", "addresses": [], "state": "—",
                            "note": "Diese Seite laeuft im Browser, nicht auf "
                                    "einem Pi. Eine Netzadresse haette hier "
                                    "nichts zu bedeuten."}],
            "platform": "browser",
        },
    }
    (ZIEL / "daten.json").write_text(json.dumps(daten, ensure_ascii=False, indent=1),
                                     encoding="utf-8")

    shutil.copy2(WEB / "demo.js", ZIEL / "demo.js")
    shutil.copy2(WEB / "demo.css", ZIEL / "demo.css")

    # Die echten Seiten — erst einsammeln, dann pruefen, dann schreiben.
    oberflaeche = (WURZEL / "setup-guide.html").read_text(encoding="utf-8")
    roh = {"setup-guide.html": oberflaeche,
           "render_tally_page": gs.render_tally_page("cam1", "Kamera 1"),
           "render_cue_page": gs.render_cue_page(),
           "render_cue_control_page": gs.render_cue_control_page()}
    waechter(roh, lagen)

    (ZIEL / "index.html").write_text(
        seite_mit_vorfuehrung(oberflaeche, 0, "Setup-Oberflaeche"),
        encoding="utf-8")

    # Die Tally-Seiten, gerendert von `render_tally_page` — inklusive
    # Maskierung des Namens und der beiden Fristen, die sonst jemand
    # verstellt und der Wachhund laeuft falsch.
    seiten = 0
    for d in KONFIGURATION["devices"]:
        ordner = ZIEL / "tally" / d["id"]
        ordner.mkdir(parents=True, exist_ok=True)
        (ordner / "index.html").write_text(
            # `tally/<id>/index.html` liegt ZWEI Verzeichnisse unter der
            # Vorfuehrung. Eine zu grosse Tiefe faellt beim Ausprobieren im
            # Wurzelverzeichnis NICHT auf (der Browser klemmt `../` an der
            # Wurzel ab) und erst auf der Projektseite unter `/demo/` —
            # deshalb zaehlt `tests/test_projektseite.py` sie nach.
            seite_mit_vorfuehrung(gs.render_tally_page(d["id"], d["name"]), 2,
                                  f"Tally-Seite {d['name']}", kompakt=True),
            encoding="utf-8")
        seiten += 1

    (ZIEL / "cue").mkdir(parents=True, exist_ok=True)
    (ZIEL / "cue" / "index.html").write_text(
        seite_mit_vorfuehrung(gs.render_cue_page(), 1, "Cue-Anzeige (Buehne)",
                              kompakt=True),
        encoding="utf-8")
    (ZIEL / "cue" / "control").mkdir(parents=True, exist_ok=True)
    (ZIEL / "cue" / "control" / "index.html").write_text(
        seite_mit_vorfuehrung(gs.render_cue_control_page(), 2, "Cue-Regie"),
        encoding="utf-8")

    gesetzt = verweis_einhaengen()
    print(f"Vorfuehrung gebaut nach {ZIEL}")
    print(f"  Verweis auf der Startseite: {'gesetzt' if gesetzt else 'keine Startseite gefunden'}")
    print(f"  Lagen:          {', '.join(l['id'] for l in lagen)}")
    print(f"  Zustandstabelle: {len(lagen[0]['zustaende'])} Kombinationen je Lage, "
          f"gerechnet von guide_server.tally_state_for_device")
    print(f"  Seiten:         Oberflaeche, {seiten} Tally-Seite(n), Cue-Anzeige, Cue-Regie")
    for l in lagen:
        zust = {d["id"]: d["state"] for d in l["antworten"]["/tally-diagnostics"]["devices"]}
        print(f"    {l['id']:8s} {zust}")
    shutil.rmtree(ARBEIT, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
