#!/usr/bin/env python3
# ---------------------------------------------------------------------------
# Baut das fertige, doppelklickbare Tally-Pi und packt es als .zip.
#
# WOZU DIESES SKRIPT. Die Release-Action ruft es auf jedem der beiden Systeme
# (macOS, Windows) auf. Dass es ein Skript ist und keine Kette von
# Workflow-Schritten, hat denselben Grund wie bei `build-site.py`: so laesst
# sich derselbe Bau auf dem eigenen Rechner nachstellen —
#
#     python3 scripts/build-local.py v1.2.3
#
# — und was in der CI schiefgeht, geht auch hier schief, an derselben Stelle.
#
# WAS ES TUT, IN ZWEI SCHRITTEN:
#   1. PyInstaller nach dem Rezept `tally-pi-local.spec` laufen lassen. Das
#      erzeugt `dist/tally-pi-local/` — einen Ordner mit dem Programm und
#      allem, was es zur Laufzeit braucht (siehe Spec).
#   2. Diesen Ordner in `release/tally-pi-local-<system>-<version>.zip` packen.
#      Der Ordnername im Zip bleibt `tally-pi-local/`, damit das Entpacken
#      einen benannten Ordner ergibt und nicht lose Dateien im Download.
#
# WARUM `shutil.make_archive` UND KEIN `zip`. `zip` gibt es unter Windows nicht
# von Haus aus; die Git-Bash auf dem Runner bringt es nicht mit. `shutil` ist
# in jedem Python dabei und packt auf allen drei Systemen gleich — und es
# behaelt das Ausfuehr-Bit des Programms im Zip, das der Mac beim Entpacken
# wieder braucht.
# ---------------------------------------------------------------------------
import shutil
import subprocess
import sys
from pathlib import Path

WURZEL = Path(__file__).resolve().parent.parent
SPEC = WURZEL / "tally-pi-local.spec"
DIST = WURZEL / "dist" / "tally-pi-local"
RELEASE = WURZEL / "release"


def systemname() -> str:
    """Kurzer, stabiler Name des Zielsystems fuer den Dateinamen."""
    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform.startswith("linux"):
        return "linux"
    return sys.platform


def main() -> int:
    # Die Version steht im Dateinamen, damit ein Download unterscheidbar ist.
    # Ohne Argument "dev" — dann ist es offenkundig kein Release-Build.
    version = sys.argv[1] if len(sys.argv) > 1 else "dev"
    version = version.lstrip("v") or "dev"

    if not SPEC.exists():
        print(f"FEHLER: {SPEC.name} fehlt — ohne das Rezept kein Bau.",
              file=sys.stderr)
        return 1

    print(f"[build] PyInstaller laeuft ({systemname()}, Version {version}) ...")
    ergebnis = subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
         str(SPEC)],
        cwd=str(WURZEL),
    )
    if ergebnis.returncode != 0:
        print("FEHLER: PyInstaller ist gescheitert — siehe Ausgabe oben.",
              file=sys.stderr)
        return ergebnis.returncode

    if not DIST.is_dir():
        print(f"FEHLER: {DIST} wurde nicht gebaut. Ein stiller Fehlschlag von "
              f"PyInstaller — nicht als Erfolg durchwinken.", file=sys.stderr)
        return 1

    RELEASE.mkdir(exist_ok=True)
    basis = RELEASE / f"tally-pi-local-{systemname()}-{version}"
    # Der Ordnername IM Zip bleibt `tally-pi-local/`: base_dir zeigt auf ihn,
    # root_dir ist sein Elternverzeichnis. So entpackt sich ein benannter
    # Ordner statt eines Haufens loser Dateien.
    zip_pfad = shutil.make_archive(
        str(basis), "zip",
        root_dir=str(DIST.parent), base_dir=DIST.name)

    groesse = Path(zip_pfad).stat().st_size / (1024 * 1024)
    print(f"[build] fertig: {zip_pfad} ({groesse:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
