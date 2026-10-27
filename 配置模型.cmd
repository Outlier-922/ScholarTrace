@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv312\Scripts\python.exe" (
  echo 未找到项目 Python 环境，请先完成 README 中的环境安装。
  pause
  exit /b 2
)
".venv312\Scripts\python.exe" -X utf8 scripts\configure_model.py --provider deepseek
pause
