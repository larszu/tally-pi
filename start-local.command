#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# Tally-Pi lokal starten — auf dem Mac zum Doppelklicken.
#
# WARUM DIE ENDUNG `.command`: Der Finder oeffnet damit ein Terminal-Fenster
# und fuehrt die Datei aus. Eine `.sh` wuerde er im Editor aufmachen. Auf
# Linux laeuft dieselbe Datei mit `./start-local.command` oder `bash
# start-local.command`.
#
# WAS ER TUT: `run-local.py` mit allem, was uebergeben wurde. Ohne Argumente
# also der normale Start — die Oberflaeche kommt hoch, der ATEM-Watcher sucht
# einen Mischer und meldet ehrlich „nicht verbunden", solange keiner da ist.
# Wer nur die Oberflaeche ansehen will, nimmt `--demo`.
#
# Beim ERSTEN Start fragt macOS, ob Python Verbindungen aus dem Netz annehmen
# darf. „Erlauben" ist noetig, damit ein Handy im selben WLAN die Tally-Seite
# oeffnen kann; „Ablehnen" laesst nur diesen Rechner zu.
# ─────────────────────────────────────────────────────────────────────────────
set -u
cd "$(dirname "$0")" || exit 1

for kandidat in python3 python; do
  if command -v "$kandidat" >/dev/null 2>&1; then
    PY="$kandidat"
    break
  fi
done

if [ -z "${PY:-}" ]; then
  echo "Kein Python gefunden."
  echo "Auf dem Mac:  brew install python   —  oder python.org/downloads"
  echo
  read -r -p "Mit der Eingabetaste schliessen." _
  exit 1
fi

"$PY" run-local.py --open "$@"
status=$?

# Das Fenster nicht zuschnappen lassen, wenn etwas schiefging: die Meldung
# darin ist das Einzige, was den Fehler erklaert.
if [ "$status" -ne 0 ]; then
  echo
  read -r -p "Beendet mit Code $status. Mit der Eingabetaste schliessen." _
fi
exit "$status"
