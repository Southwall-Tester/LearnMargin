@echo off
cd /d "%~dp0"
where uv >nul 2>nul
if errorlevel 1 (
  echo Please install uv, then follow the README setup instructions.
  pause
  exit /b 1
)
uv run --frozen learnmargin --open
if errorlevel 1 pause
