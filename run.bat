@echo off
REM Double-click this file to start AiDetector on Windows.
cd /d "%~dp0"

set PY=
where py >nul 2>nul && set PY=py
if not defined PY (where python >nul 2>nul && set PY=python)
if not defined PY goto nopython

if not exist .venv (
  echo First run: setting things up, this takes a minute...
  %PY% -m venv .venv || goto nopython
)
.venv\Scripts\python -m pip install -q --disable-pip-version-check -r requirements.txt || goto fail

echo.
echo AiDetector is running. Your browser will open at http://localhost:8000
echo Close this window to stop it.
echo.
start "" cmd /c "timeout /t 3 >nul & start http://localhost:8000"
.venv\Scripts\python -m uvicorn aidetector.main:app
pause
exit /b 0

:nopython
echo.
echo Python was not found.
echo Install it from https://www.python.org/downloads/
echo IMPORTANT: on the first installer screen tick "Add python.exe to PATH".
echo Then double-click run.bat again.
pause
exit /b 1

:fail
echo.
echo Installing the required packages failed. Check your internet connection and try again.
pause
exit /b 1
