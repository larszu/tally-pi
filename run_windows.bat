@echo off
REM ===========================================================================
REM  Tally-Pi auf Windows starten.
REM
REM  ─── WAS GEMELDET WURDE (Nutzer, 2026-09-15) ──────────────────────────────
REM
REM    "tally pi muss auch auf windows und mac laufen und dort als gpio
REM     interface einen numato gpio usb benutzen koennen."
REM
REM  ─── WARUM ES DIESE DATEI BRAUCHT ─────────────────────────────────────────
REM
REM  `run-local.py` laeuft auf Windows — es ist Python und benutzt `pathlib`.
REM  Was fehlte, war der WEG dorthin:
REM
REM    * Der Interpreter heisst hier meist `py` und nicht `python3`. Ein
REM      Windows ohne Store-Alias hat gar kein `python` im PATH, und die
REM      Suite setzte genau das ab.
REM    * `pyserial` ist keine Standard-Bibliothek. Ohne sie beendet sich
REM      `numato_watcher.py` sofort — und genau der ist auf Windows das
REM      EINZIGE GPIO-Interface, weil es kein /dev/gpiochip0 gibt.
REM
REM  ─── ZWEI BETRIEBSARTEN, und warum die zweite noetig ist ─────────────────
REM
REM    run_windows.bat            Doppelklick: einrichten, starten, Browser auf
REM    run_windows.bat --server   Vordergrund, kein Browser, kein `pause`
REM
REM  Die zweite ist die fuer die AV-Planner-Suite. Sie startet den Prozess
REM  IM VORDERGRUND: nur dann haengt er am Fenster, das ihn gestartet hat,
REM  und nur dann beendet ihn der Stopp-Knopf wieder. Ein `start ""` waere
REM  abgekoppelt — der Knopf meldete "beendet", und der Server liefe weiter.
REM  Ein `pause` am Ende waere dasselbe Problem von der anderen Seite: der
REM  Aufrufer wartete auf ein Fenster, das auf eine Taste wartet.
REM
REM  Alle weiteren Schalter werden an `run-local.py` durchgereicht, also
REM  `run_windows.bat --server --numato --atem 10.0.0.5`.
REM ===========================================================================
SETLOCAL ENABLEDELAYEDEXPANSION
cd /d "%~dp0"

REM --- Betriebsart und durchgereichte Schalter trennen ----------------------
set "DIENST="
set "ARGS="
:naechstes
if "%~1"=="" goto fertig
if /I "%~1"=="--server" (
    set "DIENST=1"
) else (
    set "ARGS=!ARGS! %1"
)
shift
goto naechstes
:fertig

REM --- Interpreter finden: py, dann python ---------------------------------
REM  `py` ist der Launcher, den der offizielle Installer mitbringt, und er
REM  ist der verlaesslichere der beiden: `python` kann auf den Store-Alias
REM  zeigen, der nur den Store oeffnet.
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
    where python >nul 2>nul && set "PY=python"
)
if not defined PY (
    echo [FEHLER] Python nicht gefunden.
    echo Bitte Python 3.10+ installieren: https://www.python.org/downloads/
    if not defined DIENST pause
    exit /b 1
)

REM --- pyserial: ohne sie gibt es auf Windows kein GPIO ---------------------
%PY% -c "import serial" >nul 2>nul
if errorlevel 1 (
    echo [*] Installiere pyserial ^(fuer das Numato-USB-Modul^) ...
    %PY% -m pip install --quiet pyserial
    if errorlevel 1 (
        echo [WARNUNG] pyserial liess sich nicht installieren.
        echo           Die Oberflaeche laeuft; --numato wird nicht arbeiten.
    )
)

if defined DIENST (
    REM Vordergrund, kein Browser, kein pause — siehe Kopf.
    %PY% run-local.py !ARGS!
    exit /b !ERRORLEVEL!
)

echo ========================================================
echo   Tally-Pi - Windows
echo ========================================================
echo.
echo   Oberflaeche:  http://localhost:8080/
echo   GPIO:         kein /dev/gpiochip0 auf Windows.
echo                 Fuer echte Ein-/Ausgaenge: --numato mit einem
echo                 Numato-USB-Modul am seriellen Anschluss.
echo.
echo   Zum Beenden Strg-C.
echo.
%PY% run-local.py !ARGS!
pause
