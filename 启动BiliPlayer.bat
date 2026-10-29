@echo off
rem BiliPlayer launcher (Windows).
rem Finds any Python 3 on this computer, then hands over to launch.py,
rem which starts the local server and opens the app window.
cd /d "%~dp0"

rem 1) pythonw on PATH - runs without a console window, the common case
for %%C in (pythonw.exe) do if not "%%~$PATH:C"=="" (
  start "" "%%~$PATH:C" "launch.py"
  exit /b 0
)

rem 2) the py launcher (python.org installer) - ask it where pythonw.exe lives
where py >nul 2>nul
if not errorlevel 1 (
  for /f "usebackq delims=" %%P in (`py -3 -c "import sys,os;print(os.path.join(sys.base_prefix,'pythonw.exe'))" 2^>nul`) do (
    if exist "%%P" (
      start "" "%%P" "launch.py"
      exit /b 0
    )
  )
  start "" /min py -3 "launch.py"
  exit /b 0
)

rem 3) scan the usual install folders, whatever 3.x version they hold
set "PF=%ProgramFiles%"
set "PF86=%ProgramFiles(x86)%"
for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do (
  if exist "%%~fD\pythonw.exe" (
    start "" "%%~fD\pythonw.exe" "launch.py"
    exit /b 0
  )
)
for /d %%D in ("%PF%\Python3*") do (
  if exist "%%~fD\pythonw.exe" (
    start "" "%%~fD\pythonw.exe" "launch.py"
    exit /b 0
  )
)
for /d %%D in ("%PF86%\Python3*") do (
  if exist "%%~fD\pythonw.exe" (
    start "" "%%~fD\pythonw.exe" "launch.py"
    exit /b 0
  )
)

rem 4) portables that bring their own python (this build was made on one)
for /d %%D in ("%USERPROFILE%\.workbuddy\binaries\python\versions\*") do (
  if exist "%%~fD\pythonw.exe" (
    start "" "%%~fD\pythonw.exe" "launch.py"
    exit /b 0
  )
)

rem 5) plain python on PATH - a console flashes for a second, harmless
where python >nul 2>nul
if not errorlevel 1 (
  start "" /min python "launch.py"
  exit /b 0
)

echo.
echo  BiliPlayer could not start: no Python 3 found on this computer.
echo.
echo  1. Install Python from https://www.python.org/downloads/
echo  2. During setup tick "Add python.exe to PATH"
echo  3. Run this file again
echo.
pause
exit /b 1
