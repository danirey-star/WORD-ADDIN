# Instalador único de Control Parental. Ejecutar como administrador (lo hace Instalar.bat).
#   -Modo control      -> PC del padre (panel de control e histórico)
#   -Modo agente       -> PC del niño (medición y bloqueos)
#   -Modo desinstalar  -> quita de este PC lo que haya instalado
param(
    [ValidateSet("", "control", "agente", "desinstalar")] [string]$Modo = "",
    [string]$Servidor,            # (agente) IP del PC del padre, p. ej. 192.168.1.50
    [string]$Token,               # (agente) token que muestra la instalación del control
    [string]$Nombre = $env:COMPUTERNAME,
    [int]$Puerto = 8765
)
$ErrorActionPreference = "Stop"
$Origen = $PSScriptRoot
$DirAgente = "C:\ProgramData\ControlParental"
$DirControl = "C:\ProgramData\ControlParentalServidor"
$TareaAgente = "ControlParental-Agente"
$TareaControl = "ControlParental-Servidor"
$ReglaEntrada = "ControlParental Servidor"

# ------------------------------------------------------------------ utilidades
function Find-Python {
    $cands = @()
    try { $cands += (& py -3 -c "import sys; print(sys.executable)" 2>$null) } catch {}
    try { $cands += (Get-Command python -All -ErrorAction SilentlyContinue | ForEach-Object Source) } catch {}
    $cands += Get-ChildItem "$env:ProgramFiles\Python3*\python.exe" -ErrorAction SilentlyContinue | ForEach-Object FullName
    foreach ($c in $cands) {
        # El "python.exe" de WindowsApps es solo un acceso a la Microsoft Store.
        if ($c -and (Test-Path $c) -and ($c -notmatch "WindowsApps")) {
            $v = & $c -c "import sys; print(sys.version_info >= (3, 10))" 2>$null
            if ($v -eq "True") { return $c }
        }
    }
    Write-Host "No se ha encontrado Python 3.10 o superior. Instalando con winget..." -ForegroundColor Yellow
    winget install -e --id Python.Python.3.12 --scope machine --silent --accept-package-agreements --accept-source-agreements
    $p = Get-ChildItem "$env:ProgramFiles\Python3*\python.exe" -ErrorAction SilentlyContinue | Select-Object -Last 1
    if (-not $p) { throw "No se pudo instalar Python. Instalalo desde python.org (marca 'Install for all users') y repite." }
    return $p.FullName
}

function Stop-Tarea($name) {
    if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
        Stop-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
        Start-Sleep 2
    }
}

function Protect-Folder($path) {
    # Solo SYSTEM y Administradores pueden modificar; los usuarios normales solo leen.
    icacls $path /inheritance:r /grant:r "*S-1-5-18:(OI)(CI)F" "*S-1-5-32-544:(OI)(CI)F" "*S-1-5-32-545:(OI)(CI)RX" | Out-Null
}

function Register-SystemTask($name, $exe, $arguments, $workdir) {
    $action = New-ScheduledTaskAction -Execute $exe -Argument $arguments -WorkingDirectory $workdir
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
        -StartWhenAvailable -MultipleInstances IgnoreNew
    Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
    Start-ScheduledTask -TaskName $name
}

function New-UrlShortcut($name, $url) {
    Set-Content -Path (Join-Path "$env:PUBLIC\Desktop" "$name.url") -Value "[InternetShortcut]`r`nURL=$url" -Encoding ASCII
}

function Add-AntivirusExclusion($path) {
    # Evita que Microsoft Defender analice o ponga en cuarentena la carpeta del programa.
    try {
        Add-MpPreference -ExclusionPath $path -ErrorAction Stop
        Write-Host "Carpeta excluida del analisis de Microsoft Defender: $path"
    } catch {
        Write-Host "No se pudo anadir la exclusion en Microsoft Defender (puede estar gestionado por otro antivirus)." -ForegroundColor Yellow
    }
}

function Show-SecurityWarnings($path) {
    # Avisa si hay un antivirus o firewall de otra marca que pueda interferir.
    try {
        $av = Get-CimInstance -Namespace root/SecurityCenter2 -ClassName AntiVirusProduct -ErrorAction Stop |
            Where-Object { $_.displayName -notmatch "Defender" } | ForEach-Object displayName | Select-Object -Unique
        if ($av) {
            Write-Host "`nAVISO: antivirus de otra marca detectado: $($av -join ', ')" -ForegroundColor Yellow
            Write-Host "  Anade a mano esta carpeta a sus exclusiones: $path"
        }
        $fw = Get-CimInstance -Namespace root/SecurityCenter2 -ClassName FirewallProduct -ErrorAction Stop |
            ForEach-Object displayName | Select-Object -Unique
        if ($fw) {
            Write-Host "AVISO: firewall de otra marca detectado: $($fw -join ', ')" -ForegroundColor Yellow
            Write-Host "  Si sustituye al Firewall de Windows, el bloqueo de internet puede no funcionar y habra que"
            Write-Host "  permitir en el el puerto 8765 (PC del padre). Los bloqueos de juegos y apps no dependen de el."
        }
    } catch {}
}

# ------------------------------------------------------------------ control (PC del padre)
function Install-Control {
    Write-Host "`n== Instalando el CONTROL (PC del padre) en $DirControl ==" -ForegroundColor Cyan
    Stop-Tarea $TareaControl
    $py = Find-Python
    $pyw = Join-Path (Split-Path $py) "pythonw.exe"
    New-Item -ItemType Directory -Force -Path $DirControl | Out-Null
    Copy-Item -Recurse -Force "$Origen\control_parental" $DirControl
    Copy-Item -Force "$Origen\run_server.py" $DirControl

    $cfgPath = "$DirControl\servidor.json"
    if (-not (Test-Path $cfgPath)) {
        $bytes = New-Object byte[] 24
        [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
        $tok = [Convert]::ToBase64String($bytes).Replace("+", "-").Replace("/", "_").TrimEnd("=")
        @{ port = $Puerto; token = $tok; db = "servidor.db" } | ConvertTo-Json | Set-Content $cfgPath -Encoding UTF8
    }
    $cfg = Get-Content $cfgPath -Raw | ConvertFrom-Json
    Protect-Folder $DirControl
    Add-AntivirusExclusion $DirControl

    # Permitir conexiones entrantes solo desde la red local.
    Get-NetFirewallRule -DisplayName $ReglaEntrada -ErrorAction SilentlyContinue | Remove-NetFirewallRule
    New-NetFirewallRule -DisplayName $ReglaEntrada -Direction Inbound -Protocol TCP `
        -LocalPort $cfg.port -RemoteAddress LocalSubnet -Action Allow -Profile Any | Out-Null

    Register-SystemTask $TareaControl $pyw "`"$DirControl\run_server.py`" `"$cfgPath`"" $DirControl
    New-UrlShortcut "Control parental" "http://127.0.0.1:$($cfg.port)/"

    $ips = Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notmatch "^(127|169\.254)\." } | ForEach-Object IPAddress
    Write-Host "`nControl instalado. Panel: http://127.0.0.1:$($cfg.port)" -ForegroundColor Green
    Write-Host "IP(s) de este PC: $($ips -join ', ')"
    Write-Host "Token: $($cfg.token)" -ForegroundColor Yellow
    Write-Host "`nEn el PC del nino ejecuta Instalar.bat, elige 'Agente' e introduce la IP y el token anteriores."
    Write-Host "Consejo: fija la IP de este PC en el router (reserva DHCP) para que no cambie."
    Show-SecurityWarnings $DirControl
}

# ------------------------------------------------------------------ agente (PC del niño)
function Install-Agente {
    $ip = $Servidor
    $tok = $Token
    if (-not $ip) { $ip = Read-Host "IP del PC del padre (p. ej. 192.168.1.50)" }
    if (-not $tok) { $tok = Read-Host "Token (lo muestra la instalacion del control)" }

    Write-Host "`n== Instalando el AGENTE (PC del nino) en $DirAgente ==" -ForegroundColor Cyan
    Stop-Tarea $TareaAgente
    $py = Find-Python
    New-Item -ItemType Directory -Force -Path $DirAgente | Out-Null
    Copy-Item -Recurse -Force "$Origen\control_parental" $DirAgente
    Copy-Item -Force "$Origen\run_agent.py" $DirAgente

    if (-not (Test-Path "$DirAgente\venv\Scripts\pythonw.exe")) { & $py -m venv "$DirAgente\venv" }
    & "$DirAgente\venv\Scripts\python.exe" -m pip install --quiet --upgrade psutil
    if ($LASTEXITCODE -ne 0) { throw "No se pudo instalar psutil (hace falta internet durante la instalacion)" }

    @{
        device             = $Nombre
        server_url         = "http://${ip}:$Puerto"
        token              = $tok
        interval_seconds   = 5
        sync_seconds       = 30
        local_port         = 8766
        always_allowed_ips = @()
    } | ConvertTo-Json | Set-Content -Path "$DirAgente\agente.json" -Encoding UTF8

    Protect-Folder $DirAgente
    Add-AntivirusExclusion $DirAgente
    Register-SystemTask $TareaAgente "$DirAgente\venv\Scripts\pythonw.exe" "`"$DirAgente\run_agent.py`" `"$DirAgente\agente.json`"" $DirAgente
    New-UrlShortcut "Mi tiempo de pantalla" "http://127.0.0.1:8766/"

    Start-Sleep 8
    try {
        Invoke-RestMethod "http://127.0.0.1:8766/api/mode" | Out-Null
        Write-Host "`nAgente funcionando. Panel del nino: http://127.0.0.1:8766" -ForegroundColor Green
    } catch {
        Write-Host "`nEl agente aun no responde. Revisa $DirAgente\agente.log" -ForegroundColor Yellow
    }
    Write-Host "IMPORTANTE: la cuenta de Windows del nino debe ser 'Usuario estandar' (no administrador)."
    Show-SecurityWarnings $DirAgente
}

# ------------------------------------------------------------------ desinstalar
function Uninstall-Todo {
    $ErrorActionPreference = "Continue"
    $algo = $false
    if ((Test-Path $DirAgente) -or (Get-ScheduledTask -TaskName $TareaAgente -ErrorAction SilentlyContinue)) {
        $algo = $true
        Stop-ScheduledTask -TaskName $TareaAgente -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $TareaAgente -Confirm:$false -ErrorAction SilentlyContinue
        Get-Process pythonw, python -ErrorAction SilentlyContinue | Where-Object { $_.Path -like "$DirAgente\*" } | Stop-Process -Force
        netsh advfirewall firewall delete rule name="ControlParental - Bloqueo internet IPv4" | Out-Null
        netsh advfirewall firewall delete rule name="ControlParental - Bloqueo internet IPv6" | Out-Null
        Start-Sleep 1
        Remove-Item -Recurse -Force $DirAgente -ErrorAction SilentlyContinue
        try { Remove-MpPreference -ExclusionPath $DirAgente -ErrorAction Stop } catch {}
        Remove-Item -Force "$env:PUBLIC\Desktop\Mi tiempo de pantalla.url" -ErrorAction SilentlyContinue
        Write-Host "Agente desinstalado e internet desbloqueado." -ForegroundColor Green
    }
    if ((Test-Path $DirControl) -or (Get-ScheduledTask -TaskName $TareaControl -ErrorAction SilentlyContinue)) {
        $algo = $true
        Stop-ScheduledTask -TaskName $TareaControl -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $TareaControl -Confirm:$false -ErrorAction SilentlyContinue
        Get-NetFirewallRule -DisplayName $ReglaEntrada -ErrorAction SilentlyContinue | Remove-NetFirewallRule
        Remove-Item -Force "$env:PUBLIC\Desktop\Control parental.url" -ErrorAction SilentlyContinue
        try { Remove-MpPreference -ExclusionPath $DirControl -ErrorAction Stop } catch {}
        $r = Read-Host "Borrar tambien el historico de uso y la configuracion guardados en este PC? (s/N)"
        if ($r -match "^[sS]") {
            Start-Sleep 1
            Remove-Item -Recurse -Force $DirControl -ErrorAction SilentlyContinue
            Write-Host "Control desinstalado y datos borrados." -ForegroundColor Green
        } else {
            Write-Host "Control desinstalado. Los datos siguen en $DirControl" -ForegroundColor Green
        }
    }
    if (-not $algo) { Write-Host "No hay nada de Control Parental instalado en este PC." }
}

# ------------------------------------------------------------------ menú
if (-not $Modo) {
    Write-Host "CONTROL PARENTAL - Instalador" -ForegroundColor Cyan
    Write-Host "Que quieres hacer en ESTE ordenador?`n"
    Write-Host "  1) Instalar el CONTROL   (PC del padre: panel, historico y bloqueos)"
    Write-Host "  2) Instalar el AGENTE    (PC del nino: mide el uso y aplica los bloqueos)"
    Write-Host "  3) Desinstalar de este PC"
    Write-Host "  4) Salir`n"
    switch (Read-Host "Elige 1, 2, 3 o 4") {
        "1" { $Modo = "control" }
        "2" { $Modo = "agente" }
        "3" { $Modo = "desinstalar" }
        default { return }
    }
}
switch ($Modo) {
    "control" { Install-Control }
    "agente" { Install-Agente }
    "desinstalar" { Uninstall-Todo }
}
