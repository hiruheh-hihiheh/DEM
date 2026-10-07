@echo off
rem Hydro Twin scenario runner (Windows wrapper).
rem Usage: scripts\run_scenario.bat chouldari [--dry-run|--force|--clean|--step X]
setlocal
python "%~dp0run_scenario.py" %*
exit /b %ERRORLEVEL%
