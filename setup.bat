@echo off
setlocal

:: Store script directory
set "SCRIPT_DIR=%~dp0"

echo Checking for Conda installation...

IF EXIST "%USERPROFILE%\anaconda3\Scripts\conda.exe" (
    set "CONDA_PATH=%USERPROFILE%\anaconda3"
    echo Found Anaconda at %CONDA_PATH%
) ELSE IF EXIST "%USERPROFILE%\miniconda3\Scripts\conda.exe" (
    set "CONDA_PATH=%USERPROFILE%\miniconda3"
    echo Found Miniconda at %CONDA_PATH%
) ELSE (
    echo Conda not found. Installing Miniconda...

    powershell -Command "Invoke-WebRequest https://repo.anaconda.com/miniconda/Miniconda3-latest-Windows-x86_64.exe -OutFile miniconda.exe"

    start /wait "" miniconda.exe /InstallationType=JustMe /AddToPath=1 /S /D=%USERPROFILE%\miniconda3

    set "CONDA_PATH=%USERPROFILE%\miniconda3"
)

echo Using Conda at: %CONDA_PATH%

:: Initialize conda
CALL "%CONDA_PATH%\Scripts\activate.bat"
IF %ERRORLEVEL% NEQ 0 (
    echo Failed to initialize Conda!
    pause
    exit /b
)

echo Checking if environment exists...

conda env list | findstr "gemma_swarm" >nul
IF %ERRORLEVEL% NEQ 0 (
    echo Creating environment gemma_swarm...
    conda create -y -n gemma_swarm python=3.11

    IF %ERRORLEVEL% NEQ 0 (
        echo Failed to create environment!
        pause
        exit /b
    )
) ELSE (
    echo Environment already exists. Skipping creation.
)

echo Activating environment...

CALL "%CONDA_PATH%\Scripts\activate.bat"
CALL conda activate gemma_swarm

IF %ERRORLEVEL% NEQ 0 (
    echo Failed to activate environment!
    pause
    exit /b
)

echo Installing requirements...

pip install -r "%SCRIPT_DIR%requirements.txt"

IF %ERRORLEVEL% NEQ 0 (
    echo Failed to install requirements!
    pause
    exit /b
)

:: ── gemma_test environment (coding agent sandbox) ─────────────────────────────
echo.
echo Checking if gemma_test environment exists...

conda env list | findstr "gemma_test" >nul
IF %ERRORLEVEL% NEQ 0 (
    echo Creating environment gemma_test...
    conda create -y -n gemma_test python=3.11

    IF %ERRORLEVEL% NEQ 0 (
        echo Failed to create gemma_test environment!
        pause
        exit /b
    )
) ELSE (
    echo gemma_test environment already exists. Skipping creation.
)

echo Installing coding agent dependencies into gemma_test...
conda run -n gemma_test pip install pytest ruff flake8 mypy magika

IF %ERRORLEVEL% NEQ 0 (
    echo Failed to install coding agent dependencies!
    pause
    exit /b
)

echo gemma_test environment ready.

:: ── Node.js (required for JS/TS project validation) ─────────────────────────────────────────
echo.
echo Checking for Node.js installation...

where node >nul 2>&1
IF %ERRORLEVEL% NEQ 0 (
    echo Node.js not found. Installing Node.js LTS via winget...
    winget install OpenJS.NodeJS.LTS --accept-source-agreements --accept-package-agreements
    IF %ERRORLEVEL% NEQ 0 (
        echo WARNING: Node.js installation failed. JS/TS validation will not be available.
        echo You can install it manually from https://nodejs.org/
    ) ELSE (
        echo Node.js installed. You may need to restart your terminal for PATH to update.
        echo Installing TypeScript and eslint globally...
        npm install -g typescript eslint
    )
) ELSE (
    echo Node.js already installed. Skipping.
    where tsc >nul 2>&1
    IF %ERRORLEVEL% NEQ 0 (
        echo TypeScript compiler not found. Installing TypeScript and eslint globally...
        npm install -g typescript eslint
    ) ELSE (
        echo TypeScript compiler already installed. Skipping.
    )
)

:: ── TS Analysis Bridge (ts-morph for semantic JS/TS analysis) ──────────────────
echo.
echo Installing ts-morph for JS/TS semantic analysis bridge...
IF EXIST "%SCRIPT_DIR%tools\ts_analysis_bridge\package.json" (
    pushd "%SCRIPT_DIR%tools\ts_analysis_bridge"
    npm install --prefer-offline
    IF %ERRORLEVEL% NEQ 0 (
        echo WARNING: ts-morph installation failed. JS/TS semantic analysis will not be available.
    ) ELSE (
        echo ts-morph bridge ready.
    )
    popd
) ELSE (
    echo WARNING: ts_analysis_bridge not found, skipping.
)

echo Creating gemma-swarm.bat...

(
echo @echo off
echo echo Starting Gemma Swarm...
echo CALL "%CONDA_PATH%\Scripts\activate.bat" gemma_swarm
echo cd /d "%SCRIPT_DIR%"
echo python slack_app.py
echo pause
) > "%SCRIPT_DIR%gemma-swarm.bat"

echo Creating desktop shortcut...

set "SHORTCUT=%USERPROFILE%\Desktop\Gemma-Swarm.lnk"
set "TARGET=%SCRIPT_DIR%gemma-swarm.bat"
set "ICON=%SCRIPT_DIR%gemma_swarm.ico"

powershell -Command ^
"$s=(New-Object -COM WScript.Shell).CreateShortcut('%SHORTCUT%'); ^
$s.TargetPath='%TARGET%'; ^
$s.IconLocation='%ICON%'; ^
$s.Save()"

echo.
echo ===============================
echo Setup completed successfully!
echo ===============================
echo.

pause