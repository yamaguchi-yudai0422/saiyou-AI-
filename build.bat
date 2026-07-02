@echo off
pyinstaller ^
  --onedir ^
  --windowed ^
  --name "NoesiaHR" ^
  --add-data "static;static" ^
  main.py
echo.
echo Build complete. Share the dist\NoesiaHR folder.
echo API key and audio settings are saved locally in config.json after first launch.
pause
