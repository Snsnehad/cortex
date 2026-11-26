@echo off
rem Runs scripts\dev.ps1 regardless of the machine's PowerShell execution policy.
rem The bypass applies only to this one PowerShell process; no system setting is changed.
rem Usage: scripts\dev.cmd <command> [options]    (see: scripts\dev.cmd help)
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0dev.ps1" %*
exit /b %ERRORLEVEL%
