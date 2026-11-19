@echo off
REM BASE Design Office - double-click to start. First run installs Python packages into .venv
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Setting up for the first time...
  py -3 -m venv .venv || python -m venv .venv
  .venv\Scripts\python.exe -m pip install --upgrade pip
  .venv\Scripts\python.exe -m pip install -r requirements.txt
)
.venv\Scripts\python.exe run.py %*
pause
