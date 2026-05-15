$ErrorActionPreference = 'Stop'
$host.UI.RawUI.WindowTitle = 'Voyager-Service | Minecraft status'
$projectRoot = if ($env:EVELYN_PROJECT_ROOT) { Resolve-Path $env:EVELYN_PROJECT_ROOT } else { Resolve-Path (Join-Path $PSScriptRoot '..\..\..') }
$coreRuntime = if ($env:EVELYN_CORE_RUNTIME) { Resolve-Path $env:EVELYN_CORE_RUNTIME } else { Resolve-Path (Join-Path $PSScriptRoot '..') }
$env:EVELYN_PROJECT_ROOT = [string]$projectRoot
$env:EVELYN_CORE_ROOT = Join-Path $projectRoot 'evelyn_core'
$env:EVELYN_CORE_RUNTIME = [string]$coreRuntime
$env:PYTHONPATH = if ($env:PYTHONPATH) { "$coreRuntime;$($env:PYTHONPATH)" } else { [string]$coreRuntime }
Set-Location $projectRoot

$pythonExe = if ($env:VOYAGER_PYTHON_EXE) { $env:VOYAGER_PYTHON_EXE } else { Join-Path $projectRoot '.venv-voyager\Scripts\python.exe' }
$hostName = if ($env:MINECRAFT_AUTONOMY_SERVICE_HOST) { $env:MINECRAFT_AUTONOMY_SERVICE_HOST } else { '127.0.0.1' }
$port = if ($env:MINECRAFT_AUTONOMY_SERVICE_PORT) { [int]$env:MINECRAFT_AUTONOMY_SERVICE_PORT } else { 8765 }
$mutexName = "Global\Evelyn-Voyager-Service-$port"
$mutex = New-Object System.Threading.Mutex($false, $mutexName)
$lockTaken = $false

try {
    try {
        $lockTaken = $mutex.WaitOne(0, $false)
    } catch [System.Threading.AbandonedMutexException] {
        $lockTaken = $true
    }

    if (-not $lockTaken) {
        Write-Output "[Evelyn] Voyager service already launching/running for port $port; skipping duplicate start."
        exit 0
    }

    $existingListener = Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($existingListener) {
        Write-Output "[Evelyn] Voyager service already listening on port $port (PID $($existingListener.OwningProcess)); skipping duplicate start."
        exit 0
    }

    if (Test-Path $pythonExe) {
        & $pythonExe -m evelyn_core.voyager_service --host $hostName --port $port
    } else {
        py -3 -m evelyn_core.voyager_service --host $hostName --port $port
    }
}
finally {
    if ($lockTaken) {
        try { $mutex.ReleaseMutex() | Out-Null } catch {}
    }
    $mutex.Dispose()
}
