@echo off
title ORION ENGINE
cd /d "%~dp0"
echo Iniciando Orion Engine...
start "" http://localhost:8787
python server.py
pause
