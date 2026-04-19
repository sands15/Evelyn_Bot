param(
    [Parameter(Mandatory = $true)][string]$Name,
    [Parameter(Mandatory = $true)][string]$Command,
    [string]$Workdir = $PSScriptRoot,
    [int]$RestartDelaySec = 3
)

$ErrorActionPreference = 'Stop'

if ($env:SUPERVISOR_RESTART_DELAY_SEC) {
    try {
        $RestartDelaySec = [int]$env:SUPERVISOR_RESTART_DELAY_SEC
    } catch {
    }
}

Set-Location $Workdir
Write-Host "[Supervisor] $Name starting"

while ($true) {
    $startedAt = Get-Date
    Write-Host "[Supervisor] launching $Name at $($startedAt.ToString('s'))"
    try {
        & powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -Command $Command
        $exitCode = $LASTEXITCODE
    } catch {
        Write-Host "[Supervisor] $Name crashed: $($_.Exception.Message)"
        $exitCode = 1
    }

    if ($exitCode -eq 0) {
        Write-Host "[Supervisor] $Name exited cleanly"
        break
    }

    Write-Host "[Supervisor] $Name exited with code $exitCode, restarting in ${RestartDelaySec}s"
    Start-Sleep -Seconds $RestartDelaySec
}
