@echo off
cd /d "%~dp0"
python "%~dp0song_tui.py" %*
if errorlevel 1 (
  echo.
  echo Launch failed. Install dependencies: python -m pip install -r requirements.txt
  pause
)
