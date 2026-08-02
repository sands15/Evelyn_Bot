param(
    [int]$TimeoutSec = 10,
    [switch]$IncludeDiscordBot,
    [switch]$IncludeMinecraftStack,
    [switch]$IncludeCodexAction,
    [switch]$IncludeLocalBridge
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$ComposeFile = Join-Path $ProjectRoot "docker-compose.fast-control.yml"
$script:Failures = 0
$script:ComposeProfiles = @("llm", "tts", "vision", "stt")
if ($IncludeMinecraftStack -or $IncludeCodexAction) {
    $script:ComposeProfiles += "voyager"
}
if ($IncludeDiscordBot) {
    $script:ComposeProfiles += "discord"
}
if ([string]::IsNullOrWhiteSpace($env:DISCORD_BOT_TOKEN)) {
    # Compose interpolates required variables even for inactive profiles.
    # This value is never used to start Discord and exists only for config/ps.
    $env:DISCORD_BOT_TOKEN = "runtime-check-disabled"
}

function Get-ComposeArgs {
    $args = @("-f", $ComposeFile)
    foreach ($profile in $script:ComposeProfiles) {
        $args += @("--profile", $profile)
    }
    return $args
}

function Write-Section {
    param([string]$Title)
    Write-Host ""
    Write-Host "== $Title =="
}

function Add-Failure {
    param([string]$Message)
    $script:Failures += 1
    Write-Host "[FAIL] $Message" -ForegroundColor Red
}

function Add-Ok {
    param([string]$Message)
    Write-Host "[OK] $Message" -ForegroundColor Green
}

function Add-Warn {
    param([string]$Message)
    Write-Host "[WARN] $Message" -ForegroundColor Yellow
}

function Invoke-RequiredHttp {
    param(
        [string]$Name,
        [string]$Uri,
        [scriptblock]$Validate
    )

    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $Uri -TimeoutSec $TimeoutSec
        $content = [string]$response.Content
        $json = $null

        try {
            if ($content.Trim().Length -gt 0) {
                $json = $content | ConvertFrom-Json
            }
        }
        catch {
            $json = $null
        }

        $valid = $response.StatusCode -eq 200
        if ($Validate) {
            $valid = $valid -and (& $Validate $json $content)
        }

        if ($valid) {
            Add-Ok "$Name responds at $Uri"
            return $json
        }

        Add-Failure "$Name responded but did not pass validation at $Uri"
        return $json
    }
    catch {
        Add-Failure "$Name is not responding at $Uri ($($_.Exception.Message))"
        return $null
    }
}

Write-Section "Docker"
if (-not (Test-Path $ComposeFile)) {
    Add-Failure "Compose file not found: $ComposeFile"
}
else {
    Add-Ok "Compose file found: $ComposeFile"
}

try {
    $dockerVersion = docker version --format "{{.Server.Os}}/{{.Server.Arch}}"
    Add-Ok "Docker daemon: $dockerVersion"
}
catch {
    Add-Failure "Docker daemon is not available ($($_.Exception.Message))"
}

try {
    $composeArgs = Get-ComposeArgs
    docker compose @composeArgs config | Out-Null
    Add-Ok "Compose config is valid"
}
catch {
    Add-Failure "Compose config failed ($($_.Exception.Message))"
}

Write-Section "Compose Services"
try {
    $composeArgs = Get-ComposeArgs
    docker compose @composeArgs ps
}
catch {
    Add-Failure "Could not read compose service status ($($_.Exception.Message))"
}

Write-Section "HTTP Health"
Invoke-RequiredHttp "Control-Page health" "http://127.0.0.1:8799/health" {
    param($json, $content)
    return $null -ne $json -and $json.ok -eq $true
} | Out-Null

$controlState = Invoke-RequiredHttp "Control-Page state" "http://127.0.0.1:8799/api/control-page/state" {
    param($json, $content)
    return $null -ne $json -and $null -ne $json.runtime -and $null -ne $json.runtime.services
}

Invoke-RequiredHttp "Bot API state" "http://127.0.0.1:8798/api/control-page/state" {
    param($json, $content)
    return $null -ne $json -and $null -ne $json.runtime -and $null -ne $json.runtime.services
} | Out-Null

Invoke-RequiredHttp "Main LLM" "http://127.0.0.1:9820/v1/models" {
    param($json, $content)
    return $true
} | Out-Null

Invoke-RequiredHttp "Router LLM" "http://127.0.0.1:9822/v1/models" {
    param($json, $content)
    return $true
} | Out-Null

Invoke-RequiredHttp "Sub LLM" "http://127.0.0.1:9821/v1/models" {
    param($json, $content)
    return $true
} | Out-Null

Invoke-RequiredHttp "TTS" "http://127.0.0.1:8880/health" {
    param($json, $content)
    return $null -ne $json -and ($json.ready -eq $true -or $json.status -eq "healthy")
} | Out-Null

Invoke-RequiredHttp "STT" "http://127.0.0.1:8892/health" {
    param($json, $content)
    return $null -ne $json -and $json.ok -eq $true -and $json.ready -eq $true
} | Out-Null

Invoke-RequiredHttp "Vision" "http://127.0.0.1:8891/health" {
    param($json, $content)
    return $null -ne $json -and $json.ok -eq $true
} | Out-Null

if ($IncludeMinecraftStack) {
    Invoke-RequiredHttp "Voyager" "http://127.0.0.1:8765/health" {
        param($json, $content)
        return $null -ne $json -and $json.ok -eq $true
    } | Out-Null
}
else {
    Add-Warn "Voyager health check skipped; use -IncludeMinecraftStack when the deferred stack is running"
}

if ($IncludeMinecraftStack -or $IncludeCodexAction) {
    Invoke-RequiredHttp "Codex Gateway" "http://127.0.0.1:8787/health" {
        param($json, $content)
        return $null -ne $json -and $json.ok -eq $true -and $json.backendReady -eq $true
    } | Out-Null
}
else {
    Add-Warn "Codex Gateway health check skipped; use -IncludeMinecraftStack or -IncludeCodexAction"
}

if ($IncludeCodexAction) {
    Write-Section "Codex Action"
    try {
        $tokenPath = if ($env:VOYAGER_CODEX_GATEWAY_TOKEN_FILE) { $env:VOYAGER_CODEX_GATEWAY_TOKEN_FILE } else { Join-Path $PSScriptRoot '..\runtime_artifacts\secrets\codex_gateway.token' }
        $token = if ($env:VOYAGER_CODEX_GATEWAY_TOKEN) { $env:VOYAGER_CODEX_GATEWAY_TOKEN.Trim() } elseif (Test-Path -LiteralPath $tokenPath) { (Get-Content -LiteralPath $tokenPath -Raw).Trim() } else { '' }
        if (-not $token) {
            throw "Codex gateway token is unavailable at $tokenPath"
        }
        $body = @{
            prompt = "Return exactly OK."
            model = "gpt-5.5"
            timeout_sec = 60
            cwd = "/app"
            source = "docker-runtime-check"
            priority = 0
        } | ConvertTo-Json
        $headers = @{ Authorization = "Bearer $token" }
        $response = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8787/codex/action" -Headers $headers -ContentType "application/json" -Body $body -TimeoutSec 75
        if ($response.ok -eq $true -and [string]$response.content) {
            Add-Ok "Codex action endpoint executed successfully"
        }
        else {
            Add-Failure "Codex action endpoint responded but did not return ok content"
        }
    }
    catch {
        Add-Failure "Codex action endpoint failed ($($_.Exception.Message))"
    }
}

if ($IncludeDiscordBot) {
    Write-Section "Discord Bot"
    try {
        Start-Sleep -Seconds 5
        $state = docker inspect -f "{{.State.Status}}" evelyn-discord-bot 2>$null
        $restartCount = docker inspect -f "{{.RestartCount}}" evelyn-discord-bot 2>$null
        $runningFor = docker inspect -f "{{.State.StartedAt}}" evelyn-discord-bot 2>$null
        if ($state -eq "running") {
            Add-Ok "discord_bot container is running (restarts=$restartCount, startedAt=$runningFor)"
        }
        else {
            Add-Failure "discord_bot container is not running (state=$state)"
        }
        if ([int]$restartCount -gt 0) {
            Add-Warn "discord_bot has restarted $restartCount time(s); check logs if this was not from a recent rebuild"
        }
    }
    catch {
        Add-Failure "discord_bot container is not available ($($_.Exception.Message))"
    }
}

if ($IncludeLocalBridge) {
    Write-Section "Windows Local I/O Bridge"
    $bridge = if ($controlState -and $controlState.voice) {
        $controlState.voice.localBridge
    }
    else {
        $null
    }
    if (
        $bridge -and
        $bridge.enabled -eq $true -and
        $bridge.ready -eq $true -and
        $bridge.stale -ne $true
    ) {
        Add-Ok "Windows local I/O bridge is attached and ready"
    }
    else {
        Add-Failure "Windows local I/O bridge is not ready in the public Control Page state"
    }
}

Write-Section "Runtime Readiness"
if ($controlState -and $controlState.runtime -and $controlState.runtime.services) {
    $services = $controlState.runtime.services
    $requiredFlags = @(
        "controlReady",
        "botReady",
        "mainReady",
        "routerReady",
        "subReady",
        "ttsReady",
        "sttReady",
        "chatReady",
        "voiceReady",
        "visionReady"
    )

    foreach ($flag in $requiredFlags) {
        if ($services.$flag -eq $true) {
            Add-Ok "$flag=true"
        }
        else {
            Add-Failure "$flag is not true"
        }
    }

    if ($IncludeMinecraftStack) {
        foreach ($flag in @("voyagerReady", "codexReady")) {
            if ($services.$flag -eq $true) {
                Add-Ok "$flag=true"
            }
            else {
                Add-Failure "$flag is not true for the requested Minecraft stack"
            }
        }
    }
    elseif ($IncludeCodexAction) {
        if ($services.codexReady -eq $true) {
            Add-Ok "codexReady=true"
        }
        else {
            Add-Failure "codexReady is not true for the requested Codex action check"
        }
        if ($services.voyagerReady -eq $true) {
            Add-Ok "voyagerReady=true"
        }
        else {
            Add-Warn "voyagerReady=false or unavailable (not required for the Codex action check)"
        }
    }
    else {
        foreach ($flag in @("voyagerReady", "codexReady")) {
            if ($services.$flag -eq $true) {
                Add-Ok "$flag=true"
            }
            else {
                Add-Warn "$flag=false or unavailable (deferred from the local core stack)"
            }
        }
    }

    if ($controlState.runtime.serviceHealth -and $controlState.runtime.serviceHealth.summary) {
        Write-Host "Runtime summary: $($controlState.runtime.serviceHealth.summary)"
    }
}
else {
    Add-Failure "Control-Page state did not include runtime services"
}

Write-Section "GPU"
try {
    nvidia-smi --query-gpu=index,name,memory.used,memory.free --format=csv,noheader
}
catch {
    Add-Warn "nvidia-smi is not available ($($_.Exception.Message))"
}

Write-Section "Result"
if ($script:Failures -gt 0) {
    Write-Host "Docker runtime check failed with $script:Failures issue(s)." -ForegroundColor Red
    exit 1
}

Write-Host "Docker runtime check passed." -ForegroundColor Green
