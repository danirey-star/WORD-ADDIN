# Instala el servidor en el PC del PADRE. Ejecutar como administrador.
param([int]$Puerto = 8765)
. "$PSScriptRoot\comun.ps1"
$Origen = Split-Path -Parent $PSScriptRoot
$Destino = "C:\ProgramData\ControlParentalServidor"
$Tarea = "ControlParental-Servidor"

Write-Host "== Instalando servidor de control parental en $Destino ==" -ForegroundColor Cyan
if (Get-ScheduledTask -TaskName $Tarea -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask -TaskName $Tarea -ErrorAction SilentlyContinue
    Start-Sleep 2
}
$py = Find-Python
$pyw = Join-Path (Split-Path $py) "pythonw.exe"
New-Item -ItemType Directory -Force -Path $Destino | Out-Null
Copy-Item -Recurse -Force "$Origen\control_parental" $Destino
Copy-Item -Force "$Origen\run_server.py" $Destino

$cfgPath = "$Destino\servidor.json"
if (-not (Test-Path $cfgPath)) {
    $bytes = New-Object byte[] 24
    [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    $tok = [Convert]::ToBase64String($bytes).Replace("+", "-").Replace("/", "_").TrimEnd("=")
    @{ port = $Puerto; token = $tok; db = "servidor.db" } | ConvertTo-Json | Set-Content $cfgPath -Encoding UTF8
}
$cfg = Get-Content $cfgPath -Raw | ConvertFrom-Json
Protect-Folder $Destino
Add-AntivirusExclusion $Destino

# Permitir conexiones entrantes solo desde la red local.
Get-NetFirewallRule -DisplayName "ControlParental Servidor" -ErrorAction SilentlyContinue | Remove-NetFirewallRule
New-NetFirewallRule -DisplayName "ControlParental Servidor" -Direction Inbound -Protocol TCP `
    -LocalPort $cfg.port -RemoteAddress LocalSubnet -Action Allow -Profile Any | Out-Null

Register-SystemTask $Tarea $pyw "`"$Destino\run_server.py`" `"$cfgPath`"" $Destino
New-UrlShortcut "Control parental" "http://127.0.0.1:$($cfg.port)/"

$ips = Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notmatch "^(127|169\.254)\." } | ForEach-Object IPAddress
Write-Host "`nServidor instalado. Panel: http://127.0.0.1:$($cfg.port)" -ForegroundColor Green
Write-Host "IP(s) de este PC: $($ips -join ', ')"
Write-Host "Token: $($cfg.token)" -ForegroundColor Yellow
Write-Host "`nEn el PC del nino ejecuta Instalar-Agente.bat e introduce la IP y el token anteriores."
Write-Host "Consejo: fija la IP de este PC en el router (reserva DHCP) para que no cambie."
Show-SecurityWarnings $Destino
