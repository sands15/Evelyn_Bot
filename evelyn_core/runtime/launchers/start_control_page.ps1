$ErrorActionPreference = 'Stop'

$projectRoot = if ($env:EVELYN_PROJECT_ROOT) { Resolve-Path $env:EVELYN_PROJECT_ROOT } else { Resolve-Path (Join-Path $PSScriptRoot '..\..\..') }
$coreRuntime = if ($env:EVELYN_CORE_RUNTIME) { Resolve-Path $env:EVELYN_CORE_RUNTIME } else { Resolve-Path (Join-Path $PSScriptRoot '..') }

$env:EVELYN_PROJECT_ROOT = [string]$projectRoot
$env:EVELYN_CORE_ROOT = Join-Path $projectRoot 'evelyn_core'
$env:EVELYN_CORE_RUNTIME = [string]$coreRuntime
$env:PYTHONPATH = if ($env:PYTHONPATH) { "$coreRuntime;$($env:PYTHONPATH)" } else { [string]$coreRuntime }
$env:CONTROL_PAGE_PUBLIC_PORT = if ($env:CONTROL_PAGE_PUBLIC_PORT) { $env:CONTROL_PAGE_PUBLIC_PORT } else { '8799' }
$env:CONTROL_PAGE_BOT_API_PORT = if ($env:CONTROL_PAGE_BOT_API_PORT) { $env:CONTROL_PAGE_BOT_API_PORT } else { '8798' }

Set-Location $projectRoot

$venvPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (Test-Path $venvPython) {
    & $venvPython '-m' 'evelyn_core.control_page_server'
} else {
    py -3 -m 'evelyn_core.control_page_server'
}
