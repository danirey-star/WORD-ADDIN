@echo off
rem Instalador unico de Control Parental (control, agente y desinstalacion). Pide permisos de administrador.
powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process powershell -Verb RunAs -ArgumentList '-NoProfile -ExecutionPolicy Bypass -NoExit -File \"%~dp0instalar.ps1\"'"
