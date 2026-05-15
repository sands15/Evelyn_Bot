$ErrorActionPreference = 'Stop'

$hostName = if ($env:MINECRAFT_AUTONOMY_SERVICE_HOST) { $env:MINECRAFT_AUTONOMY_SERVICE_HOST } else { '127.0.0.1' }
$port = if ($env:MINECRAFT_AUTONOMY_SERVICE_PORT) { [int]$env:MINECRAFT_AUTONOMY_SERVICE_PORT } else { 8765 }
$mode = if ($env:VOYAGER_START_MODE) { $env:VOYAGER_START_MODE } else { '' }
$goal = if ($env:VOYAGER_START_GOAL) { $env:VOYAGER_START_GOAL } else { 'discovering as many diverse things as possible' }
$timeoutSec = if ($env:START_WAIT_TIMEOUT_SEC) { [int]$env:START_WAIT_TIMEOUT_SEC } else { 120 }
$intervalSec = if ($env:START_WAIT_INTERVAL_SEC) { [int]$env:START_WAIT_INTERVAL_SEC } else { 2 }
$baseUrl = "http://$hostName`:$port"
$deadline = (Get-Date).AddSeconds([Math]::Max(5, $timeoutSec))

while ((Get-Date) -lt $deadline) {
    try {
        $health = Invoke-RestMethod -Uri "$baseUrl/health" -Method Get -TimeoutSec 5
        if ($health.ok) { break }
    } catch {
    }
    Start-Sleep -Seconds ([Math]::Max(1, $intervalSec))
}

$body = @{}
if ($mode) { $body.mode = $mode }
if ($goal) { $body.goal = $goal }

$response = Invoke-RestMethod -Uri "$baseUrl/start" -Method Post -ContentType 'application/json' -Body ($body | ConvertTo-Json -Compress) -TimeoutSec ([Math]::Max(30, $timeoutSec))
Write-Output '[Evelyn] Voyager auto-start triggered.'
Write-Output ($response | ConvertTo-Json -Depth 8)
