@echo off
rem Abre el instalador con permisos de administrador.
powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process powershell -Verb RunAs -ArgumentList '-NoProfile -ExecutionPolicy Bypass -NoExit -File \"%~dp0instalacion\instalar_servidor.ps1\"'"
