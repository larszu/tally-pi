@echo off
rem ---------------------------------------------------------------------------
rem Tally-Pi lokal starten -- unter Windows zum Doppelklicken.
rem
rem WELCHES PYTHON: zuerst der Starter `py -3` (den legt jede Installation von
rem python.org an), sonst `python` aus dem PATH. Der Starter zuerst, weil
rem `python.exe` unter Windows oft die Platzhalter-Verknuepfung des Microsoft
rem Store ist -- die oeffnet den Store statt das Programm zu starten, und der
rem Nutzer sieht nie eine Fehlermeldung.
rem
rem WAS ER TUT: `run-local.py` mit allem, was uebergeben wurde. Ohne Argumente
rem also der normale Start -- die Oberflaeche kommt hoch, der ATEM-Watcher
rem sucht einen Mischer und meldet ehrlich "nicht verbunden", solange keiner
rem da ist. Wer nur die Oberflaeche ansehen will, nimmt --demo.
rem
rem Beim ERSTEN Start fragt die Windows-Firewall, ob Python Verbindungen
rem annehmen darf. Zulassen ist noetig, damit ein Handy im selben WLAN die
rem Tally-Seite oeffnen kann; ablehnen laesst nur diesen Rechner zu.
rem ---------------------------------------------------------------------------
setlocal
cd /d "%~dp0"

set "PY="
py -3 --version >nul 2>&1 && set "PY=py -3"
if not defined PY (
  python --version >nul 2>&1 && set "PY=python"
)

if not defined PY (
  echo Kein Python gefunden.
  echo Unter Windows:  winget install Python.Python.3.12
  echo             oder python.org/downloads  ^(Haken bei "Add python.exe to PATH"^)
  echo.
  pause
  exit /b 1
)

%PY% run-local.py --open %*
set "STATUS=%ERRORLEVEL%"

rem Das Fenster nicht zuschnappen lassen, wenn etwas schiefging: die Meldung
rem darin ist das Einzige, was den Fehler erklaert.
if not "%STATUS%"=="0" (
  echo.
  echo Beendet mit Code %STATUS%.
  pause
)
exit /b %STATUS%
