$ErrorActionPreference = 'Stop'
$host.UI.RawUI.WindowTitle = 'Codex-Gateway | Minecraft status'
$projectRoot = if ($env:EVELYN_PROJECT_ROOT) { Resolve-Path $env:EVELYN_PROJECT_ROOT } else { Resolve-Path (Join-Path $PSScriptRoot '..\..\..') }
$coreRuntime = if ($env:EVELYN_CORE_RUNTIME) { Resolve-Path $env:EVELYN_CORE_RUNTIME } else { Resolve-Path (Join-Path $PSScriptRoot '..') }
$env:EVELYN_PROJECT_ROOT = [string]$projectRoot
$env:EVELYN_CORE_ROOT = Join-Path $projectRoot 'evelyn_core'
$env:EVELYN_CORE_RUNTIME = [string]$coreRuntime
$env:PYTHONPATH = if ($env:PYTHONPATH) { "$coreRuntime;$($env:PYTHONPATH)" } else { [string]$coreRuntime }
Set-Location $projectRoot

$pythonExe = if ($env:VOYAGER_CODEX_GATEWAY_PYTHON_EXE) { $env:VOYAGER_CODEX_GATEWAY_PYTHON_EXE } else { Join-Path $projectRoot '.venv-voyager\Scripts\python.exe' }
$hostName = if ($env:VOYAGER_CODEX_GATEWAY_HOST) { $env:VOYAGER_CODEX_GATEWAY_HOST } else { '127.0.0.1' }
$port = if ($env:VOYAGER_CODEX_GATEWAY_PORT) { [int]$env:VOYAGER_CODEX_GATEWAY_PORT } else { 8787 }
$mutexName = "Global\Evelyn-Codex-Gateway-$port"
$mutex = New-Object System.Threading.Mutex($false, $mutexName)
$lockTaken = $false

try {
    try {
        $lockTaken = $mutex.WaitOne(0, $false)
    } catch [System.Threading.AbandonedMutexException] {
        $lockTaken = $true
    }

    if (-not $lockTaken) {
        Write-Output "[Evelyn] Codex gateway already launching/running for port $port; skipping duplicate start."
        exit 0
    }

    $existingListener = Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($existingListener) {
        Write-Output "[Evelyn] Codex gateway already listening on port $port (PID $($existingListener.OwningProcess)); skipping duplicate start."
        exit 0
    }

    if (Test-Path $pythonExe) {
        & $pythonExe -m evelyn_core.codex_gateway_server --host $hostName --port $port
    } else {
        py -3 -m evelyn_core.codex_gateway_server --host $hostName --port $port
    }
}
finally {
    if ($lockTaken) {
        try { $mutex.ReleaseMutex() | Out-Null } catch {}
    }
    $mutex.Dispose()
}
