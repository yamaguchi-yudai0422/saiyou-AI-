@echo off
setlocal
cd /d "%~dp0"

set "PYTHON=.venv\Scripts\python.exe"
if not exist "%PYTHON%" set "PYTHON=python"
set "CONFIG_BACKUP=%~dp0.release-config-backup.json"

if exist "release\NoesiaHR\config.json" (
  copy /y "release\NoesiaHR\config.json" "%CONFIG_BACKUP%" >nul
  if errorlevel 1 (
    echo Failed to back up release config.json.
    exit /b 1
  )
)

"%PYTHON%" -m PyInstaller ^
  --noconfirm ^
  --clean ^
  --onedir ^
  --windowed ^
  --name "NoesiaHR" ^
  --distpath "release" ^
  --workpath "build" ^
  --specpath "build" ^
  --contents-directory "_internal" ^
  --add-data "%CD%\static;static" ^
  --collect-all "faster_whisper" ^
  --collect-all "ctranslate2" ^
  --collect-all "pyaudiowpatch" ^
  --collect-all "speech_recognition" ^
  --collect-all "pymupdf" ^
  --collect-all "winrt" ^
  --hidden-import "winrt.windows.globalization" ^
  --hidden-import "winrt.windows.graphics.directx" ^
  --hidden-import "winrt.windows.graphics.imaging" ^
  --hidden-import "winrt.windows.media.ocr" ^
  --hidden-import "winrt.windows.storage.streams" ^
  --hidden-import "winrt.windows.system" ^
  --collect-all "nvidia.cublas" ^
  --collect-all "nvidia.cudnn" ^
  --collect-all "nvidia.cuda_nvrtc" ^
  main.py

set "BUILD_FAILED=0"
if errorlevel 1 set "BUILD_FAILED=1"

if exist "%CONFIG_BACKUP%" (
  if not exist "release\NoesiaHR" mkdir "release\NoesiaHR"
  copy /y "%CONFIG_BACKUP%" "release\NoesiaHR\config.json" >nul
  del /q "%CONFIG_BACKUP%"
)

if "%BUILD_FAILED%"=="1" (
  echo.
  echo Build failed.
  exit /b 1
)

copy /y "%~dp0NoesiaHR.exe.config" "release\NoesiaHR\NoesiaHR.exe.config" >nul
if errorlevel 1 (
  echo Failed to copy NoesiaHR.exe.config.
  exit /b 1
)

copy /y "%~dp0STARTUP_HELP.txt" "release\NoesiaHR\STARTUP_HELP.txt" >nul
if errorlevel 1 (
  echo Failed to copy startup instructions.
  exit /b 1
)

echo.
echo Build complete: release\NoesiaHR\NoesiaHR.exe
echo Share the whole release\NoesiaHR folder.
echo API key and audio settings are saved locally in config.json after first launch.
exit /b 0
