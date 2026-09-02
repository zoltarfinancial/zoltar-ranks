@echo off
REM Double-click this to refresh the console's data and open it in your browser.
REM
REM All it does is call dashboard\refresh_and_open.ps1 with the execution
REM policy bypassed for this one process, so an unsigned .ps1 in the repo
REM doesn't need any machine-wide policy change.
REM
REM   Open Dashboard.bat            refresh, then open (file://)
REM   Open Dashboard.bat -Serve     refresh, then start the local server
REM   Open Dashboard.bat -NoOpen    refresh only

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0dashboard\refresh_and_open.ps1" %*
if errorlevel 1 (
  echo.
  echo The build monitor emit failed - see the messages above.
  pause
)
