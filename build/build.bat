@echo off
setlocal
title Scarlet hub - build
cd /d "%~dp0.."

rem ==========================================================================
rem  Scarlet hub - release build
rem  Run from the repository: build\build.bat
rem  Result: dist\ScarletHub\ScarletHub.exe  +  ScarletHub-<ver>.zip  + .sha256
rem
rem  Why it is built this way (fewer antivirus false positives):
rem   --onedir   a folder instead of a single self-extracting exe. Onefile
rem              builds unpack themselves into %TEMP% at start - the same
rem              thing droppers do, so heuristics flag them much more often.
rem   --noupx    UPX-packed exes look like packed malware to scanners.
rem   version    file properties (name, company, version) - an exe with empty
rem              metadata looks suspicious.
rem   clean venv only the packages the macro needs, nothing extra inside.
rem  Optional: set BUILD_BOOTLOADER=1 to compile PyInstaller's launcher from
rem  source (needs "Visual Studio Build Tools" with C++). The stock launcher
rem  is shared by thousands of programs, including malware, so a locally built
rem  one is flagged less. Use it if the normal build still gets detections.
rem ==========================================================================

set NAME=ScarletHub
set VER=1.0

for %%F in (scarlet_hub.py assets\scarlet.ico build\version_info.txt) do (
    if not exist "%%F" (
        echo [!] %%F not found
        pause & exit /b 1
    )
)

where python >nul 2>nul
if errorlevel 1 (
    echo [!] Python not found. Install Python 3.10+ and tick "Add to PATH".
    pause & exit /b 1
)

echo [1/4] Clean build environment...
if not exist .venv-build python -m venv .venv-build
call .venv-build\Scripts\activate.bat
python -m pip install --upgrade pip >nul
python -m pip install mss numpy pyautogui pynput pillow >nul
if errorlevel 1 (echo [!] pip install failed & pause & exit /b 1)
rem dxcam is optional: without it the macro simply stays on mss
python -m pip install dxcam >nul 2>nul

if "%BUILD_BOOTLOADER%"=="1" (
    echo       compiling PyInstaller launcher from source...
    set PYINSTALLER_COMPILE_BOOTLOADER=1
    python -m pip install --force-reinstall --no-binary pyinstaller pyinstaller
) else (
    python -m pip install pyinstaller >nul
)

echo [2/4] Building %NAME% %VER%...
if exist .pyi-work rmdir /s /q .pyi-work
if exist dist rmdir /s /q dist
if exist %NAME%.spec del /q %NAME%.spec
pyinstaller --noconfirm --clean --noconsole --onedir --noupx ^
    --workpath .pyi-work --distpath dist ^
    --name %NAME% ^
    --icon assets\scarlet.ico ^
    --version-file build\version_info.txt ^
    --hidden-import pynput.keyboard._win32 ^
    --hidden-import pynput.mouse._win32 ^
    --hidden-import PIL._tkinter_finder ^
    scarlet_hub.py
if errorlevel 1 (echo [!] Build failed - see messages above & pause & exit /b 1)

echo [3/4] Zip for the release...
if exist %NAME%-%VER%.zip del /q %NAME%-%VER%.zip
powershell -NoProfile -Command "Compress-Archive -Path 'dist\%NAME%' -DestinationPath '%NAME%-%VER%.zip'"

echo [4/4] SHA-256 (publish it next to the download)...
certutil -hashfile %NAME%-%VER%.zip SHA256 | findstr /v ":" > %NAME%-%VER%.sha256.txt
type %NAME%-%VER%.sha256.txt

echo.
echo Done:  dist\%NAME%\%NAME%.exe
echo        %NAME%-%VER%.zip
echo Check the zip on virustotal.com before publishing.
pause
