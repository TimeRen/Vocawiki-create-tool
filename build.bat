@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"

rem Pause at the end only when the file was double-clicked
rem (cmdcmdline contains this script's name only in that case).
set "PAUSE_AT_END="
echo %cmdcmdline% | find /i "%~nx0" >nul
if not errorlevel 1 set "PAUSE_AT_END=1"

echo ============================================
echo   Vocawiki-create-tool : build
echo ============================================
echo   usage:  build.bat [version]      e.g.  build.bat 1.3.1
echo           no version : build.py will ask for it
echo           --check    : check python/deps only, no build
echo.

rem ---- 1. find python (prefer the project's own .venv) ----------------
set "PY="
if exist ".venv\Scripts\python.exe" (
    set "PY=.venv\Scripts\python.exe"
    echo [env] using project virtualenv: .venv
) else (
    where python >nul 2>nul
    if not errorlevel 1 set "PY=python"
    if not defined PY (
        where py >nul 2>nul
        if not errorlevel 1 set "PY=py -3"
    )
    if not defined PY (
        echo [FAILED] Python not found. Install Python 3.10+ with
        echo          "Add python.exe to PATH" checked, then run this file again.
        set "EXITCODE=1"
        goto :done
    )
    echo [env] no .venv yet, creating one with !PY! ^(one time only^) ...
    !PY! -m venv .venv
    if not exist ".venv\Scripts\python.exe" (
        echo [FAILED] could not create .venv, try: !PY! -m venv .venv
        set "EXITCODE=1"
        goto :done
    )
    set "PY=.venv\Scripts\python.exe"
)

rem ---- 2. dependencies: install requirements.txt when something lacks --
echo [deps] checking build/GUI dependencies ...
!PY! -c "import PyInstaller, PyQt5, yaml, requests, PIL, numpy" >nul 2>nul
if errorlevel 1 (
    echo [deps] missing packages, installing requirements.txt ^(may take a few minutes^) ...
    !PY! -m pip install --upgrade pip
    !PY! -m pip install -r requirements.txt
    !PY! -c "import PyInstaller, PyQt5, yaml, requests, PIL, numpy" >nul 2>nul
    if errorlevel 1 (
        echo [FAILED] dependencies still missing, run manually:
        echo          !PY! -m pip install -r requirements.txt
        set "EXITCODE=1"
        goto :done
    )
)
echo [deps] ok.
echo.

rem ---- 3. environment check only --------------------------------------
if /i "%~1"=="--check" (
    echo [done] environment looks good, ready to build.
    set "EXITCODE=0"
    goto :done
)

rem ---- 4. build -------------------------------------------------------
echo [build] !PY! build.py %*
echo.
!PY! build.py %*
set "EXITCODE=!ERRORLEVEL!"
echo.
if "!EXITCODE!"=="0" (
    echo [done] build finished:
    echo        dist\         - exe + runtime resources
    echo        *.zip in root - the release package
) else (
    echo [FAILED] build stopped with exit code !EXITCODE!. Common causes:
    echo          - dependencies not installed completely
    echo          - the exe is still running ^(file locked^)
    echo          - not enough disk space
    echo        see the output above, and build\Vocawiki-create-tool\warn-Vocawiki-create-tool.txt
)

:done
echo.
if defined PAUSE_AT_END (
    rem Never block automated runs: keep the window open only when stdin really is
    rem an interactive console (pipes, "<nul", CI, ... fail the timeout probe).
    if /i not "%VOCAWIKI_NOPAUSE%"=="1" (
        timeout /t 1 /nobreak >nul 2>nul
        if not errorlevel 1 (
            echo press any key to close ...
            pause >nul
        )
    )
)
rem Note: no "endlocal" here - !EXITCODE! must still expand when we exit.
exit /b !EXITCODE!
