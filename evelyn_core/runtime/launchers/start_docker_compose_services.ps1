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

$sourceRevisionHelper = Join-Path $PSScriptRoot 'source_revision.ps1'
if (-not (Test-Path -LiteralPath $sourceRevisionHelper -PathType Leaf)) {
    throw "Source revision helper not found: $sourceRevisionHelper"
}
. $sourceRevisionHelper
$sourceRevision = Initialize-EvelynSourceRevision -ProjectRoot $projectRoot
Write-Host "[Evelyn] Runtime source revision: $sourceRevision"

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

function Initialize-CodexCredentialMount {
    $needsGateway = (
        $normalizedServices -contains 'codex_gateway' -or
        $normalizedServices -contains 'voyager' -or
        ($normalizedServices.Count -eq 0 -and $normalizedProfiles -contains 'voyager')
    )
    if (-not $needsGateway) {
        return
    }

    $credentialDirectory = if ($env:EVELYN_CODEX_CREDENTIALS_DIR) {
        [System.IO.Path]::GetFullPath($env:EVELYN_CODEX_CREDENTIALS_DIR)
    } else {
        [System.IO.Path]::GetFullPath(
            (Join-Path $projectRoot 'runtime_artifacts\secrets\codex_device_home')
        )
    }
    $liveCodexHome = [System.IO.Path]::GetFullPath(
        (Join-Path $env:USERPROFILE '.codex')
    ).TrimEnd('\', '/')
    $liveCodexPrefix = $liveCodexHome + [System.IO.Path]::DirectorySeparatorChar
    if (
        $credentialDirectory.Equals(
            $liveCodexHome,
            [System.StringComparison]::OrdinalIgnoreCase
        ) -or
        $credentialDirectory.StartsWith(
            $liveCodexPrefix,
            [System.StringComparison]::OrdinalIgnoreCase
        )
    ) {
        throw 'EVELYN_CODEX_CREDENTIALS_DIR must not point at the live user .codex directory.'
    }

    $authFile = Join-Path $credentialDirectory 'auth.json'
    if (-not (Test-Path -LiteralPath $authFile -PathType Leaf)) {
        $provisioner = Join-Path $PSScriptRoot 'provision_codex_credentials.ps1'
        throw (
            'Dedicated Codex auth.json is missing. Run: powershell.exe ' +
            "-NoProfile -ExecutionPolicy Bypass -File `"$provisioner`""
        )
    }
    if ((Get-Item -LiteralPath $authFile).Length -gt 1MB) {
        throw 'Dedicated Codex auth.json exceeds the 1 MiB safety limit.'
    }
    try {
        Get-Content -LiteralPath $authFile -Raw -Encoding UTF8 |
            ConvertFrom-Json |
            Out-Null
    } catch {
        throw 'Dedicated Codex auth.json is not valid JSON.'
    }
    $env:EVELYN_CODEX_CREDENTIALS_DIR = $credentialDirectory
}

Initialize-CodexCredentialMount

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

$pathSafeBuildServices = @()
if ($buildEnabled -and $normalizedServices -contains 'vision') {
    $pathSafeBuildServices += 'vision'
}
if ($normalizedServices -contains 'tts') {
    $ttsImage = 'evelyn-omnivoice-tts:recipe-7cfc51e96088'
    & docker image inspect $ttsImage *> $null
    $ttsImageMissing = $LASTEXITCODE -ne 0
    if ($buildEnabled -or $ttsImageMissing) {
        $pathSafeBuildServices += 'tts'
    }
}
if ($pathSafeBuildServices.Count -gt 0) {
    $pathSafeBuilder = Join-Path $PSScriptRoot 'build_local_docker_images.ps1'
    if (-not (Test-Path -LiteralPath $pathSafeBuilder -PathType Leaf)) {
        throw "Path-safe Docker image builder not found: $pathSafeBuilder"
    }
    & $pathSafeBuilder -ProjectRoot $projectRoot -Services $pathSafeBuildServices
}

if ($buildEnabled) {
    $composeBuildServices = @(
        $serviceArgs | Where-Object {
            $_ -notin $pathSafeBuildServices
        }
    )
    if ($composeBuildServices.Count -gt 0) {
        Invoke-DockerCommand -Arguments (
            @('compose') + $composeArgs + @('build') + $composeBuildServices
        )
    }
}

Invoke-DockerCommand -Arguments (
    @('compose') + $composeArgs + @('up', '-d', '--no-build') + $serviceArgs
)
