@echo off
title Lila Companion
if exist "D:\jarvis_project\start_lila_companion.bat" (
    cd /d "D:\jarvis_project"
) else if exist "C:\Users\Rishabh_Joshi\Downloads\jarvis_project\start_lila_companion.bat" (
    cd /d "C:\Users\Rishabh_Joshi\Downloads\jarvis_project"
) else (
    cd /d "%~dp0"
)
call "start_lila_companion.bat" ana
exit 0
