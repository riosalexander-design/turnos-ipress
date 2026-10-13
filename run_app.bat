@echo off
title IPRESS - Sistema de Programacion de Turnos Medicos
echo ========================================================
echo   IPRESS - SISTEMA DE PROGRAMACION ASISTENCIAL MEDICA
echo ========================================================
echo   Iniciando servidor web en http://localhost:5000 ...
echo   Presione Ctrl+C para detener el servidor.
echo ========================================================
cd /d "%~dp0"
start http://localhost:5000
python app.py
pause
