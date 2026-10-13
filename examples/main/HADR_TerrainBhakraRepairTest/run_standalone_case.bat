@echo off
setlocal

cd /d "%~dp0"

rem Read the generated case name from this case's config so the runner uses the
rem correct definition XML and output directory for any site (not just
rem HADR_TerrainChouldari).
set CASE_NAME=
if exist "terrain_case_config.json" (
    for /f "tokens=2 delims=:, " %%a in ('findstr /c:"case_name" "terrain_case_config.json"') do set CASE_NAME=%%~a
)
if "%CASE_NAME%"=="" set CASE_NAME=HADR_TerrainChouldari

set DEF_BASE=%CASE_NAME%_Def
set OUT_DIR=%CASE_NAME%_out

if "%DUALSPHYSICS_ROOT%"=="" (
    echo ERROR: DUALSPHYSICS_ROOT environment variable is not set.
    exit /b 1
)

set BIN_DIR=%DUALSPHYSICS_ROOT%\bin\windows

if not exist "%DEF_BASE%.xml" (
    echo ERROR: %DEF_BASE%.xml not found.
    echo Run generate_terrain_case.py first.
    exit /b 1
)

if not exist "%BIN_DIR%\GenCase_win64.exe" (
    echo ERROR: GenCase_win64.exe not found in %BIN_DIR%
    exit /b 1
)

if not exist "%BIN_DIR%\DualSPHysics5.4_win64.exe" (
    echo ERROR: DualSPHysics5.4_win64.exe not found in %BIN_DIR%
    exit /b 1
)

if not exist "%BIN_DIR%\PartVTK_win64.exe" (
    echo ERROR: PartVTK_win64.exe not found in %BIN_DIR%
    exit /b 1
)

if exist "%OUT_DIR%" (
    echo Removing old %OUT_DIR%
    rmdir /s /q "%OUT_DIR%"
)

echo.
echo Running GenCase...
"%BIN_DIR%\GenCase_win64.exe" "%DEF_BASE%" "%OUT_DIR%\%CASE_NAME%" -save:all
if errorlevel 1 (
    echo ERROR: GenCase failed.
    exit /b 1
)

if not exist "%OUT_DIR%\%CASE_NAME%.xml" (
    echo ERROR: GenCase did not produce %OUT_DIR%\%CASE_NAME%.xml
    exit /b 1
)

echo.
echo Running DualSPHysics GPU solver...
"%BIN_DIR%\DualSPHysics5.4_win64.exe" -gpu "%OUT_DIR%\%CASE_NAME%" "%OUT_DIR%"
if errorlevel 1 (
    echo ERROR: DualSPHysics solver failed.
    exit /b 1
)

if not exist "%OUT_DIR%\data" (
    echo ERROR: Solver did not produce %OUT_DIR%\data
    exit /b 1
)

echo.
echo Running PartVTK...
if not exist "%OUT_DIR%\particles" mkdir "%OUT_DIR%\particles"

"%BIN_DIR%\PartVTK_win64.exe" -dirdata "%OUT_DIR%\data" -savevtk "%OUT_DIR%\particles\PartFluid" -onlytype:-all,fluid -vars:+idp,+vel,+rhop,+press,+vor,+energy
if errorlevel 1 (
    echo ERROR: PartVTK failed.
    exit /b 1
)

echo.
echo Terrain integration case completed.
echo Output directory: %CD%\%OUT_DIR%
echo VTK particles:    %CD%\%OUT_DIR%\particles

endlocal