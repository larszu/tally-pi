#!/usr/bin/env python3
# ---------------------------------------------------------------------------
# Keine GitHub-Action laeuft mehr auf einer abgekuendigten Node-Version.
#
# WARUM ES DAS GIBT. Am Ende jedes CI-Laufs stand seit Monaten:
#
#   Node.js 20 is deprecated. The following actions target Node.js 20 ...
#
# Eine Warnung, die in jedem Lauf steht, liest nach dem dritten Mal niemand
# mehr. Gemessen 2026-09-05 lief hier ALLES auf node20 -- der Workflow hat nur
# zwei Actions, und beide waren es.
#
# Dies ist ein Python-Repo ohne Node in der CI; der Guard ist deshalb hier
# Python, macht aber genau dasselbe wie sein Zwilling `actions-node-runtime.mjs`
# in den JS-Repos der Suite.
#
# WIE ER PRUEFT. Er fragt die Action selbst, nicht eine Tabelle: fuer jedes
# `uses:` in `.github/workflows/` wird deren `action.yml` an genau dem
# gepinnten Ref gelesen und `runs.using` ausgewertet. Eine Liste im Skript
# waere die naechste Behauptung, die veraltet -- in den Nachbar-Repos war genau
# ein solcher Kommentar der Grund, monatelang nicht nachzusehen.
#
# Er folgt EINE Ebene in Composite-Actions hinein. Eine Composite-Action hat
# keine eigene Node-Version; ihre node20-Aufrufe stecken im Inneren und sind
# der gepinnten Zeile nicht anzusehen.
#
# WAS ER NICHT PRUEFT: ob ein neueres Major sonst kompatibel ist. Welche
# `with:`-Schluessel eine Action kennt, steht in ihrer `action.yml` -- wer ein
# Major anhebt, gleicht das ab.
#
# OHNE NETZ prueft er nichts und sagt das. Das ist kein stilles Ueberspringen:
# der Grund steht in der Ausgabe, und der Lauf bleibt gruen, damit ein
# Netz-Aussetzer keinen fremden PR blockiert.
# ---------------------------------------------------------------------------
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

WURZEL = Path(__file__).resolve().parent.parent
WORKFLOWS = WURZEL / ".github" / "workflows"

# Aelter als das gilt als abgekuendigt.
MINDESTENS = 24


def referenzen(text):
    return [m.group(1).strip("\"'") for m in re.finditer(r"^\s*-?\s*uses:\s*(\S+)", text, re.M)]


def alle_referenzen():
    treffer = set()
    if not WORKFLOWS.is_dir():
        return []
    for datei in sorted(WORKFLOWS.glob("*.y*ml")):
        treffer.update(referenzen(datei.read_text(encoding="utf-8")))
    return sorted(treffer)


def pruefbar(referenz):
    return "@" in referenz and not referenz.startswith((".", "docker://"))


def action_yml(referenz):
    """Der Inhalt der action.yml an genau diesem Ref, oder None."""
    pfad, ref = referenz.split("@", 1)
    teile = pfad.split("/")
    basis = f"https://raw.githubusercontent.com/{teile[0]}/{teile[1]}/{ref}"
    rest = "/" + "/".join(teile[2:]) if len(teile) > 2 else ""
    for name in ("action.yml", "action.yaml"):
        try:
            with urllib.request.urlopen(f"{basis}{rest}/{name}", timeout=30) as antwort:
                return antwort.read().decode("utf-8")
        except urllib.error.HTTPError as fehler:
            if fehler.code != 404:
                raise
    return None


def laufzeit(inhalt):
    treffer = re.search(r"^\s*using:\s*['\"]?([\w.-]+)['\"]?", inhalt, re.M)
    return treffer.group(1) if treffer else None


def main():
    alle = alle_referenzen()
    if not alle:
        print("FEHLER: keine `uses:`-Zeilen in .github/workflows/ gefunden -- greift der Suchlauf daneben?")
        return 1

    maengel = []
    gesehen = set()
    # Erst ALLE eigenen Pins, dann das Innere der Composites: taucht dieselbe
    # Action beides Mal auf, soll die Meldung an dem Pin haengen, den man
    # tatsaechlich anheben kann -- an unserem.
    offen = [(referenz, None) for referenz in alle]
    try:
        while offen:
            naechste = []
            for referenz, ueber in offen:
                if not pruefbar(referenz) or referenz in gesehen:
                    continue
                gesehen.add(referenz)
                inhalt = action_yml(referenz)
                if inhalt is None:
                    maengel.append(f"{referenz}: keine action.yml an diesem Ref (Tippfehler im Pin?)")
                    continue
                art = laufzeit(inhalt)
                if art == "composite":
                    naechste.extend((innen, referenz) for innen in referenzen(inhalt))
                    continue
                if art and art.startswith("node") and art[4:].isdigit() and int(art[4:]) < MINDESTENS:
                    if ueber:
                        maengel.append(
                            f"{referenz} laeuft auf {art} (verschachtelt in {ueber}) -- "
                            f"gepinnt wird {ueber}, also dort das Major anheben."
                        )
                    else:
                        maengel.append(f"{referenz} laeuft auf {art} -- ein neueres Major anheben.")
            offen = naechste
    except (urllib.error.URLError, OSError) as fehler:
        # Kein stilles Ueberspringen: der Grund steht hier, und er steht im Log.
        print(f"NICHT GEPRUEFT: die Actions waren nicht erreichbar ({fehler}).")
        print("Der Lauf bleibt gruen, damit ein Netz-Aussetzer keinen PR blockiert.")
        return 0

    if not maengel:
        print(f"OK: {len(gesehen)} Action-Referenz(en) geprueft, alle auf node{MINDESTENS} oder neuer.")
        return 0

    print(f"FEHLER: {len(maengel)} Action(s) auf abgekuendigter Node-Version:\n")
    for m in maengel:
        print(f"  ! {m}")
    print(
        "\nGitHub faehrt sie derzeit noch ersatzweise auf node24 und schreibt eine Warnung\n"
        "ans Ende jedes Laufs. Das ist eine Frist, kein Zustand."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
