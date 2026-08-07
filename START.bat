@echo off
title PsychApp
cd /d "%~dp0"

echo.
echo  ========================================
echo   PsychApp - arranque
echo  ========================================
echo.

REM Start Docker Desktop if needed
docker version >nul 2>&1
if errorlevel 1 (
  echo [1/3] Arrancando Docker Desktop...
  start "" "C:\Program Files\Docker\Docker\Docker Desktop.exe"
  echo       Esperando a que Docker este listo (puede tardar 1-2 min)...
  :waitdocker
  timeout /t 5 /nobreak >nul
  docker version >nul 2>&1
  if errorlevel 1 goto waitdocker
)

echo [2/3] Docker OK. Levantando PsychApp...
docker compose up -d --remove-orphans
if errorlevel 1 (
  echo ERROR al arrancar. Revisa que Docker Desktop este en marcha.
  pause
  exit /b 1
)

echo [3/3] Esperando health...
timeout /t 8 /nobreak >nul

echo.
echo  ========================================
echo   Abre en el PC:
echo     http://localhost:5173
echo.
echo   En el movil (misma Wi-Fi):
echo     http://192.168.1.213:5173
echo.
echo   Login demo:
echo     patient@demo.psychapp.example.com
echo     DemoPass123!
echo.
echo   IMPORTANTE: deja Docker Desktop abierto.
echo  ========================================
echo.

start http://localhost:5173
pause
