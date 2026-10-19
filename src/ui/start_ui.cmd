@echo off
rem Starts the manual-send UI (loopback only) and opens it in the default browser.
rem   start_ui.cmd            start (or tell you it is already running)
rem   start_ui.cmd --stop     stop the running UI
rem   start_ui.cmd --port N   use another port
setlocal EnableDelayedExpansion
set "HERE=%~dp0"
set "PY="
rem "python" may be the Microsoft Store alias: accept it only if it really runs Flask.
python -c "import flask" >nul 2>&1 && set "PY=python"
if not defined PY (
  py -3 -c "import flask" >nul 2>&1 && set "PY=py -3"
)
if not defined PY (
  for /d %%D in ("%LOCALAPPDATA%\Python\pythoncore-*" "%LOCALAPPDATA%\Programs\Python\Python3*") do (
    if not defined PY if exist "%%~D\python.exe" (
      "%%~D\python.exe" -c "import flask" >nul 2>&1 && set PY="%%~D\python.exe"
    )
  )
)
if not defined PY (
  echo Could not find a Python with Flask. Install it with:  python -m pip install flask
  pause
  exit /b 1
)
!PY! "%HERE%app.py" %*
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" if not "%~1"=="--stop" pause
exit /b %RC%
