@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"

rem ---- 0. arguments ---------------------------------------------------
rem Flags may come in any order and any position; whatever is left over is the
rem version number. Parsing them by hand keeps "build.bat 1.3.1 --with-apk"
rem from being passed to build.py (which would take "--with-apk" as a version).
set "EXITCODE=0"
set "APK_MODE="
set "APK_CHECK="
set "CHECK_ONLY="
set "APK_BUILD=debug"
set "WITH_APK="
set "NO_APK="
set "SHOW_HELP="
set "ARG_VERSION="

:parse_args
if "%~1"=="" goto :args_done
set "CURARG=%~1"
if /i "!CURARG!"=="apk" set "APK_MODE=1"
if /i "!CURARG!"=="--apk" set "APK_MODE=1"
if /i "!CURARG!"=="--release" set "APK_BUILD=release"
if /i "!CURARG!"=="--check" set "CHECK_ONLY=1"
if /i "!CURARG!"=="--with-apk" set "WITH_APK=1"
if /i "!CURARG!"=="--no-apk" set "NO_APK=1"
if /i "!CURARG!"=="--help" set "SHOW_HELP=1"
if /i "!CURARG!"=="-h" set "SHOW_HELP=1"
if not "!CURARG:~0,1!"=="-" if /i not "!CURARG!"=="apk" if not defined ARG_VERSION set "ARG_VERSION=!CURARG!"
shift
goto :parse_args

:args_done
rem "--check apk" checks the Android toolchain instead of the python ones.
if defined CHECK_ONLY if defined APK_MODE set "APK_CHECK=1"
if defined APK_CHECK set "CHECK_ONLY="
rem --no-apk wins when both are given.
if defined NO_APK set "WITH_APK="

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
echo           --with-apk [--release] : also build the Android APK afterwards
echo           --no-apk           : never ask about the Android APK
echo           apk [version]      : build Android debug APK through WSL2 + Buildozer
echo           apk --release [ver]: build signed release APK (keystore env required)
echo           --check            : check python/deps only, no build
echo           --check apk        : check WSL / Buildozer / Android toolchain only
echo.

if defined SHOW_HELP goto :done
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
if defined CHECK_ONLY (
    echo [done] environment looks good, ready to build.
    set "EXITCODE=0"
    goto :done
)

rem ---- 4. build -------------------------------------------------------
echo [build] !PY! build.py !ARG_VERSION!
echo.
!PY! build.py !ARG_VERSION!
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
    goto :done
)

rem ---- 5. optional Android APK ----------------------------------------
call :maybe_apk
goto :done

:apk_check
echo [apk] checking WSL2 / Ubuntu / Buildozer ...
where wsl >nul 2>nul
if errorlevel 1 (
    echo [FAILED] wsl.exe not found. Install WSL2 first ^(admin PowerShell^):
    echo          wsl --install -d Ubuntu-22.04
    echo          then reboot and run this file again.
    set "EXITCODE=1"
    goto :done
)
call :findwsl
if errorlevel 1 (
    echo [FAILED] no Ubuntu WSL distribution found:
    echo          wsl --install -d Ubuntu-22.04
    set "EXITCODE=1"
    goto :done
)
echo [apk] distro: !WSLDISTRO!
!WSLEXE! bash -lc "command -v python3 >/dev/null 2>&1"
if errorlevel 1 (
    echo [FAILED] python3 not found in WSL. Install:
    echo          sudo apt install -y python3 python3-venv
    set "EXITCODE=1"
    goto :done
)
!WSLEXE! bash -lc "test -x $HOME/.buildozer-venv/bin/buildozer || command -v buildozer >/dev/null 2>&1"
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
!WSLEXE! bash -lc "test -x /usr/lib/jvm/java-17-openjdk-amd64/bin/java || test -x /usr/lib/jvm/java-17-openjdk/bin/java"
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
if not defined APK_VERSION set "APK_VERSION=!ARG_VERSION!"
if not defined APK_VERSION set "APK_VERSION=0.1.0"
call :build_apk
goto :done

:build_apk
rem Shared by "build.bat apk ..." (APK only) and the optional step after a
rem desktop build. Needs APK_VERSION / APK_BUILD, sets EXITCODE.
where wsl >nul 2>nul
if errorlevel 1 (
    echo [FAILED] wsl.exe not found. Install WSL2 first ^(admin PowerShell^):
    echo          wsl --install -d Ubuntu-22.04
    echo          then reboot and run this file again.
    set "EXITCODE=1"
    exit /b 1
)
call :findwsl
if errorlevel 1 (
    echo [FAILED] no Ubuntu WSL distribution found:
    echo          wsl --install -d Ubuntu-22.04
    set "EXITCODE=1"
    exit /b 1
)
echo [apk] distro: !WSLDISTRO!
set "WSLROOT="
for /f "usebackq delims=" %%i in (`!WSLEXE! wslpath -a "%CD%"`) do set "WSLROOT=%%i"
if not defined WSLROOT (
    echo [FAILED] cannot translate project path for WSL.
    set "EXITCODE=1"
    exit /b 1
)
rem Pass Windows-side release keystore settings into WSL when present.
set "APK_WSLENV="
if defined P4A_RELEASE_KEYSTORE set "APK_WSLENV=!APK_WSLENV!P4A_RELEASE_KEYSTORE/p:"
if defined P4A_RELEASE_KEYSTORE_PASSWD set "APK_WSLENV=!APK_WSLENV!P4A_RELEASE_KEYSTORE_PASSWD:"
if defined P4A_RELEASE_KEYALIAS set "APK_WSLENV=!APK_WSLENV!P4A_RELEASE_KEYALIAS:"
if defined APK_WSLENV (
    set "APK_WSLENV=!APK_WSLENV:~0,-1!"
    if defined WSLENV (
        set "WSLENV=!WSLENV!:!APK_WSLENV!"
    ) else (
        set "WSLENV=!APK_WSLENV!"
    )
)

echo [apk] !APK_BUILD! APK, version !APK_VERSION!
echo [apk] WSL path: !WSLROOT!
!WSLEXE! bash -lc "cd '!WSLROOT!' && bash build_android.sh '!APK_VERSION!' '!APK_BUILD!'"
set "EXITCODE=!ERRORLEVEL!"
if "!EXITCODE!"=="0" (
    echo [done] APK is in the project root: Vocawiki-create-tool ^(!APK_VERSION!^).apk
) else (
    echo [FAILED] APK build stopped with exit code !EXITCODE!.
    echo          see mobile/bin/ and the Buildozer log above.
)
exit /b !EXITCODE!

:maybe_apk
rem Runs after a successful desktop build. The APK needs WSL2 + Buildozer and
rem takes minutes, so it is opt-in: --with-apk, or a y answer when a real
rem console is attached (double-click / interactive shell).
if defined APK_MODE exit /b 0
if defined NO_APK (
    echo [apk] skipped ^(--no-apk^).
    exit /b 0
)
set "WANT_APK="
if defined WITH_APK set "WANT_APK=1"
if not defined WANT_APK (
    rem Pipes, "<nul" and CI fail this probe - never block on them.
    timeout /t 1 /nobreak >nul 2>nul
    if errorlevel 1 (
        echo [apk] skipped; pass --with-apk to build the Android APK too.
        exit /b 0
    )
    echo.
    set "ANSWER="
    set /p "ANSWER=[apk] also build the Android APK? needs WSL2 + Buildozer [y/N]: "
    if /i "!ANSWER!"=="y" set "WANT_APK=1"
    if /i "!ANSWER!"=="yes" set "WANT_APK=1"
)
if not defined WANT_APK (
    echo [apk] skipped.
    exit /b 0
)
if not defined APK_VERSION set "APK_VERSION=!ARG_VERSION!"
if not defined APK_VERSION call :apk_version_from_zip
if not defined APK_VERSION set "APK_VERSION=0.1.0"
echo.
call :build_apk
exit /b !EXITCODE!

:apk_version_from_zip
rem Reuse the version build.py just used: newest "Vocawiki-create-tool (x.y.z).zip"
for /f "delims=" %%f in ('dir /b /o-d "Vocawiki-create-tool (*).zip" 2^>nul') do (
    if not defined APK_VERSION (
        for /f "tokens=2 delims=()" %%v in ("%%~nf") do set "APK_VERSION=%%v"
    )
)
exit /b 0

:findwsl
rem `wsl -l -q` prints UTF-16, which findstr cannot match inside cmd.
rem So probe the usual distro names instead; this also accepts Ubuntu-22.04 etc.
set "WSLDISTRO="
set "WSLEXE="
rem A missing distro makes wsl.exe exit with a negative HRESULT, which
rem `if errorlevel 1` would wrongly treat as success - compare against 0.
for %%d in (Ubuntu Ubuntu-22.04 Ubuntu-24.04 Ubuntu-20.04 Ubuntu-18.04) do (
    if not defined WSLDISTRO (
        wsl -d %%d -- true >nul 2>nul
        if "!ERRORLEVEL!"=="0" set "WSLDISTRO=%%d"
    )
)
if defined WSLDISTRO (
    set "WSLEXE=wsl -d !WSLDISTRO!"
    exit /b 0
)
rem A distro with a custom name: fall back to whatever WSL uses by default.
wsl -- true >nul 2>nul
if "!ERRORLEVEL!"=="0" (
    set "WSLDISTRO=(default)"
    set "WSLEXE=wsl"
    exit /b 0
)
exit /b 1

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
