# Instala el agente en el PC del NIÑO. Ejecutar como administrador.
param(
    [string]$Servidor,            # IP del PC del padre, p. ej. 192.168.1.50
    [string]$Token,               # token que muestra el instalador del servidor
    [string]$Nombre = $env:COMPUTERNAME,
    [int]$Puerto = 8765
)
. "$PSScriptRoot\comun.ps1"
$Origen = Split-Path -Parent $PSScriptRoot
$Destino = "C:\ProgramData\ControlParental"
$Tarea = "ControlParental-Agente"

if (-not $Servidor) { $Servidor = Read-Host "IP del PC del padre (p. ej. 192.168.1.50)" }
if (-not $Token) { $Token = Read-Host "Token del servidor" }

Write-Host "== Instalando agente de control parental en $Destino ==" -ForegroundColor Cyan
if (Get-ScheduledTask -TaskName $Tarea -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask -TaskName $Tarea -ErrorAction SilentlyContinue
    Start-Sleep 2
}
$py = Find-Python
Write-Host "Python: $py"

New-Item -ItemType Directory -Force -Path $Destino | Out-Null
Copy-Item -Recurse -Force "$Origen\control_parental" $Destino
Copy-Item -Force "$Origen\run_agent.py" $Destino

if (-not (Test-Path "$Destino\venv\Scripts\pythonw.exe")) { & $py -m venv "$Destino\venv" }
& "$Destino\venv\Scripts\python.exe" -m pip install --quiet --upgrade psutil
if ($LASTEXITCODE -ne 0) { throw "No se pudo instalar psutil (¿hay internet?)" }

@{
    device           = $Nombre
    server_url       = "http://${Servidor}:$Puerto"
    token            = $Token
    interval_seconds = 5
    sync_seconds     = 30
    local_port       = 8766
    always_allowed_ips = @()
} | ConvertTo-Json | Set-Content -Path "$Destino\agente.json" -Encoding UTF8

Protect-Folder $Destino
Register-SystemTask $Tarea "$Destino\venv\Scripts\pythonw.exe" "`"$Destino\run_agent.py`" `"$Destino\agente.json`"" $Destino
New-UrlShortcut "Mi tiempo de pantalla" "http://127.0.0.1:8766/"

Start-Sleep 8
try {
    Invoke-RestMethod "http://127.0.0.1:8766/api/mode" | Out-Null
    Write-Host "`nAgente funcionando. Panel del nino: http://127.0.0.1:8766" -ForegroundColor Green
} catch {
    Write-Host "`nEl agente aun no responde. Revisa $Destino\agente.log" -ForegroundColor Yellow
}
Write-Host "IMPORTANTE: la cuenta de Windows del nino debe ser 'Usuario estandar' (no administrador)."
