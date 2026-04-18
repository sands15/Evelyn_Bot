$ErrorActionPreference = 'Stop'
$projectRoot = Resolve-Path (Join-Path $PSScriptRoot '..')
Set-Location $projectRoot

function Wait-Port {
    param(
        [string]$HostName,
        [int]$Port,
        [string]$Label
    )

    $timeoutSec = if ($env:START_WAIT_TIMEOUT_SEC) { [int]$env:START_WAIT_TIMEOUT_SEC } else { 120 }
    $intervalSec = if ($env:START_WAIT_INTERVAL_SEC) { [int]$env:START_WAIT_INTERVAL_SEC } else { 2 }
    $deadline = (Get-Date).AddSeconds($timeoutSec)

    Write-Host "[Evelyn] Waiting for $Label at ${HostName}:$Port"
    while ((Get-Date) -lt $deadline) {
        $client = $null
        try {
            $client = [System.Net.Sockets.TcpClient]::new()
            $iar = $client.BeginConnect($HostName, $Port, $null, $null)
            if ($iar.AsyncWaitHandle.WaitOne(1000)) {
                $client.EndConnect($iar)
                $client.Close()
                Write-Host "[Evelyn] $Label is ready"
                return
            }
        } catch {
        } finally {
            if ($client) {
                $client.Close()
            }
        }
        Start-Sleep -Seconds $intervalSec
    }

    throw "$Label was not ready in time"
}

$env:OPUS_ERROR_TO_SILENCE = if ($env:OPUS_ERROR_TO_SILENCE) { $env:OPUS_ERROR_TO_SILENCE } else { 'false' }
$env:STT_USE_RAW_48K = if ($env:STT_USE_RAW_48K) { $env:STT_USE_RAW_48K } else { 'false' }

$mainPort = if ($env:MAIN_LLM_PORT) { [int]$env:MAIN_LLM_PORT } else { 9820 }
$subPort = if ($env:SUB_LLM_PORT) { [int]$env:SUB_LLM_PORT } else { 9821 }
$ttsPort = if ($env:TTS_PORT) { [int]$env:TTS_PORT } else { 8880 }

Wait-Port -HostName '127.0.0.1' -Port $mainPort -Label 'Main-LLM'
Wait-Port -HostName '127.0.0.1' -Port $subPort -Label 'Sub-LLM'
Wait-Port -HostName '127.0.0.1' -Port $ttsPort -Label 'OmniVoice-TTS'

if (-not $env:DISCORD_BOT_TOKEN) {
    throw '[Evelyn] DISCORD_BOT_TOKEN 환경변수가 설정되지 않았습니다.'
}

$venvPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (Test-Path $venvPython) {
    & $venvPython 'main.py'
} else {
    py -3 'main.py'
}
