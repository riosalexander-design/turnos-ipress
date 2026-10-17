@echo off
title Subir IPRESS a GitHub (riosalexander-design)
cd /d "%~dp0"
set "PATH=%LOCALAPPDATA%\Programs\Git\cmd;%LOCALAPPDATA%\Programs\Git\gcm;%PATH%"
set "GCM_INTERACTIVE=always"
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
git -c credential.helper="%LOCALAPPDATA%/Programs/Git/gcm/git-credential-manager.exe" push -u origin main
echo.
if %ERRORLEVEL% equ 0 (
    echo ========================================================
    echo   EXITO: Tu codigo ya esta publicado en:
    echo   https://github.com/riosalexander-design/turnos-ipress
    echo   Y en Streamlit: https://turnos-ipress.streamlit.app/
    echo ========================================================
) else (
    echo.
    echo Ocurrio un inconveniente al subir. Revisa tus credenciales de GitHub.
)
echo.
pause
