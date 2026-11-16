# Funciones compartidas por los instaladores.
$ErrorActionPreference = "Stop"

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
