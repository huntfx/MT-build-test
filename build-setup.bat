@echo off
:: Check the virtual environment exists
if not exist ".venv" (
    echo Virtual Environment does not exist.
    echo Please create it first with `python -m venv .venv` using Python 3.11 or later.
    pause
    exit /b 1
)

:: Enter the virtual environment
call .venv\Scripts\activate

:: Retrieve the version
set PYTHON_COMMAND_TO_GET_VERSION="from mousetracks2 import __version__; print(__version__)"
for /f "delims=" %%V in ('python -c %PYTHON_COMMAND_TO_GET_VERSION% 2^>nul') do set VERSION=%%V

if not defined VERSION (
    echo Failed to detect version. Exiting.
    exit /b 1
)

set "SOURCE_BASE_NAME=dist\MouseTracks-%VERSION%-windows-x64"
set "DEST_BASE_NAME=dist\MouseTracks-%VERSION%-windows-x64-setup"

:: Inno Setup Installer Build
:: 1. Check if ISCC is already on the PATH
where ISCC >nul 2>nul
if %errorlevel% equ 0 set "ISCC_PATH=ISCC"
:: 2. Search Program Files (x86) for "Inno Setup*"
if not defined ISCC_PATH (
    for /d %%D in ("%ProgramFiles(x86)%\Inno Setup*") do (
        if exist "%%D\ISCC.exe" set "ISCC_PATH=%%D\ISCC.exe"
    )
)
:: 3. Search Program Files for "Inno Setup*"
if not defined ISCC_PATH (
    for /d %%D in ("%ProgramFiles%\Inno Setup*") do (
        if exist "%%D\ISCC.exe" set "ISCC_PATH=%%D\ISCC.exe"
    )
)
:: 4. Run or Warn
if defined ISCC_PATH (
    echo --- Building Installer using "%ISCC_PATH%" ---
    "%ISCC_PATH%" /DMyAppVersion="%VERSION%" /DMySourceBaseName="%SOURCE_BASE_NAME%" /DMyDestinationBaseName="%DEST_BASE_NAME%" "MouseTracks.iss"
    if errorlevel 1 (
        echo Warning: Installer creation failed.
    ) else (
        echo Installer created successfully.
    )
) else (
    echo -------------------------------------------------------------------
    echo WARNING: Inno Setup Compiler ^(ISCC.exe^) was not found.
    echo.
    echo The main executable was built successfully in the "dist" folder.
    echo However, the installer could not be generated.
    echo.
    echo Please ensure Inno Setup is installed or added to your system PATH.
    echo https://jrsoftware.org/isdl.php
    echo -------------------------------------------------------------------
)

:: Exit the virtual environment
call deactivate
