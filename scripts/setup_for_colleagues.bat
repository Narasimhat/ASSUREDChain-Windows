@echo off
setlocal EnableDelayedExpansion

REM One-time setup for colleagues to run ASSUREDChain from the network share
REM This creates a local Python environment and installs dependencies; will auto-install Python if missing

echo ========================================
echo ASSUREDChain Network Setup
echo ========================================
echo.

set "TARGETUNC=\\mdc-berlin.net\fs\AG_Diecke\DATA MANAGMENT\Projects\Gene_Editing_Projects\ASSUREDChain"
set "LOCALDIR=%LOCALAPPDATA%\ASSUREDChain"
set "VENVDIR=%LOCALDIR%\.venv"
set "PY_VER=3.11.7"
set "PY_SHORT=311"
set "PY_URL=https://www.python.org/ftp/python/%PY_VER%/python-%PY_VER%-amd64.exe"
set "PY_TMP=%TEMP%\python-%PY_VER%-amd64.exe"
set "PYTHON_EXE="
set "PY_EMBED_URL=https://www.python.org/ftp/python/%PY_VER%/python-%PY_VER%-embed-amd64.zip"
set "PY_EMBED_ZIP=%TEMP%\python-%PY_VER%-embed-amd64.zip"
set "PY_EMBED_DIR=%LOCALDIR%\py%PY_SHORT%"
set "USING_EMBED=0"

REM Try existing python in PATH
for /f "delims=" %%P in ('where python 2^>nul') do (
    echo Found python candidate: %%P
    echo %%P | find /i "WindowsApps" >nul
    if errorlevel 1 (
        set "PYTHON_EXE=%%P"
        goto :have_python
    ) else (
        echo Skipping WindowsApps python shim; looking for a real install...
    )
)

REM Try py launcher (common on Windows)
for /f "delims=" %%P in ('where py 2^>nul') do (
    echo Found py launcher: %%P
    set "PYTHON_EXE=%%P -3.11"
    goto :have_python
)

REM Try default per-user install location
if exist "%LOCALAPPDATA%\Programs\Python\Python%PY_SHORT%\python.exe" (
    set "PYTHON_EXE=%LOCALAPPDATA%\Programs\Python\Python%PY_SHORT%\python.exe"
    goto :have_python
)

REM Reuse previously downloaded embeddable Python if present
if exist "%PY_EMBED_DIR%\python.exe" (
    set "PYTHON_EXE=%PY_EMBED_DIR%\python.exe"
    set "USING_EMBED=1"
    goto :python_ready
)

REM Download and install locally (no admin required)
echo [0/4] Python not found; downloading %PY_VER%...
powershell -NoProfile -Command "Invoke-WebRequest -Uri '%PY_URL%' -OutFile '%PY_TMP%'"
if errorlevel 1 (
    echo ERROR: Failed to download Python installer.
    goto :fallback_embed
)
echo Installing Python locally (per-user)...
"%PY_TMP%" /quiet InstallAllUsers=0 PrependPath=1 Include_test=0 Include_doc=0 Include_pip=1 Shortcuts=0
set "PYTHON_EXE=%LOCALAPPDATA%\Programs\Python\Python%PY_SHORT%\python.exe"
if not exist "%PYTHON_EXE%" (
    echo Installer failed or was blocked. Falling back to portable Python...
    goto :fallback_embed
)

:have_python
echo Using Python: %PYTHON_EXE%
goto :python_ready

:fallback_embed
echo [0/4] Using portable Python (no admin needed)...
echo Downloading embeddable zip...
powershell -NoProfile -Command "Invoke-WebRequest -Uri '%PY_EMBED_URL%' -OutFile '%PY_EMBED_ZIP%'"
if errorlevel 1 (
    echo ERROR: Failed to download embeddable Python.
    pause
    exit /b 1
)
echo Extracting to %PY_EMBED_DIR% ...
if not exist "%PY_EMBED_DIR%" mkdir "%PY_EMBED_DIR%"
powershell -NoProfile -Command "Expand-Archive -Force -Path '%PY_EMBED_ZIP%' -DestinationPath '%PY_EMBED_DIR%'"
set "PYTHON_EXE=%PY_EMBED_DIR%\python.exe"
if not exist "%PYTHON_EXE%" (
    echo ERROR: Portable python.exe not found after extract.
    pause
    exit /b 1
)
REM Enable site packages in embeddable: append 'import site'
powershell -NoProfile -Command "Add-Content -Path '%PY_EMBED_DIR%\python%PY_SHORT%._pth' -Value 'import site'"
REM Bootstrap pip
powershell -NoProfile -Command "Invoke-WebRequest -Uri 'https://bootstrap.pypa.io/get-pip.py' -OutFile '%PY_EMBED_DIR%\get-pip.py'"
"%PYTHON_EXE%" "%PY_EMBED_DIR%\get-pip.py" --no-warn-script-location --no-cache-dir
if errorlevel 1 (
    echo ERROR: Failed to bootstrap pip for portable Python.
    pause
    exit /b 1
)
set "USING_EMBED=1"

:python_ready

echo [1/4] Creating local directory: %LOCALDIR%
if not exist "%LOCALDIR%" mkdir "%LOCALDIR%"

echo [2/4] Creating Python virtual environment...
if "%USING_EMBED%"=="0" (
    if not exist "%VENVDIR%" (
        REM Handle py launcher vs full path
        if /i "%PYTHON_EXE:~0,2%"=="py" (
            %PYTHON_EXE% -m venv "%VENVDIR%"
        ) else (
            "%PYTHON_EXE%" -m venv "%VENVDIR%"
        )
        if errorlevel 1 (
            echo ERROR: Failed to create virtual environment
            pause
            exit /b 1
        )
    )
) else (
    echo Skipping venv creation (portable Python in use)
)

echo [3/4] Installing dependencies from network share...
if "%USING_EMBED%"=="0" (
    call "%VENVDIR%\Scripts\activate.bat"
    "%VENVDIR%\Scripts\python.exe" -m pip install --upgrade pip
    "%VENVDIR%\Scripts\python.exe" -m pip install -r "%TARGETUNC%\requirements.txt"
    if errorlevel 1 (
        echo ERROR: Failed to install dependencies
        pause
        exit /b 1
    )
) else (
    set "EMBED_SITE=%PY_EMBED_DIR%\site-packages"
    if not exist "%EMBED_SITE%" mkdir "%EMBED_SITE%"
    "%PYTHON_EXE%" -m pip install --upgrade pip --no-warn-script-location
    "%PYTHON_EXE%" -m pip install -r "%TARGETUNC%\requirements.txt" --target "%EMBED_SITE%" --no-warn-script-location
    if errorlevel 1 (
        echo ERROR: Failed to install dependencies into portable Python
        pause
        exit /b 1
    )
)

echo [4/4] Creating desktop shortcut...
set "DESKTOP_DIR="
for /f "delims=" %%D in ('powershell -NoProfile -Command "[Environment]::GetFolderPath(''Desktop'').Trim()"') do (
    set "DESKTOP_DIR=%%D"
)
if not defined DESKTOP_DIR (
    set "DESKTOP_DIR=%USERPROFILE%\Desktop"
)
if not exist "%DESKTOP_DIR%" (
    mkdir "%DESKTOP_DIR%" >nul 2>nul
)
if not exist "%DESKTOP_DIR%" (
    echo Warning: Desktop path "%DESKTOP_DIR%" not accessible. Falling back to %TEMP%.
    set "DESKTOP_DIR=%TEMP%"
)
set "LNK=%DESKTOP_DIR%\ASSUREDChain.lnk"
set "PWSH=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"

REM Create shortcut via helper script (handles desktop path + start_shared_server)
powershell -NoProfile -ExecutionPolicy Bypass -File "%TARGETUNC%\scripts\create_shortcut_unc.ps1" -ShortcutPath "%LNK%" -TargetUNC "%TARGETUNC%" -Port 8503 -PortTries 10

echo.
echo ========================================
echo Setup complete!
echo ========================================
echo.
if "%USING_EMBED%"=="0" (
    echo Local environment: %VENVDIR%
) else (
    echo Portable Python: %PY_EMBED_DIR%
)
echo Desktop shortcut: %LNK%
echo.
echo You can now double-click "ASSUREDChain.lnk" to start the app.
echo.
pause
