param(
    [string]$PythonExecutable = "",
    [switch]$Recreate
)

$ErrorActionPreference = 'Stop'

$projectRoot = [string](Resolve-Path (Join-Path $PSScriptRoot '..\..\..'))
$runtimeRoot = Join-Path $projectRoot 'evelyn_core\runtime'
$requirementsPath = Join-Path $projectRoot 'requirements.host.lock'
$venvRoot = Join-Path $projectRoot '.venv-host'
$venvPython = Join-Path $venvRoot 'Scripts\python.exe'

if (-not (Test-Path -LiteralPath $requirementsPath -PathType Leaf)) {
    throw "Host runtime lock file not found: $requirementsPath"
}

function Test-Python311 {
    param(
        [string]$FilePath,
        [string[]]$PrefixArguments = @()
    )

    if ([string]::IsNullOrWhiteSpace($FilePath) -or -not (Test-Path -LiteralPath $FilePath -PathType Leaf)) {
        return $false
    }
    try {
        $version = & $FilePath @PrefixArguments -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
        return $LASTEXITCODE -eq 0 -and ([string]$version).Trim() -eq '3.11'
    } catch {
        return $false
    }
}

function Resolve-BootstrapPython {
    $candidates = New-Object System.Collections.Generic.List[object]
    if (-not [string]::IsNullOrWhiteSpace($PythonExecutable)) {
        $candidates.Add([PSCustomObject]@{ FilePath = $PythonExecutable; PrefixArguments = @() })
    }
    if (-not [string]::IsNullOrWhiteSpace($env:EVELYN_HOST_BOOTSTRAP_PYTHON)) {
        $candidates.Add([PSCustomObject]@{
            FilePath = [string]$env:EVELYN_HOST_BOOTSTRAP_PYTHON
            PrefixArguments = @()
        })
    }
    if (-not [string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
        $candidates.Add([PSCustomObject]@{
            FilePath = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python311\python.exe'
            PrefixArguments = @()
        })
    }
    if (-not [string]::IsNullOrWhiteSpace($env:ProgramFiles)) {
        $candidates.Add([PSCustomObject]@{
            FilePath = Join-Path $env:ProgramFiles 'Python311\python.exe'
            PrefixArguments = @()
        })
    }
    $pyLauncher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($pyLauncher) {
        $candidates.Add([PSCustomObject]@{
            FilePath = [string]$pyLauncher.Source
            PrefixArguments = @('-3.11')
        })
    }

    foreach ($candidate in $candidates) {
        if (Test-Python311 -FilePath $candidate.FilePath -PrefixArguments $candidate.PrefixArguments) {
            return $candidate
        }
    }
    throw (
        "Python 3.11 was not found. Install Python 3.11 or pass " +
        "-PythonExecutable <path-to-python.exe>."
    )
}

if ($Recreate -and (Test-Path -LiteralPath $venvRoot)) {
    $resolvedProject = [System.IO.Path]::GetFullPath($projectRoot)
    $resolvedVenv = [System.IO.Path]::GetFullPath($venvRoot)
    if (-not $resolvedVenv.StartsWith($resolvedProject + [System.IO.Path]::DirectorySeparatorChar)) {
        throw "Refusing to remove host runtime outside the project root: $resolvedVenv"
    }
    Remove-Item -LiteralPath $resolvedVenv -Recurse -Force
}

if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
    $bootstrap = Resolve-BootstrapPython
    Write-Host "[Evelyn] Creating isolated Windows host runtime at $venvRoot"
    & $bootstrap.FilePath @($bootstrap.PrefixArguments) -m venv $venvRoot
    if ($LASTEXITCODE -ne 0) {
        throw "Python failed to create the Windows host runtime."
    }
}

Write-Host '[Evelyn] Installing locked Windows Host Supervisor dependencies.'
& $venvPython -m pip install --disable-pip-version-check --no-input --requirement $requirementsPath
if ($LASTEXITCODE -ne 0) {
    throw "Windows host dependency installation failed."
}

$previousPythonPath = $env:PYTHONPATH
try {
    $env:PYTHONPATH = $runtimeRoot
    & $venvPython -c "import aiohttp, numpy, sounddevice; import evelyn_core.local_io_bridge; print('host_runtime_ready')"
    if ($LASTEXITCODE -ne 0) {
        throw "Windows host runtime import verification failed."
    }
} finally {
    if ($null -eq $previousPythonPath) {
        Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
    } else {
        $env:PYTHONPATH = $previousPythonPath
    }
}

Write-Host "[Evelyn] Windows host runtime is ready: $venvPython"
