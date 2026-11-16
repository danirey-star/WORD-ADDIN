# Instalador único de Control Parental. Ejecutar como administrador (lo hace Instalar.bat).
#   -Modo control      -> PC del padre (panel de control e histórico). Deja en el escritorio
#                         "Instalar cliente.bat": un solo archivo con todo lo que
#                         necesita el PC de tu hijo (programa, IP y token ya puestos).
#   -Modo desinstalar  -> quita de este PC lo que haya instalado
param(
    [ValidateSet("", "control", "desinstalar")] [string]$Modo = "",
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
# Python portatil oficial de python.org (no se instala en Windows: es solo una carpeta) y psutil.
$PyZipUrl = "https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip"
$PyZipSha = "4ACBED6DD1C744B0376E3B1CF57CE906F9DC9E95E68824584C8099A63025A3C3"
$PsutilUrl = "https://files.pythonhosted.org/packages/50/1b/6921afe68c74868b4c9fa424dad3be35b095e16687989ebbb50ce4fceb7c/psutil-7.0.0-cp37-abi3-win_amd64.whl"
$PsutilSha = "4CF3D4EB1AA9B348DEC30105C55CD9B7D4629285735A102BEB4441E38DB90553"

function Get-Descarga($url, $sha, $dest) {
    if ((Test-Path $dest) -and ((Get-FileHash $dest).Hash -eq $sha)) { return }
    Write-Host "Descargando $(Split-Path $url -Leaf)..."
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest $url -OutFile $dest -UseBasicParsing
    if ((Get-FileHash $dest).Hash -ne $sha) { Remove-Item $dest; throw "La descarga no coincide con la esperada: $url" }
}

function New-PythonPortable($dest, [switch]$ConPsutil) {
    $cache = "$DirControl\descargas"
    New-Item -ItemType Directory -Force -Path $cache | Out-Null
    Get-Descarga $PyZipUrl $PyZipSha "$cache\python-embed.zip"
    if (Test-Path $dest) { Remove-Item -Recurse -Force $dest }
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [IO.Compression.ZipFile]::ExtractToDirectory("$cache\python-embed.zip", $dest)
    if ($ConPsutil) {
        Get-Descarga $PsutilUrl $PsutilSha "$cache\psutil.whl"
        [IO.Compression.ZipFile]::ExtractToDirectory("$cache\psutil.whl", $dest)
    }
    # Rutas de Python: su biblioteca, su carpeta (psutil) y ".." (la carpeta del programa).
    Set-Content -Path "$dest\python312._pth" -Value "python312.zip`r`n.`r`n.." -Encoding ASCII
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
    New-Item -ItemType Directory -Force -Path $DirControl | Out-Null
    New-PythonPortable "$DirControl\python"
    $pyw = "$DirControl\python\pythonw.exe"
    Copy-Item -Recurse -Force "$Origen\control_parental" $DirControl
    # run_agent.py y ayudante.py: el servidor los reparte por red a los PCs de tus hijos (actualizacion automatica).
    Copy-Item -Force "$Origen\run_server.py", "$Origen\run_agent.py", "$Origen\ayudante.py" $DirControl

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

    $bat = New-InstaladorHijo $cfg
    Write-Host "`nControl instalado. Panel: http://127.0.0.1:$($cfg.port)" -ForegroundColor Green
    Write-Host "`nPara el PC de tu hijo tienes en el escritorio:" -ForegroundColor Yellow
    Write-Host "  $bat"
    Write-Host "Copialo a su PC (USB, red...) y dale doble clic. No hace falta nada mas."
    Write-Host "Lleva la clave de acceso dentro: borralo de su PC despues de instalar."
    Write-Host "Consejo: fija la IP de este PC en el router (reserva DHCP) para que no cambie."
    Show-SecurityWarnings $DirControl
}

function New-InstaladorHijo($cfg) {
    # Un solo .bat con el instalador del agente, el programa (zip en base64) y la IP y el token ya puestos.
    $ips = @(Get-NetIPConfiguration | Where-Object { $_.IPv4DefaultGateway -and $_.NetAdapter.Status -eq "Up" } |
        ForEach-Object { $_.IPv4Address.IPAddress })
    $ips += Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notmatch "^(127|169\.254)\." } | ForEach-Object IPAddress
    $ips = $ips | Where-Object { $_ } | Select-Object -Unique

    $tmp = Join-Path $env:TEMP ("cp_" + [guid]::NewGuid())
    New-Item -ItemType Directory -Path $tmp | Out-Null
    Copy-Item -Recurse "$Origen\control_parental" $tmp
    Copy-Item "$Origen\run_agent.py", "$Origen\ayudante.py" $tmp
    Get-ChildItem $tmp -Recurse -Directory -Filter __pycache__ | Remove-Item -Recurse -Force
    New-PythonPortable "$tmp\python" -ConPsutil
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [IO.Compression.ZipFile]::CreateFromDirectory($tmp, "$tmp.zip")
    $b64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes("$tmp.zip"), [Base64FormattingOptions]::InsertLineBreaks)
    Remove-Item -Recurse -Force $tmp, "$tmp.zip"

    $ps = (Get-Content "$Origen\agente_instalador.ps1" -Raw).Replace("__SERVIDORES__", ($ips -join ",")).
        Replace("__PUERTO__", [string]$cfg.port).Replace("__TOKEN__", $cfg.token)
    # Cabecera: pide administrador, extrae el bloque de PowerShell de este mismo archivo y lo ejecuta.
    $cab = @"
@echo off
rem Instalador de Control Parental para el PC de tu hijo. Solo hace falta este archivo.
set "CP_BAT=%~f0"
net session >nul 2>&1 || goto elevar
powershell -NoProfile -ExecutionPolicy Bypass -Command "`$t=[IO.File]::ReadAllText(`$env:CP_BAT); `$i=`$t.IndexOf('#'+'PS#')+4; iex `$t.Substring(`$i, `$t.IndexOf('#'+'ZIP#')-`$i)"
pause
exit /b
:elevar
powershell -NoProfile -Command "Start-Process -Verb RunAs -FilePath `$env:CP_BAT"
exit /b
"@
    $dest = Join-Path ([Environment]::GetFolderPath("Desktop")) "Instalar cliente.bat"
    [IO.File]::WriteAllText($dest, ($cab + "`r`n#PS#`r`n" + $ps + "`r`n#ZIP#`r`n" + $b64 + "`r`n"), [Text.Encoding]::ASCII)
    return $dest
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
        $h = "$env:SystemRoot\System32\drivers\etc\hosts"
        $t = [IO.File]::ReadAllText($h)
        $t2 = [regex]::Replace($t, "(?s)\r?\n?# >>> ControlParental.*?# <<< ControlParental\r?\n?", "`r`n")
        if ($t2 -ne $t) { [IO.File]::WriteAllText($h, $t2.TrimEnd() + "`r`n"); ipconfig /flushdns | Out-Null }
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
    Write-Host "  1) Instalar el CONTROL en este PC (el tuyo) y crear el instalador del PC de tu hijo"
    Write-Host "  2) Desinstalar de este PC"
    Write-Host "  3) Salir`n"
    switch (Read-Host "Elige 1, 2 o 3") {
        "1" { $Modo = "control" }
        "2" { $Modo = "desinstalar" }
        default { return }
    }
}
switch ($Modo) {
    "control" { Install-Control }
    "desinstalar" { Uninstall-Todo }
}
