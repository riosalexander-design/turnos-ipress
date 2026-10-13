@echo off
title Subir IPRESS a GitHub (riosalexander-design)
cd /d "%~dp0"
echo ========================================================
echo   PUBLICANDO PROYECTO IPRESS EN GITHUB
echo   Usuario: riosalexander-design
echo   Repositorio: turnos-ipress
echo ========================================================
echo.
echo 1. Verificando estado de Git...
git status -s
echo.
echo 2. Subiendo rama main a GitHub...
echo (Si te solicita iniciar sesion, haz clic en 'Sign in with your browser')
echo.
git push -u origin main
echo.
if %ERRORLEVEL% equ 0 (
    echo ========================================================
    echo   EXITO: Tu codigo ya esta publicado en:
    echo   https://github.com/riosalexander-design/turnos-ipress
    echo ========================================================
    echo.
    echo Siguiente paso:
    echo 1. Entra a https://share.streamlit.io
    echo 2. Clic en 'New app'
    echo 3. Selecciona: riosalexander-design/turnos-ipress
    echo 4. Archivo: streamlit_app.py
    echo 5. Clic en 'Deploy!'
) else (
    echo.
    echo Ocurrio un inconveniente al subir. Revisa tus credenciales de GitHub.
)
echo.
pause
