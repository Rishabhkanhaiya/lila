@echo off
cd /d "%~dp0"

:: Resolve pythonw.exe (Silent / No Terminal Bridge)
set "PYTHONW_EXE="
if exist "D:\jarvis_project\venv\Scripts\pythonw.exe" set "PYTHONW_EXE=D:\jarvis_project\venv\Scripts\pythonw.exe"
if "%PYTHONW_EXE%"=="" if exist "%~dp0venv\Scripts\pythonw.exe" set "PYTHONW_EXE=%~dp0venv\Scripts\pythonw.exe"
if "%PYTHONW_EXE%"=="" if exist "C:\Users\Rishabh_Joshi\AppData\Local\Programs\Python\Python311\pythonw.exe" set "PYTHONW_EXE=C:\Users\Rishabh_Joshi\AppData\Local\Programs\Python\Python311\pythonw.exe"
if "%PYTHONW_EXE%"=="" set "PYTHONW_EXE=pythonw"

:: Resolve python.exe (For cleanup script)
set "PYTHON_EXE="
if exist "D:\jarvis_project\venv\Scripts\python.exe" set "PYTHON_EXE=D:\jarvis_project\venv\Scripts\python.exe"
if "%PYTHON_EXE%"=="" if exist "%~dp0venv\Scripts\python.exe" set "PYTHON_EXE=%~dp0venv\Scripts\python.exe"
if "%PYTHON_EXE%"=="" if exist "C:\Users\Rishabh_Joshi\AppData\Local\Programs\Python\Python311\python.exe" set "PYTHON_EXE=C:\Users\Rishabh_Joshi\AppData\Local\Programs\Python\Python311\python.exe"
if "%PYTHON_EXE%"=="" set "PYTHON_EXE=python"

:: 1. Cleanup any stale processes (Zero terminal output)
"%PYTHON_EXE%" "core\cleanup_stale_processes.py" >nul 2>&1

:: 2. Launch Lila Companion Bridge silently (Zero Terminal Window)
start "" "%PYTHONW_EXE%" "run_companion_bridge.py"

:: Small pause for WebSocket state bridge initialization
timeout /t 1 /nobreak >nul 2>&1

:: 3. Resolve Target 3D Character Model
set "TARGET_MODEL=%~1"
if "%TARGET_MODEL%"=="" (
    if "%LILA_MODEL%"=="" (
        set "TARGET_MODEL=ana"
    ) else (
        set "TARGET_MODEL=%LILA_MODEL%"
    )
)
set "LILA_MODEL=%TARGET_MODEL%"

:: 4. Launch Electron 3D Overlay (Zero Terminal Window)
cd /d "%~dp0lila-overlay"
if exist "node_modules\electron\dist\electron.exe" (
    start "" "%~dp0lila-overlay\node_modules\electron\dist\electron.exe" . --model=%TARGET_MODEL%
) else (
    start "" npm start -- --model=%TARGET_MODEL%
)

exit 0
