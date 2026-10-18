@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"

set "APK_MODE="
set "APK_CHECK="
if /i "%~1"=="apk" set "APK_MODE=1"
if /i "%~1"=="--apk" set "APK_MODE=1"
if /i "%~1"=="--check" if /i "%~2"=="apk" set "APK_CHECK=1"

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
echo           apk [version]       : build Android debug APK through WSL2 + Buildozer
echo           apk --release [ver] : build signed release APK (keystore env required)
echo           --check             : check python/deps only, no build
echo           --check apk         : check WSL / Buildozer / Android toolchain only
echo.

if defined APK_CHECK goto :apk_check
if defined APK_MODE goto :apk

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
    echo [done] build finished, the release package is in the project root:
    echo        Vocawiki-create-tool ^<version^>.zip  - exe + runtime resources, unzip and run
    echo        no dist\ folder is left behind ^(temp files are cleaned up^)
) else (
    echo [FAILED] build stopped with exit code !EXITCODE!. Common causes:
    echo          - dependencies not installed completely
    echo          - the exe is still running ^(file locked^)
    echo          - not enough disk space
    echo        see the output above, and build\Vocawiki-create-tool\warn-Vocawiki-create-tool.txt
)

:apk_check
echo [apk] checking WSL2 / Ubuntu / Buildozer ...
where wsl >nul 2>nul
if errorlevel 1 (
    echo [FAILED] wsl.exe not found. Install WSL2 first:
    echo          wsl --install -d Ubuntu
    set "EXITCODE=1"
    goto :done
)
wsl -l -q 2>nul | findstr /i "Ubuntu" >nul
if errorlevel 1 (
    echo [FAILED] no Ubuntu WSL distribution found:
    echo          wsl --install -d Ubuntu
    set "EXITCODE=1"
    goto :done
)
wsl -d Ubuntu bash -lc "command -v python3 >/dev/null 2>&1"
if errorlevel 1 (
    echo [FAILED] python3 not found in WSL. Install:
    echo          sudo apt install -y python3 python3-venv
    set "EXITCODE=1"
    goto :done
)
wsl -d Ubuntu bash -lc "export PATH=$HOME/.buildozer-venv/bin:$HOME/.local/bin:$PATH; command -v buildozer >/dev/null 2>&1"
if errorlevel 1 (
    echo [FAILED] buildozer not installed in WSL. Run:
    echo          sudo apt update
    echo          sudo apt install -y python3 python3-venv openjdk-17-jdk unzip git zip
    echo          python3 -m venv $HOME/.buildozer-venv
    echo          $HOME/.buildozer-venv/bin/pip install --upgrade pip buildozer cython
    echo          echo 'export PATH="$HOME/.buildozer-venv/bin:$PATH"' ^>^> ~/.bashrc
    set "EXITCODE=1"
    goto :done
)
wsl -d Ubuntu bash -lc "test -x /usr/lib/jvm/java-17-openjdk-amd64/bin/java || test -x /usr/lib/jvm/java-17-openjdk/bin/java"
if errorlevel 1 (
    echo [FAILED] Java 17 not found in WSL. Install:
    echo          sudo apt install -y openjdk-17-jdk
    set "EXITCODE=1"
    goto :done
)
echo [done] WSL / Buildozer / Java look ready.
set "EXITCODE=0"
goto :done

:apk
set "APK_VERSION=%~2"
set "APK_BUILD=debug"
if /i "%~2"=="--release" (
    set "APK_BUILD=release"
    set "APK_VERSION=%~3"
)
if "%APK_VERSION%"=="" set "APK_VERSION=0.0.0"
where wsl >nul 2>nul
if errorlevel 1 (
    echo [FAILED] wsl.exe not found. Install WSL2 first:
    echo          wsl --install -d Ubuntu
    set "EXITCODE=1"
    goto :done
)
wsl -l -q 2>nul | findstr /i "Ubuntu" >nul
if errorlevel 1 (
    echo [FAILED] no Ubuntu WSL distribution found:
    echo          wsl --install -d Ubuntu
    set "EXITCODE=1"
    goto :done
)
set "WSLROOT="
for /f "usebackq delims=" %%i in (`wsl -d Ubuntu wslpath -a "%CD%"`) do set "WSLROOT=%%i"
if not defined WSLROOT (
    echo [FAILED] cannot translate project path for WSL.
    set "EXITCODE=1"
    goto :done
)
echo [apk] %APK_BUILD% APK, version %APK_VERSION%
echo [apk] WSL path: !WSLROOT!
wsl -d Ubuntu bash -lc "cd '!WSLROOT!' && bash build_android.sh '%APK_VERSION%' '%APK_BUILD%'"
set "EXITCODE=!ERRORLEVEL!"
if "!EXITCODE!"=="0" (
    echo [done] APK is in the project root: Vocawiki-create-tool ^(%APK_VERSION%^).apk
) else (
    echo [FAILED] APK build stopped with exit code !EXITCODE!.
    echo          see mobile/bin/ and the Buildozer log above.
)
goto :done

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
