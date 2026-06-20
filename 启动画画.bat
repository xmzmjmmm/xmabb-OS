@echo off
chcp 65001 >nul
python "%~dp0画画.py"
if %errorlevel% neq 0 pause
