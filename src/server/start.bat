@echo off
setlocal
REM Windstock -- double-click to start the server on Windows.
REM   1. asks for Administrator rights (ports 53, 80 and 443 need them)
REM   2. installs the one Python dependency (s2sphere)
REM   3. opens the Windows Firewall for the phone (once; the rules are kept)
REM   4. starts the server -- it generates its TLS certificate on first start
REM      and prints the address to use on the phone
REM Optional: pass the IP the phone should point at, e.g.  start.bat 192.168.1.50
cd /d "%~dp0"

net session >nul 2>&1
if %errorlevel% neq 0 (
  echo Asking for Administrator rights -- ports 53, 80 and 443 need them.
  if "%~1"=="" (
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs -WorkingDirectory '%~dp0'"
  ) else (
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -ArgumentList '%*' -Verb RunAs -WorkingDirectory '%~dp0'"
  )
  exit /b
)

REM The python.org installer provides "py"; the Microsoft Store one only "python".
set "PY="
where py >nul 2>&1 && set "PY=py -3"
if not defined PY where python >nul 2>&1 && set "PY=python"
if not defined PY (
  echo.
  echo Python 3 was not found. Install it from https://www.python.org/downloads/
  echo and tick "Add python.exe to PATH" in the installer, then run this again.
  pause
  exit /b 1
)
%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)" >nul 2>&1
if %errorlevel% neq 0 (
  echo.
  echo Python 3.8 or newer is needed ^(found an older one, or the Store stub^).
  echo Install it from https://www.python.org/downloads/ and run this again.
  pause
  exit /b 1
)

echo Installing the Python dependency ...
%PY% -m pip install --quiet --disable-pip-version-check -r "%~dp0..\..\requirements.txt"
if %errorlevel% neq 0 (
  echo.
  echo pip could not install the requirements. Check the internet connection and
  echo run:  %PY% -m pip install -r "%~dp0..\..\requirements.txt"
  pause
  exit /b 1
)

echo Opening the Windows Firewall for the phone ^(udp 53, tcp 443, tcp 80^) ...
netsh advfirewall firewall show rule name="Windstock DNS (udp 53)" >nul 2>&1 || netsh advfirewall firewall add rule name="Windstock DNS (udp 53)" dir=in action=allow protocol=UDP localport=53 >nul
netsh advfirewall firewall show rule name="Windstock game (tcp 443)" >nul 2>&1 || netsh advfirewall firewall add rule name="Windstock game (tcp 443)" dir=in action=allow protocol=TCP localport=443 >nul
netsh advfirewall firewall show rule name="Windstock setup page (tcp 80)" >nul 2>&1 || netsh advfirewall firewall add rule name="Windstock setup page (tcp 80)" dir=in action=allow protocol=TCP localport=80 >nul

echo.
%PY% run.py %*
echo.
pause
