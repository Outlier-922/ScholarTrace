@echo off
chcp 65001 >nul
pushd "%~dp0"
if not exist ".venv312\Scripts\python.exe" (
  echo Python environment missing. Follow README first.
  pause
  popd
  exit /b 1
)
".venv312\Scripts\python.exe" -X utf8 scripts\serve_demo.py
pause
popd
