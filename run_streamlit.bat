@echo off
title IPRESS - Sistema de Programacion de Turnos (Streamlit)
cd /d "%~dp0"
echo ========================================================
echo   IPRESS SALUD PERU - SISTEMA DE PROGRAMACION ASISTENCIAL
echo                 MODO STREAMLIT WEB
echo ========================================================
echo.
echo Iniciando servidor Streamlit...
streamlit run streamlit_app.py
pause
