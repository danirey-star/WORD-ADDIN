# Desinstala el agente del PC del niño. Ejecutar como administrador.
$ErrorActionPreference = "Continue"
$Tarea = "ControlParental-Agente"
Stop-ScheduledTask -TaskName $Tarea -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName $Tarea -Confirm:$false -ErrorAction SilentlyContinue
Get-Process pythonw -ErrorAction SilentlyContinue | Where-Object { $_.Path -like "C:\ProgramData\ControlParental\*" } | Stop-Process -Force
netsh advfirewall firewall delete rule name="ControlParental - Bloqueo internet IPv4" | Out-Null
netsh advfirewall firewall delete rule name="ControlParental - Bloqueo internet IPv6" | Out-Null
Remove-Item -Recurse -Force "C:\ProgramData\ControlParental" -ErrorAction SilentlyContinue
Remove-Item -Force "$env:PUBLIC\Desktop\Mi tiempo de pantalla.url" -ErrorAction SilentlyContinue
Write-Host "Agente desinstalado e internet desbloqueado." -ForegroundColor Green
