# Plantilla del instalador del AGENTE (PC de tu hijo).
# No se ejecuta directamente: al instalar el control, instalar.ps1 la mete dentro de
# "Instalar cliente.bat" junto con el programa y rellena IP, puerto y token.
# Solo ASCII: el .bat generado se guarda en ASCII.

$ErrorActionPreference = "Stop"
$Servidores = "__SERVIDORES__".Split(",")
$Puerto = __PUERTO__
$Token = "__TOKEN__"
$DirAgente = "C:\ProgramData\ControlParental"
$TareaAgente = "ControlParental-Agente"

function Find-Servidor {
    # Prueba las IPs del PC del padre y se queda con la que responde.
    foreach ($ip in $Servidores) {
        $c = New-Object Net.Sockets.TcpClient
        try { if ($c.ConnectAsync($ip, $Puerto).Wait(1500) -and $c.Connected) { return $ip } } catch {} finally { $c.Close() }
    }
    Write-Host "No se encuentra el PC del padre (debe estar encendido y en la misma red)." -ForegroundColor Yellow
    $r = Read-Host "IP del PC del padre (Enter para usar $($Servidores[0]))"
    if ($r) { return $r.Trim() }
    return $Servidores[0]
}

function Unpack-Programa($dest) {
    # El programa va en base64 al final de este mismo .bat.
    $t = [IO.File]::ReadAllText($env:CP_BAT)
    $b64 = $t.Substring($t.IndexOf("#" + "ZIP#") + 5) -replace "\s", ""
    $zip = Join-Path $env:TEMP ("cp_" + [guid]::NewGuid() + ".zip")
    [IO.File]::WriteAllBytes($zip, [Convert]::FromBase64String($b64))
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $tmp = "$zip.dir"
    [IO.Compression.ZipFile]::ExtractToDirectory($zip, $tmp)
    Copy-Item -Recurse -Force "$tmp\*" $dest
    Remove-Item -Recurse -Force $zip, $tmp
}

function Install-Agente {
    $ip = Find-Servidor
    Write-Host "`n== Instalando el agente en $DirAgente (servidor $ip) ==" -ForegroundColor Cyan
    if (Get-ScheduledTask -TaskName $TareaAgente -ErrorAction SilentlyContinue) {
        Stop-ScheduledTask -TaskName $TareaAgente -ErrorAction SilentlyContinue
    }
    Get-Process pythonw, python -ErrorAction SilentlyContinue | Where-Object { $_.Path -like "$DirAgente\*" } | Stop-Process -Force
    Start-Sleep 2
    # Todo va dentro de este archivo, Python incluido (portatil, en la carpeta del programa).
    Remove-Item -Recurse -Force "$DirAgente\venv", "$DirAgente\python" -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force -Path $DirAgente | Out-Null
    Unpack-Programa $DirAgente

    @{
        device             = $env:COMPUTERNAME
        server_url         = "http://${ip}:$Puerto"
        token              = $Token
        interval_seconds   = 5
        sync_seconds       = 30
        local_port         = 8766
        always_allowed_ips = @()
    } | ConvertTo-Json | Set-Content -Path "$DirAgente\agente.json" -Encoding UTF8

    # Solo SYSTEM y Administradores pueden modificar; los usuarios normales solo leen.
    icacls $DirAgente /inheritance:r /grant:r "*S-1-5-18:(OI)(CI)F" "*S-1-5-32-544:(OI)(CI)F" "*S-1-5-32-545:(OI)(CI)RX" | Out-Null
    try { Add-MpPreference -ExclusionPath $DirAgente -ErrorAction Stop } catch {
        Write-Host "No se pudo excluir la carpeta en Microsoft Defender (puede haber otro antivirus)." -ForegroundColor Yellow
    }

    $action = New-ScheduledTaskAction -Execute "$DirAgente\python\pythonw.exe" `
        -Argument "`"$DirAgente\run_agent.py`" `"$DirAgente\agente.json`"" -WorkingDirectory $DirAgente
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
        -StartWhenAvailable -MultipleInstances IgnoreNew
    Register-ScheduledTask -TaskName $TareaAgente -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
    Start-ScheduledTask -TaskName $TareaAgente
    Set-Content -Path "$env:PUBLIC\Desktop\Mi tiempo de pantalla.url" -Value "[InternetShortcut]`r`nURL=http://127.0.0.1:8766/" -Encoding ASCII

    Start-Sleep 8
    try {
        Invoke-RestMethod "http://127.0.0.1:8766/api/mode" | Out-Null
        Write-Host "`nAgente funcionando. En menos de un minuto aparece en tu panel de control." -ForegroundColor Green
    } catch {
        Write-Host "`nEl agente aun no responde. Revisa $DirAgente\agente.log" -ForegroundColor Yellow
    }
    Write-Host "IMPORTANTE: la cuenta de Windows de tu hijo debe ser 'Usuario estandar' (no administrador)."
    try {
        $av = Get-CimInstance -Namespace root/SecurityCenter2 -ClassName AntiVirusProduct -ErrorAction Stop |
            Where-Object { $_.displayName -notmatch "Defender" } | ForEach-Object displayName | Select-Object -Unique
        if ($av) { Write-Host "AVISO: antivirus $($av -join ', '): anade a mano $DirAgente a sus exclusiones." -ForegroundColor Yellow }
    } catch {}
}

function Uninstall-Agente {
    $ErrorActionPreference = "Continue"
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

try {
    Write-Host "CONTROL PARENTAL - Instalador del PC de tu hijo" -ForegroundColor Cyan
    if ((Test-Path $DirAgente) -or (Get-ScheduledTask -TaskName $TareaAgente -ErrorAction SilentlyContinue)) {
        Write-Host "`nYa esta instalado en este PC.`n  1) Actualizar / reinstalar`n  2) Desinstalar`n  3) Salir`n"
        switch (Read-Host "Elige 1, 2 o 3") {
            "1" { Install-Agente }
            "2" { Uninstall-Agente }
        }
    } else {
        Install-Agente
    }
} catch {
    Write-Host "`nERROR: $($_.Exception.Message)" -ForegroundColor Red
}
