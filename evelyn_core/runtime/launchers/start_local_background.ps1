$ErrorActionPreference = 'Stop'

$projectRoot = if ($env:EVELYN_PROJECT_ROOT) { Resolve-Path $env:EVELYN_PROJECT_ROOT } else { Resolve-Path (Join-Path $PSScriptRoot '..\..\..') }
$projectRoot = [string]$projectRoot
$coreRuntime = Join-Path $projectRoot 'evelyn_core\runtime'
$composeFile = Join-Path $projectRoot 'docker-compose.fast-control.yml'
$controlPagePublicPort = if ($env:CONTROL_PAGE_PUBLIC_PORT) { [int]$env:CONTROL_PAGE_PUBLIC_PORT } else { 8799 }
$botApiPort = if ($env:CONTROL_PAGE_BOT_API_PORT) { [int]$env:CONTROL_PAGE_BOT_API_PORT } else { 8798 }
$controlPageUrl = "http://127.0.0.1:$controlPagePublicPort/"
$stopMarker = Join-Path $projectRoot '.evelyn_stop_requested'
$logDir = Join-Path $projectRoot 'runtime_artifacts\logs\background_start'
$bridgeLog = Join-Path $logDir 'Local-IO-Bridge.log'

$env:CONTROL_PAGE_PUBLIC_PORT = [string]$controlPagePublicPort
$env:CONTROL_PAGE_BOT_API_PORT = [string]$botApiPort

if (Test-Path $stopMarker) {
    Remove-Item -LiteralPath $stopMarker -Force -ErrorAction SilentlyContinue
}
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

function Test-PortConnect {
    param(
        [string]$HostName = '127.0.0.1',
        [int]$Port,
        [int]$TimeoutMs = 1000
    )

    $client = $null
    try {
        $client = [System.Net.Sockets.TcpClient]::new()
        $iar = $client.BeginConnect($HostName, $Port, $null, $null)
        if ($iar.AsyncWaitHandle.WaitOne($TimeoutMs)) {
            $client.EndConnect($iar)
            return $true
        }
    } catch {
    } finally {
        if ($client) {
            $client.Close()
        }
    }
    return $false
}

function Wait-Port {
    param(
        [string]$HostName,
        [int]$Port,
        [string]$Label
    )

    $timeoutSec = if ($env:START_WAIT_TIMEOUT_SEC) { [int]$env:START_WAIT_TIMEOUT_SEC } else { 180 }
    $intervalMs = if ($env:START_WAIT_INTERVAL_SEC) { [int]$env:START_WAIT_INTERVAL_SEC * 1000 } else { 2000 }
    $deadline = (Get-Date).AddSeconds($timeoutSec)

    Write-Host "[Evelyn] Waiting for $Label at ${HostName}:$Port"
    while ((Get-Date) -lt $deadline) {
        if (Test-PortConnect -HostName $HostName -Port $Port -TimeoutMs 1000) {
            Write-Host "[Evelyn] $Label is ready"
            return
        }
        Start-Sleep -Milliseconds $intervalMs
    }

    throw "$Label was not ready in time"
}

function New-EncodedCommand {
    param([string]$Script)
    $bytes = [System.Text.Encoding]::Unicode.GetBytes($Script)
    return [Convert]::ToBase64String($bytes)
}

function Start-PowerShellWindow {
    param(
        [string]$Title,
        [string]$Script,
        [bool]$Visible = $true
    )

    $bootstrap = @"
try { `$Host.UI.RawUI.WindowTitle = '$Title' } catch {}
$Script
"@
    $encoded = New-EncodedCommand -Script $bootstrap
    $windowStyle = if ($Visible) { 'Normal' } else { 'Hidden' }
    Start-Process -FilePath 'powershell.exe' -WorkingDirectory $projectRoot -WindowStyle $windowStyle -ArgumentList @(
        '-NoLogo',
        '-NoProfile',
        '-ExecutionPolicy', 'Bypass',
        '-EncodedCommand', $encoded
    ) | Out-Null
}

function Invoke-DockerCommand {
    param(
        [string[]]$Arguments,
        [switch]$IgnoreFailure
    )

    $previousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $output = & docker @Arguments 2>&1
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }

    if ($output) {
        $output | ForEach-Object { Write-Host $_ }
    }
    if ($exitCode -ne 0 -and -not $IgnoreFailure) {
        throw "docker $($Arguments -join ' ') failed with exit code $exitCode"
    }
}

function Start-DockerCore {
    if (-not (Test-Path -LiteralPath $composeFile)) {
        throw "Compose file not found: $composeFile"
    }

    Write-Host '[Evelyn] Starting Docker local core services.'
    $composeBaseArgs = @(
        '-f', $composeFile,
        '--profile', 'llm',
        '--profile', 'tts',
        '--profile', 'vision',
        '--profile', 'stt',
        '--profile', 'voyager'
    )
    $coreServices = @(
        'bot_api',
        'control_page',
        'main_llm',
        'router_llm',
        'sub_llm',
        'tts',
        'stt',
        'vision',
        'codex_gateway',
        'voyager'
    )

    $buildEnabled = $env:EVELYN_DOCKER_BUILD -and ([string]$env:EVELYN_DOCKER_BUILD).ToLowerInvariant() -in @('1', 'true', 'yes', 'on')
    if ($buildEnabled) {
        Write-Host '[Evelyn] Rebuilding Docker app images because EVELYN_DOCKER_BUILD is enabled.'
        Invoke-DockerCommand -Arguments (@('compose') + $composeBaseArgs + @('build', 'bot_api', 'control_page', 'codex_gateway', 'voyager'))
    } else {
        Write-Host '[Evelyn] Reusing existing Docker images. Set EVELYN_DOCKER_BUILD=true to rebuild app images.'
    }

    $keepDiscordBot = $env:EVELYN_LOCAL_KEEP_DISCORD_BOT -and ([string]$env:EVELYN_LOCAL_KEEP_DISCORD_BOT).ToLowerInvariant() -in @('1', 'true', 'yes', 'on')
    if (-not $keepDiscordBot) {
        Invoke-DockerCommand -Arguments (@('compose') + $composeBaseArgs + @('--profile', 'discord', 'stop', 'discord_bot')) -IgnoreFailure
    }

    $composeArgs = $composeBaseArgs + @('up', '-d') + $coreServices
    Invoke-DockerCommand -Arguments (@('compose') + $composeArgs)
}

function Test-LocalBridgeRunning {
    $escapedRoot = [Regex]::Escape($projectRoot)
    $matches = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object {
            $_.CommandLine -match 'evelyn_core\.local_io_bridge' -and
            $_.CommandLine -match $escapedRoot
        }
    return [bool]$matches
}

function Start-LocalIoBridge {
    if (Test-LocalBridgeRunning) {
        Write-Host '[Evelyn] Local I/O bridge is already running.'
        return
    }

    $pythonCommand = @"
Set-Location '$projectRoot'
`$env:PYTHONPATH = '$coreRuntime'
`$env:EVELYN_PROJECT_ROOT = '$projectRoot'
`$env:EVELYN_CORE_ROOT = '$projectRoot\evelyn_core'
`$env:EVELYN_CORE_RUNTIME = '$coreRuntime'
`$env:LOCAL_BRIDGE_BOT_API_BASE = 'http://127.0.0.1:$botApiPort'
`$env:STT_SERVICE_URL = 'http://127.0.0.1:8892'
`$env:OMNIVOICE_SERVER_URL = 'http://127.0.0.1:8880'
if (-not `$env:LOCAL_MIC_START_THRESHOLD) { `$env:LOCAL_MIC_START_THRESHOLD = '0.002' }
if (-not `$env:LOCAL_MIC_CONTINUE_THRESHOLD) { `$env:LOCAL_MIC_CONTINUE_THRESHOLD = '0.001' }
if (-not `$env:LOCAL_MIC_MIN_VOICED_MS) { `$env:LOCAL_MIC_MIN_VOICED_MS = '280' }
if (-not `$env:LOCAL_MIC_WAVEFORM_FILTER_ENABLED) { `$env:LOCAL_MIC_WAVEFORM_FILTER_ENABLED = 'true' }
if (-not `$env:LOCAL_BRIDGE_TTS_INPUT_SUPPRESS_AFTER_SEC) { `$env:LOCAL_BRIDGE_TTS_INPUT_SUPPRESS_AFTER_SEC = '0.7' }
if (-not `$env:LOCAL_BRIDGE_STATUS_INTERVAL_SEC) { `$env:LOCAL_BRIDGE_STATUS_INTERVAL_SEC = '0.25' }
if (-not `$env:LOCAL_BRIDGE_TTS_WARMUP_ENABLED) { `$env:LOCAL_BRIDGE_TTS_WARMUP_ENABLED = 'true' }
if (-not `$env:LOCAL_BRIDGE_TTS_WARMUP_DELAY_SEC) { `$env:LOCAL_BRIDGE_TTS_WARMUP_DELAY_SEC = '0.5' }
if (Test-Path '.venv\Scripts\python.exe') {
    & '.venv\Scripts\python.exe' -m evelyn_core.local_io_bridge --project-root '$projectRoot' *>> '$bridgeLog'
} else {
    py -3 -m evelyn_core.local_io_bridge --project-root '$projectRoot' *>> '$bridgeLog'
}
"@

    $visible = if ($env:EVELYN_LOCAL_BRIDGE_VISIBLE) {
        ([string]$env:EVELYN_LOCAL_BRIDGE_VISIBLE).ToLowerInvariant() -notin @('0', 'false', 'no', 'off')
    } else {
        $true
    }
    Start-PowerShellWindow -Title 'Evelyn Local I/O Bridge' -Script $pythonCommand -Visible:$visible
}

function Open-ControlPage {
    if ($env:CONTROL_PAGE_AUTO_OPEN -and ([string]$env:CONTROL_PAGE_AUTO_OPEN).ToLowerInvariant() -in @('0', 'false', 'no', 'off')) {
        return
    }

    $chromeCandidates = @(@(
        (Join-Path $env:ProgramFiles 'Google\Chrome\Application\chrome.exe'),
        (Join-Path ${env:ProgramFiles(x86)} 'Google\Chrome\Application\chrome.exe'),
        (Join-Path $env:LOCALAPPDATA 'Google\Chrome\Application\chrome.exe')
    ) | Where-Object { $_ -and (Test-Path $_) })

    if ($chromeCandidates) {
        Start-Process -FilePath $chromeCandidates[0] -ArgumentList @('--new-tab', $controlPageUrl) | Out-Null
        return
    }

    Start-Process $controlPageUrl | Out-Null
}

Start-DockerCore

Wait-Port -HostName '127.0.0.1' -Port 9820 -Label 'Main-LLM'
Wait-Port -HostName '127.0.0.1' -Port 9822 -Label 'Router-LLM'
Wait-Port -HostName '127.0.0.1' -Port 9821 -Label 'Sub-LLM'
Wait-Port -HostName '127.0.0.1' -Port 8880 -Label 'OmniVoice-TTS'
Wait-Port -HostName '127.0.0.1' -Port 8892 -Label 'STT'
Wait-Port -HostName '127.0.0.1' -Port $botApiPort -Label 'Docker Bot API'
Wait-Port -HostName '127.0.0.1' -Port $controlPagePublicPort -Label 'Docker Control Page'

Start-LocalIoBridge

Write-Host "[Evelyn] Docker local core is ready. Control page: $controlPageUrl"
Write-Host "[Evelyn] Windows local I/O bridge log: $bridgeLog"
Open-ControlPage
