@echo off
chcp 65001 >nul
if not exist "%~dp0data\stage12_review\review.html" (
  echo 尚未生成复核页，请参照 docs\HUMAN_REVIEW.md 使用对应原运行生成。
  pause
  exit /b 1
)
start "" "%~dp0data\stage12_review\review.html"
