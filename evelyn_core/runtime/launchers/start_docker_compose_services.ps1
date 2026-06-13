param(
    [string[]]$Profiles = @(),
    [string[]]$Services = @(),
    [switch]$Build
)

$ErrorActionPreference = 'Stop'

$projectRoot = if ($env:EVELYN_PROJECT_ROOT) { Resolve-Path $env:EVELYN_PROJECT_ROOT } else { Resolve-Path (Join-Path $PSScriptRoot '..\..\..') }
$projectRoot = [string]$projectRoot
$composeFile = Join-Path $projectRoot 'docker-compose.fast-control.yml'

if (-not (Test-Path -LiteralPath $composeFile)) {
    throw "Compose file not found: $composeFile"
}

function Invoke-DockerCommand {
    param([string[]]$Arguments)

    $output = & docker @Arguments 2>&1
    $exitCode = $LASTEXITCODE
    if ($output) {
        $output | ForEach-Object { Write-Host $_ }
    }
    if ($exitCode -ne 0) {
        throw "docker $($Arguments -join ' ') failed with exit code $exitCode"
    }
}

$normalizedProfiles = @()
foreach ($profile in $Profiles) {
    foreach ($item in ([string]$profile -split ',')) {
        $trimmed = $item.Trim()
        if ($trimmed) {
            $normalizedProfiles += $trimmed
        }
    }
}

$normalizedServices = @()
foreach ($service in $Services) {
    foreach ($item in ([string]$service -split ',')) {
        $trimmed = $item.Trim()
        if ($trimmed) {
            $normalizedServices += $trimmed
        }
    }
}

$composeArgs = @('-f', $composeFile)
foreach ($profile in $normalizedProfiles) {
    $composeArgs += @('--profile', $profile)
}

$serviceArgs = $normalizedServices

Set-Location $projectRoot

$buildEnabled = $Build -or (
    $env:EVELYN_DOCKER_BUILD -and
    ([string]$env:EVELYN_DOCKER_BUILD).ToLowerInvariant() -in @('1', 'true', 'yes', 'on')
)

if ($buildEnabled) {
    Invoke-DockerCommand -Arguments (@('compose') + $composeArgs + @('build') + $serviceArgs)
}

Invoke-DockerCommand -Arguments (@('compose') + $composeArgs + @('up', '-d') + $serviceArgs)
