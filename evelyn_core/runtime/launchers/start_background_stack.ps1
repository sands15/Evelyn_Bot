$ErrorActionPreference = 'Stop'

$projectRoot = if ($env:EVELYN_PROJECT_ROOT) { Resolve-Path $env:EVELYN_PROJECT_ROOT } else { Resolve-Path (Join-Path $PSScriptRoot '..\..\..') }
$coreRoot = Join-Path $projectRoot 'evelyn_core'
$supervisor = Join-Path $PSScriptRoot 'supervise_service.ps1'
$controlPageUrl = 'http://127.0.0.1:8799/'
$stopMarker = Join-Path $projectRoot '.evelyn_stop_requested'
if (Test-Path $stopMarker) {
    Remove-Item -LiteralPath $stopMarker -Force -ErrorAction SilentlyContinue
}

function Test-PortListening {
    param([int]$Port)
    return $null -ne (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1)
}

function Wait-Port {
    param(
        [string]$HostName,
        [int]$Port,
        [string]$Label
    )

    $timeoutSec = if ($env:START_WAIT_TIMEOUT_SEC) { [int]$env:START_WAIT_TIMEOUT_SEC } else { 120 }
    $intervalMs = if ($env:START_WAIT_INTERVAL_SEC) { [int]$env:START_WAIT_INTERVAL_SEC * 1000 } else { 2000 }
    $deadline = (Get-Date).AddSeconds($timeoutSec)

    while ((Get-Date) -lt $deadline) {
        $client = $null
        try {
            $client = [System.Net.Sockets.TcpClient]::new()
            $iar = $client.BeginConnect($HostName, $Port, $null, $null)
            if ($iar.AsyncWaitHandle.WaitOne(1000)) {
                $client.EndConnect($iar)
                $client.Close()
                return
            }
        } catch {
        } finally {
            if ($client) {
                $client.Close()
            }
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

function Start-HiddenPowerShellWindow {
    param(
        [string]$Title,
        [string]$Script
    )

    $bootstrap = @"
try { `$Host.UI.RawUI.WindowTitle = '$Title' } catch {}
$Script
"@
    $encoded = New-EncodedCommand -Script $bootstrap
    $startHidden = if ($env:EVELYN_START_HIDDEN) { ([string]$env:EVELYN_START_HIDDEN).ToLowerInvariant() -in @('1', 'true', 'yes', 'on') } else { $false }
    $windowStyle = if ($startHidden) { 'Hidden' } else { 'Normal' }
    Start-Process -FilePath 'powershell.exe' -WorkingDirectory $projectRoot -WindowStyle $windowStyle -ArgumentList @(
        '-NoLogo',
        '-NoProfile',
        '-ExecutionPolicy', 'Bypass',
        '-EncodedCommand', $encoded
    ) | Out-Null
}

function Start-SupervisedService {
    param(
        [string]$Title,
        [int]$Port,
        [string]$Name,
        [string]$Command
    )

    if (Test-PortListening -Port $Port) {
        return
    }

    $script = @"
Set-Location '$projectRoot'
& '$supervisor' -Name '$Name' -Workdir '$projectRoot' -Command "$Command"
"@
    Start-HiddenPowerShellWindow -Title $Title -Script $script
}

function Open-ChromeToControlPage {
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

Start-SupervisedService -Title 'Main-LLM' -Port 9820 -Name 'Main-LLM' -Command "& '$coreRoot\start_main_llm.bat' --inline"
Start-SupervisedService -Title 'Router-LLM' -Port 9822 -Name 'Router-LLM' -Command "& '$coreRoot\start_router_llm.bat' --inline"
Start-SupervisedService -Title 'Sub-LLM' -Port 9821 -Name 'Sub-LLM' -Command "& '$coreRoot\start_sub_llm.bat' --inline"
Start-SupervisedService -Title 'TTS' -Port 8880 -Name 'TTS' -Command "& '$PSScriptRoot\start_tts.ps1'"
Start-SupervisedService -Title 'Control-Page' -Port 8799 -Name 'Control-Page' -Command "& '$PSScriptRoot\start_control_page.ps1'"
Start-SupervisedService -Title 'Bot' -Port 8798 -Name 'Bot' -Command "`$env:CONTROL_PAGE_PORT='8798'; & '$PSScriptRoot\start_bot.ps1'"
Wait-Port -HostName '127.0.0.1' -Port 8799 -Label 'Evelyn Control Page'
Write-Host '[Evelyn] Full stack launch requested. The control page will show Main/Router/Sub/TTS/Bot boot progress.'
Open-ChromeToControlPage
