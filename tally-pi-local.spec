# -*- mode: python ; coding: utf-8 -*-
# ---------------------------------------------------------------------------
# PyInstaller-Rezept fuer das fertige, doppelklickbare Tally-Pi.
#
# WAS HIER GEBAUT WIRD, UND FUER WEN. Bis hierher brauchte der lokale Start ein
# installiertes Python (`start-local.command` / `start-local.bat` rufen es auf).
# Das ist eine Huerde fuer alle, die nur die Oberflaeche sehen wollen: kein
# Python, kein Start. Wie beim cable-planner (dort electron-builder) baut die
# Release-Action `.github/workflows/release.yml` daraus je ein eigenstaendiges
# Programm fuer macOS und Windows — ohne Python, ohne pip, ein Ordner zum
# Entpacken und Starten.
#
# WARUM EIN SPEC UND KEINE KOMMANDOZEILE. Zwei Dinge lassen sich in der
# Kommandozeile nicht sauber ausdruecken und veralten dort als Kommentar:
#
#   * die DATEN, die zur Laufzeit gebraucht werden — `guide_server.py`
#     liefert `setup-guide.html` und `web/` als statische Dateien aus (es
#     bedient sie mit `directory=ROOT`). Ohne sie im Bundle zeigt die
#     Oberflaeche eine leere Seite.
#   * die versteckten Importe — die drei Dienste werden zur Laufzeit ueber
#     `runpy.run_module(name)` gestartet (siehe `run-local.py`,
#     `dienst_aus_bundle_starten`). PyInstaller sieht diesen String nicht als
#     Import und wuerde die Module weglassen; sie muessen von Hand hinein.
#
# WARUM `onedir` UND NICHT `onefile`. Dies ist ein Programm, das VIER Prozesse
# startet (Oberflaeche + drei Dienste). Bei `onefile` entpackt sich das ganze
# Bundle bei JEDEM Start neu in ein Temp-Verzeichnis — hier also viermal, bei
# jedem Dienststart erneut. `onedir` entpackt nichts: der zweite bis vierte
# Prozess starten dieselbe Datei aus demselben Ordner. Der Preis ist ein Ordner
# statt einer Datei; die Release-Action packt ihn ohnehin in ein `.zip`.
#
# WAS DAS BUNDLE NICHT KANN, kann es auf keiner Plattform: GPIO-Eingaenge,
# Tally-Ausgaenge und das OLED brauchen den Pi. Genau wie beim Start aus dem
# Quellbaum sagt die Oberflaeche das an jeder betroffenen Stelle selbst — es
# wird hier nichts nachgebildet und nichts versteckt.
# ---------------------------------------------------------------------------
import sys

# Die drei Dienste, die `run-local.py` per `--_dienst` startet, plus die
# Module, die sie importieren. PyInstaller findet die direkten Importe von
# `run-local.py` selbst; diese hier kommen nur ueber einen `runpy`-String
# hinein und muessen darum ausdruecklich genannt werden.
versteckte_importe = [
    "guide_server",
    "atem_watcher",
    "gpio_watcher",
    "numato_watcher",
    "pi_status",
    "paths",
    "cmd_channel",
]

# Die Dateien, die `guide_server` zur Laufzeit ausliefert. Ziel ".", weil es
# sie unter `ROOT/<name>` erwartet und `ROOT` im Bundle die Bundle-Wurzel ist.
daten = [
    ("setup-guide.html", "."),
    ("web", "web"),
]

# Ad-hoc-Signatur auf dem Mac — genau wie beim cable-planner. Ohne irgendeine
# Signatur weist Gatekeeper auf Apple Silicon das Programm als "beschaedigt"
# ab; die leere Identitaet "-" ist die kostenlose Ad-hoc-Signatur, die diese
# Abweisung vermeidet. Eine bezahlte Notarisierung gibt es nicht, also bleibt
# beim ersten Start der Rechtsklick -> Oeffnen noetig (steht im README).
codesign = "-" if sys.platform == "darwin" else None

a = Analysis(
    ["run-local.py"],
    pathex=[],
    binaries=[],
    datas=daten,
    hiddenimports=versteckte_importe,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="tally-pi-local",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=codesign,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="tally-pi-local",
)
