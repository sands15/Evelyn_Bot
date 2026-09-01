#Requires -Version 7.2

[CmdletBinding()]
param(
    [switch]$RunLive,

    [ValidatePattern('^[1-9]\d{16,19}$')]
    [string]$GuildId = '',

    [ValidatePattern('^S-\d(?:-\d+)+$')]
    [string]$ExpectedAdminSid = '',

    [string]$ExpectedAdminAccount = '',

    [ValidatePattern('^[1-9]\d{4,23}$')]
    [string]$AdminDiscordUserId = '',

    [ValidateRange(60, 900)]
    [int]$ValidationWindowSec = 300,

    [string]$MainLlmImage = 'evelyn-fast-control-main_llm:latest',
    [string]$RouterLlmImage = 'evelyn-fast-control-router_llm:latest',
    [string]$SubLlmImage = 'evelyn-fast-control-sub_llm:latest',
    [string]$SttImage = 'evelyn-fast-control-stt:latest'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$ownerLabel = 'ai.evelyn.owner'
$runLabel = 'ai.evelyn.run-id'
$roleLabel = 'ai.evelyn.role'
$owner = 'evelyn.discord-feedback-live-validation.v1'
$placeholderRevision = '0' * 64
$discordInterpolationPlaceholder = 'validation-placeholder'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$projectRuntimeArtifactsRoot = Join-Path $projectRoot 'runtime_artifacts'
$runId = [Guid]::NewGuid().ToString('N')
$shortRunId = $runId.Substring(0, 12)
$composeProject = "evelyn-discord-live-$shortRunId"
$runRoot = Join-Path $projectRuntimeArtifactsRoot "validation\discord-feedback-live\$runId"
$sourceRoot = Join-Path $runRoot 'source'
$identityRoot = Join-Path $runRoot 'identity'
$scratchRoot = Join-Path $runRoot 'scratch'
$scratchRuntimeRoot = Join-Path $scratchRoot 'runtime_artifacts'
$scratchBotMemoryRoot = Join-Path $scratchRoot 'bot_memory'
$scratchBotProfilesRoot = Join-Path $scratchRoot 'bot_profiles'
$scratchGuildSettingsRoot = Join-Path $scratchRoot 'guild_settings'
$scratchLogsRoot = Join-Path $scratchRoot 'logs'
$scratchHostSessionPlaceholder = Join-Path $scratchRoot 'host-session.unavailable'
$preflightOverlay = Join-Path $runRoot 'compose.preflight.yml'
$liveOverlay = Join-Path $runRoot 'compose.live.yml'
$resultPath = Join-Path $runRoot 'result.json'
$commandGuardRoot = Join-Path $runRoot 'command_guard'
$commandGuardStatusPath = Join-Path $commandGuardRoot 'status.json'
$commandGuardCleanupPath = Join-Path $commandGuardRoot 'cleanup.request'
$baseCompose = Join-Path $projectRoot 'docker-compose.fast-control.yml'
$archiveCompose = Join-Path $projectRoot 'docker-compose.conversation-archive.yml'
$credentialModule = Join-Path $projectRoot 'tools\discord_capture_credential.psm1'
$provisioner = Join-Path $projectRoot 'scripts\Initialize-EvelynConversationArchiveTest.ps1'
$attestationLauncher = Join-Path $projectRoot 'scripts\Start-EvelynConversationArchiveAdmin.ps1'
$archiveSecretsRoot = 'C:\ProgramData\Evelyn\private-audit-secrets'
$maxOutputBytes = 16MB
$dockerWaitSec = 120
$cleanupFailures = [Collections.Generic.List[string]]::new()
$hostSupervisor = $null
$discordAttach = $null
$discordContainerId = ''
$commandGuard = $null
$commandGuardContainerId = ''
$initialDockerRunning = $null
$initialDockerState = $null
$baselineContainers = ''
$protectedImageSnapshot = ''
$dockerHostSnapshotCaptured = $false
$dockerStartAttemptedByLauncher = $false
$dockerContextName = ''
$sourceDigest = ''
$operatorConfirmed = $false
$validationAttestationHash = ''
$validationAttestationNonce = ''
$validationAttestationOwned = $false
$validationAttestationPath = Join-Path $archiveSecretsRoot 'host-attestation.json'
$validationHostSessionPath = Join-Path $archiveSecretsRoot 'host-session.json'

function Write-PublicResult {
    param([bool]$Ok, [string]$State, [string]$Failure = '')

    $payload = [ordered]@{
        schema = 'discord_feedback.live-validation.v1'
        ok = $Ok
        state = $State
        runId = $runId
        sourceDigest = $sourceDigest
        failure = $Failure
        cleanupFailures = @($cleanupFailures)
        operatorConfirmed = $operatorConfirmed
        contentFree = $true
    }
    if (Test-Path -LiteralPath $runRoot -PathType Container) {
        [IO.File]::WriteAllText(
            $resultPath,
            ($payload | ConvertTo-Json -Depth 4 -Compress),
            [Text.UTF8Encoding]::new($false)
        )
        if ($cleanupFailures.Count -eq 0) {
            $remaining = @(Get-ChildItem -LiteralPath $runRoot -Force)
            if (
                $remaining.Count -ne 1 -or
                $remaining[0].PSIsContainer -or
                -not [string]::Equals(
                    $remaining[0].FullName,
                    $resultPath,
                    [StringComparison]::OrdinalIgnoreCase
                )
            ) { throw 'public_result_artifact_isolation_failed' }
        }
    }
    $payload | ConvertTo-Json -Depth 4 -Compress
}

function Get-PublicFailureCode {
    param([string]$Value)
    $candidate = ([string]$Value).Trim()
    if ($candidate -match '^[a-z][a-z0-9_]{0,95}$') { return $candidate }
    return 'unexpected_validation_failure'
}

function New-ChannelToken {
    $bytes = [byte[]]::new(48)
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $rng.GetBytes($bytes)
        return [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
    } finally {
        [Array]::Clear($bytes, 0, $bytes.Length)
        $rng.Dispose()
    }
}

function Invoke-External {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Collections.IDictionary]$Environment = @{},
        [byte[]]$InputBytes = $null,
        [int]$TimeoutSec = 120,
        [switch]$AllowFailure,
        [string]$WorkingDirectory = $projectRoot
    )

    $startInfo = [Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $FilePath
    $startInfo.WorkingDirectory = $WorkingDirectory
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardInput = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    foreach ($argument in $Arguments) {
        $null = $startInfo.ArgumentList.Add([string]$argument)
    }
    $null = $startInfo.Environment.Remove('DISCORD_BOT_TOKEN')
    foreach ($entry in $Environment.GetEnumerator()) {
        $startInfo.Environment[[string]$entry.Key] = [string]$entry.Value
    }
    $process = [Diagnostics.Process]::new()
    $process.StartInfo = $startInfo
    $processStarted = $false
    try {
        if (-not $process.Start()) {
            throw 'process_start_failed'
        }
        $processStarted = $true
        if ($null -ne $InputBytes) {
            $process.StandardInput.BaseStream.Write($InputBytes, 0, $InputBytes.Length)
            $process.StandardInput.BaseStream.WriteByte(10)
            $process.StandardInput.BaseStream.Flush()
        }
        $process.StandardInput.Close()
        $stdoutTask = $process.StandardOutput.ReadToEndAsync()
        $stderrTask = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit($TimeoutSec * 1000)) {
            try { $process.Kill($true) } catch { $process.Kill() }
            $process.WaitForExit()
            throw 'process_timeout'
        }
        $stdout = $stdoutTask.GetAwaiter().GetResult()
        $stderr = $stderrTask.GetAwaiter().GetResult()
        if (
            [Text.Encoding]::UTF8.GetByteCount($stdout) +
            [Text.Encoding]::UTF8.GetByteCount($stderr) -gt $maxOutputBytes
        ) {
            throw 'process_output_too_large'
        }
        $result = [pscustomobject]@{
            ExitCode = $process.ExitCode
            Stdout = $stdout
            Stderr = $stderr
        }
        if ($result.ExitCode -ne 0 -and -not $AllowFailure) {
            throw 'external_command_failed'
        }
        return $result
    } finally {
        if ($processStarted -and -not $process.HasExited) {
            try { $process.Kill($true) } catch { }
        }
        $process.Dispose()
    }
}

$dockerCommand = Get-Command docker.exe -ErrorAction SilentlyContinue
if ($null -eq $dockerCommand) {
    $dockerCommand = Get-Command docker -ErrorAction SilentlyContinue
}

function Initialize-DockerContext {
    if (
        -not [string]::IsNullOrWhiteSpace($env:DOCKER_HOST) -or
        -not [string]::IsNullOrWhiteSpace($env:DOCKER_CONTEXT)
    ) { throw 'docker_context_override_forbidden' }
    $context = Invoke-External `
        -FilePath $dockerCommand.Source `
        -Arguments @('context', 'show') `
        -TimeoutSec 15
    $name = $context.Stdout.Trim()
    if ([string]::IsNullOrWhiteSpace($name) -or $name -match "`r|`n") {
        throw 'docker_context_unknown'
    }
    $endpoint = Invoke-External `
        -FilePath $dockerCommand.Source `
        -Arguments @('context', 'inspect', $name, '--format', '{{.Endpoints.docker.Host}}') `
        -TimeoutSec 15
    $endpointName = $endpoint.Stdout.Trim()
    if ($endpointName -cnotin @(
        'npipe:////./pipe/dockerDesktopLinuxEngine',
        'npipe:////./pipe/docker_engine'
    )) { throw 'docker_context_not_local_desktop' }
    $script:dockerContextName = $name
}

function Invoke-Docker {
    param(
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Collections.IDictionary]$Environment = @{},
        [int]$TimeoutSec = 120,
        [switch]$AllowFailure
    )
    if ([string]::IsNullOrWhiteSpace($dockerContextName)) {
        throw 'docker_context_uninitialized'
    }
    return Invoke-External `
        -FilePath $dockerCommand.Source `
        -Arguments (@('--context', $dockerContextName) + $Arguments) `
        -Environment $Environment `
        -TimeoutSec $TimeoutSec `
        -AllowFailure:$AllowFailure
}

function Test-DockerReady {
    if ($null -eq $dockerCommand) { return $false }
    try {
        $result = Invoke-Docker `
            -Arguments @('version', '--format', '{{.Server.Version}}') `
            -TimeoutSec 15 `
            -AllowFailure
        return $result.ExitCode -eq 0 -and -not [string]::IsNullOrWhiteSpace($result.Stdout)
    } catch { return $false }
}

function Get-DockerDesktopOwnerProcessSnapshot {
    return @(
        Get-Process -Name @(
            'Docker Desktop',
            'com.docker.backend',
            'com.docker.build',
            'docker-sandboxd',
            'vpnkit'
        ) -ErrorAction SilentlyContinue |
            Group-Object -Property ProcessName |
            Sort-Object -Property Name |
            ForEach-Object { "$($_.Name)=$($_.Count)" }
    ) -join "`n"
}

function Get-DockerDesktopWslSnapshot {
    $wsl = Get-Command wsl.exe -ErrorAction SilentlyContinue
    if ($null -eq $wsl) { throw 'docker_wsl_state_unknown' }
    $result = Invoke-External `
        -FilePath $wsl.Source `
        -Arguments @('--list', '--running', '--quiet') `
        -TimeoutSec 15 `
        -AllowFailure
    if ($result.ExitCode -ne 0) { throw 'docker_wsl_state_unknown' }
    return @(
        (($result.Stdout -replace "`0", '') -split "`r?`n") |
            ForEach-Object { $_.Trim() } |
            Where-Object { $_ -in @('docker-desktop', 'docker-desktop-data') } |
            Sort-Object -Unique
    ) -join "`n"
}

function Test-DockerDesktopFullyStopped {
    if (Test-DockerReady) { return $false }
    return (
        [string]::IsNullOrEmpty((Get-DockerDesktopOwnerProcessSnapshot)) -and
        [string]::IsNullOrEmpty((Get-DockerDesktopWslSnapshot))
    )
}

function Get-DockerInitialState {
    $ready = Test-DockerReady
    $processes = Get-DockerDesktopOwnerProcessSnapshot
    $wsl = Get-DockerDesktopWslSnapshot
    $status = Invoke-Docker `
        -Arguments @('desktop', 'status') `
        -TimeoutSec 15 `
        -AllowFailure
    $statusText = $status.Stdout.Trim().ToLowerInvariant()
    if ($ready) {
        if (
            $status.ExitCode -ne 0 -or
            $statusText -match 'stopped|not running' -or
            $statusText -notmatch '\brunning\b' -or
            [string]::IsNullOrEmpty($processes) -or
            [string]::IsNullOrEmpty($wsl)
        ) { throw 'docker_initial_state_unknown' }
    } elseif (
        $status.ExitCode -ne 0 -or
            $status.Stdout.Trim().ToLowerInvariant() -notmatch 'stopped|not running' -or
            -not [string]::IsNullOrEmpty($processes) -or
            -not [string]::IsNullOrEmpty($wsl)
    ) { throw 'docker_initial_state_unknown' }
    return [pscustomobject]@{
        EngineRunning = $ready
        DesktopProcesses = $processes
        DockerWsl = $wsl
    }
}

function Wait-DockerReady {
    param([bool]$Running)
    $deadline = [DateTime]::UtcNow.AddSeconds($dockerWaitSec)
    do {
        if ((Test-DockerReady) -eq $Running) { return }
        Start-Sleep -Milliseconds 1000
    } while ([DateTime]::UtcNow -lt $deadline)
    throw 'docker_state_timeout'
}

function Wait-DockerDesktopFullyStopped {
    $deadline = [DateTime]::UtcNow.AddSeconds($dockerWaitSec)
    $stable = 0
    do {
        if (Test-DockerDesktopFullyStopped) {
            $stable += 1
            if ($stable -ge 2) { return }
        }
        else { $stable = 0 }
        Start-Sleep -Milliseconds 500
    } while ([DateTime]::UtcNow -lt $deadline)
    throw 'docker_state_timeout'
}

function Start-DockerDesktop {
    $null = Invoke-Docker `
        -Arguments @('desktop', 'start', '--detach', '--timeout', '30') `
        -TimeoutSec 45
    Wait-DockerReady -Running $true
}

function Stop-DockerDesktop {
    $null = Invoke-Docker `
        -Arguments @('desktop', 'stop', '--detach', '--timeout', '30') `
        -TimeoutSec 45
    Wait-DockerDesktopFullyStopped
}

function Start-DockerIfNeeded {
    $script:initialDockerState = Get-DockerInitialState
    $script:initialDockerRunning = [bool]$initialDockerState.EngineRunning
    if (-not $script:initialDockerRunning) {
        $script:dockerStartAttemptedByLauncher = $true
        Start-DockerDesktop
    }
}

function Ensure-DockerForCleanup {
    if ($null -eq $initialDockerState) { return }
    if (Test-DockerReady) { return }
    Start-DockerDesktop
}

function Restore-DockerInitialState {
    if ($null -eq $initialDockerState) { return }
    if ($initialDockerRunning) {
        if (-not (Test-DockerReady)) { Start-DockerDesktop }
    }
    elseif (-not (Test-DockerDesktopFullyStopped)) {
        Stop-DockerDesktop
    }
    $finalProcesses = Get-DockerDesktopOwnerProcessSnapshot
    $finalWsl = Get-DockerDesktopWslSnapshot
    if (
        (Test-DockerReady) -ne [bool]$initialDockerState.EngineRunning -or
        $finalProcesses -cne [string]$initialDockerState.DesktopProcesses -or
        $finalWsl -cne [string]$initialDockerState.DockerWsl
    ) {
        throw 'docker_restore_failed'
    }
}

function Get-LabelValue {
    param([object]$Labels, [string]$Name)
    $property = $Labels.PSObject.Properties[$Name]
    return if ($null -eq $property) { '' } else { [string]$property.Value }
}

function Assert-OwnedContainer {
    param([string]$ContainerId, [string]$Role)
    $items = @(ConvertFrom-Json (Invoke-Docker -Arguments @('container', 'inspect', $ContainerId)).Stdout)
    if ($items.Count -ne 1) { throw 'owned_container_identity_invalid' }
    $item = $items[0]
    if (
        (Get-LabelValue $item.Config.Labels $ownerLabel) -cne $owner -or
        (Get-LabelValue $item.Config.Labels $runLabel) -cne $runId -or
        (Get-LabelValue $item.Config.Labels $roleLabel) -cne $Role
    ) { throw 'owned_container_identity_invalid' }
    return $item
}

function Assert-ContainerBind {
    param(
        [object]$Container,
        [string]$Destination,
        [string]$ExpectedSource,
        [bool]$ReadOnly
    )
    $matches = @($Container.Mounts | Where-Object {
        [string]$_.Destination -ceq $Destination
    })
    if ($matches.Count -ne 1 -or [string]$matches[0].Type -cne 'bind') {
        throw 'container_runtime_isolation_invalid'
    }
    $actual = [IO.Path]::GetFullPath(([string]$matches[0].Source).Replace('/', '\'))
    if (
        -not $actual.Equals(
            [IO.Path]::GetFullPath($ExpectedSource),
            [StringComparison]::OrdinalIgnoreCase
        ) -or
        [bool]$matches[0].RW -eq $ReadOnly
    ) { throw 'container_runtime_isolation_invalid' }
}

function Get-ContainerSnapshot {
    $ids = @(
        ((Invoke-Docker -Arguments @(
            'container', 'ls', '--all', '--quiet', '--no-trunc'
        )).Stdout -split '\s+') | Where-Object { $_ }
    )
    $snapshot = foreach ($id in ($ids | Sort-Object)) {
        $items = @(ConvertFrom-Json (
            Invoke-Docker -Arguments @('container', 'inspect', $id)
        ).Stdout)
        if ($items.Count -ne 1) { throw 'container_snapshot_failed' }
        $item = $items[0]
        [ordered]@{
            id = [string]$item.Id
            name = [string]$item.Name
            image = [string]$item.Image
            status = [string]$item.State.Status
            running = [bool]$item.State.Running
            paused = [bool]$item.State.Paused
            restarting = [bool]$item.State.Restarting
            dead = [bool]$item.State.Dead
            exitCode = [int]$item.State.ExitCode
            restartCount = [int]$item.RestartCount
        }
    }
    return ConvertTo-Json -InputObject @($snapshot) -Compress -Depth 4
}

function Get-ProtectedImageSnapshot {
    $snapshot = [ordered]@{}
    foreach ($tag in @(
        'evelyn-fast-control-bot_api:latest',
        'evelyn-fast-control-control_page:latest',
        'evelyn-fast-control-discord_bot:latest'
    )) {
        $result = Invoke-Docker `
            -Arguments @('image', 'inspect', '--format', '{{.Id}}', $tag) `
            -AllowFailure
        $snapshot[$tag] = if ($result.ExitCode -eq 0) {
            $result.Stdout.Trim()
        } else { '' }
    }
    return $snapshot | ConvertTo-Json -Compress
}

function Test-ProductionContainersStopped {
    $running = (Invoke-Docker -Arguments @(
        'container', 'ls', '--quiet', '--no-trunc',
        '--filter', 'label=com.docker.compose.project=evelyn-fast-control'
    )).Stdout.Trim()
    if ($running) { throw 'production_container_running' }
}

function Capture-DockerHostSnapshot {
    Test-ProductionContainersStopped
    $script:baselineContainers = Get-ContainerSnapshot
    $script:protectedImageSnapshot = Get-ProtectedImageSnapshot
    $script:dockerHostSnapshotCaptured = $true
}

function Assert-DockerHostSnapshotUnchanged {
    if (-not $dockerHostSnapshotCaptured) { return }
    if (
        (Get-ContainerSnapshot) -cne $baselineContainers -or
        (Get-ProtectedImageSnapshot) -cne $protectedImageSnapshot
    ) { throw 'non_owned_docker_state_drift' }
    Test-ProductionContainersStopped
}

$manifestProgram = @'
import hashlib, os, stat
from pathlib import Path
root = Path('/app')
h = hashlib.sha256()
def put(value):
    value = value if isinstance(value, bytes) else str(value).encode('utf-8')
    h.update(len(value).to_bytes(8, 'big')); h.update(value)
for path in sorted(root.rglob('*'), key=lambda p: p.relative_to(root).as_posix().encode('utf-8')):
    rel = path.relative_to(root).as_posix()
    info = path.lstat()
    put(rel); put(info.st_mode & 0o7777)
    if stat.S_ISDIR(info.st_mode): put(b'd')
    elif stat.S_ISLNK(info.st_mode): put(b'l'); put(os.readlink(path))
    elif stat.S_ISREG(info.st_mode):
        put(b'f'); put(info.st_size)
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''): digest.update(chunk)
        put(digest.digest())
    else: raise SystemExit(65)
print(h.hexdigest())
'@

function Get-ImageSourceDigest {
    param([string]$Image, [string]$Role)
    $name = "evelyn-source-manifest-$shortRunId-$($Role.Replace('_', '-'))"
    $result = Invoke-Docker -Arguments @(
        'run', '--rm', '--name', $name,
        '--label', "$ownerLabel=$owner",
        '--label', "$runLabel=$runId",
        '--label', "$roleLabel=source-manifest",
        '--network', 'none', '--read-only',
        '--entrypoint', 'python', $Image,
        '-I', '-S', '-c', $manifestProgram
    ) -TimeoutSec 180
    $digest = $result.Stdout.Trim()
    if ($digest -notmatch '^[0-9a-f]{64}$') { throw 'source_manifest_invalid' }
    return $digest
}

function Build-CurrentSourceImages {
    $specifications = @(
        [pscustomobject]@{ Role='bot_api'; Dockerfile='docker/Dockerfile.bot-api' },
        [pscustomobject]@{ Role='control_page'; Dockerfile='docker/Dockerfile.control-page' },
        [pscustomobject]@{ Role='discord_bot'; Dockerfile='docker/Dockerfile.discord-bot' }
    )
    foreach ($specification in $specifications) {
        $specification | Add-Member NoteProperty StageTag "evelyn-discord-live-$shortRunId-$($specification.Role)-stage"
        $specification | Add-Member NoteProperty FinalTag "evelyn-discord-live-$shortRunId-$($specification.Role)"
        $null = Invoke-Docker -Arguments @(
            'build', '--quiet', '--pull=false',
            '-f', $specification.Dockerfile,
            '-t', $specification.StageTag,
            '--build-arg', "EVELYN_SOURCE_REVISION=$placeholderRevision",
            '--label', "$ownerLabel=$owner",
            '--label', "$runLabel=$runId",
            '--label', "$roleLabel=$($specification.Role)-stage",
            $projectRoot
        ) -TimeoutSec 1800
        $specification | Add-Member NoteProperty Digest (
            Get-ImageSourceDigest -Image $specification.StageTag -Role $specification.Role
        )
    }
    $digests = @($specifications | ForEach-Object { $_.Digest } | Select-Object -Unique)
    if ($digests.Count -ne 1) { throw 'current_source_snapshot_raced' }
    $script:sourceDigest = [string]$digests[0]

    $null = New-Item -ItemType Directory -Path $identityRoot -Force
    $identityDockerfile = Join-Path $identityRoot 'Dockerfile'
    [IO.File]::WriteAllText($identityDockerfile, @'
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
ARG EVELYN_SOURCE_REVISION
ARG EVELYN_OWNER
ARG EVELYN_RUN_ID
ARG EVELYN_ROLE
ENV EVELYN_IMAGE_SOURCE_REVISION=${EVELYN_SOURCE_REVISION}
LABEL ai.evelyn.owner=${EVELYN_OWNER}
LABEL ai.evelyn.run-id=${EVELYN_RUN_ID}
LABEL ai.evelyn.role=${EVELYN_ROLE}
LABEL ai.evelyn.source-digest=${EVELYN_SOURCE_REVISION}
'@, [Text.UTF8Encoding]::new($false))
    foreach ($specification in $specifications) {
        $null = Invoke-Docker -Arguments @(
            'build', '--quiet', '--pull=false', '--no-cache',
            '-f', $identityDockerfile,
            '-t', $specification.FinalTag,
            '--build-arg', "BASE_IMAGE=$($specification.StageTag)",
            '--build-arg', "EVELYN_SOURCE_REVISION=$sourceDigest",
            '--build-arg', "EVELYN_OWNER=$owner",
            '--build-arg', "EVELYN_RUN_ID=$runId",
            '--build-arg', "EVELYN_ROLE=$($specification.Role)",
            $identityRoot
        ) -TimeoutSec 600
        if ((Get-ImageSourceDigest -Image $specification.FinalTag -Role $specification.Role) -cne $sourceDigest) {
            throw 'promoted_source_manifest_mismatch'
        }
        $image = @(ConvertFrom-Json (Invoke-Docker -Arguments @('image', 'inspect', $specification.FinalTag)).Stdout)[0]
        $revisionEntries = @($image.Config.Env | Where-Object { $_ -like 'EVELYN_IMAGE_SOURCE_REVISION=*' })
        if (
            $revisionEntries.Count -ne 1 -or
            $revisionEntries[0] -cne "EVELYN_IMAGE_SOURCE_REVISION=$sourceDigest" -or
            (Get-LabelValue $image.Config.Labels $ownerLabel) -cne $owner -or
            (Get-LabelValue $image.Config.Labels $runLabel) -cne $runId
        ) { throw 'promoted_image_identity_invalid' }
    }
    return $specifications
}

function Export-ExactSourceSnapshot {
    param([string]$Image)
    $null = New-Item -ItemType Directory -Path $sourceRoot -Force
    $name = "evelyn-source-export-$shortRunId"
    $id = (Invoke-Docker -Arguments @(
        'create', '--name', $name,
        '--label', "$ownerLabel=$owner",
        '--label', "$runLabel=$runId",
        '--label', "$roleLabel=source-export",
        '--network', 'none', $Image, 'python', '-c', 'raise SystemExit(0)'
    )).Stdout.Trim()
    $null = Assert-OwnedContainer -ContainerId $id -Role 'source-export'
    try {
        $null = Invoke-Docker -Arguments @('cp', "$id`:/app/.", $sourceRoot) -TimeoutSec 300
    } finally {
        $null = Invoke-Docker -Arguments @('rm', '-f', $id) -AllowFailure
    }
}

function Write-ComposeOverlays {
    param([hashtable]$Images)
    foreach ($directory in @(
        $scratchRuntimeRoot,
        $scratchBotMemoryRoot,
        $scratchBotProfilesRoot,
        $scratchGuildSettingsRoot,
        $scratchLogsRoot
    )) {
        $null = New-Item -ItemType Directory -Path $directory -Force
    }
    [IO.File]::WriteAllBytes($scratchHostSessionPlaceholder, [byte[]]::new(0))
    $sourceExternal = (Join-Path $sourceRoot 'external').Replace('\', '/')
    $sourceAssets = (Join-Path $sourceRoot 'assets').Replace('\', '/')
    $sourceDocs = (Join-Path $sourceRoot 'docs').Replace('\', '/')
    $scratchRuntime = $scratchRuntimeRoot.Replace('\', '/')
    $scratchBotMemory = $scratchBotMemoryRoot.Replace('\', '/')
    $scratchBotProfiles = $scratchBotProfilesRoot.Replace('\', '/')
    $scratchGuildSettings = $scratchGuildSettingsRoot.Replace('\', '/')
    $scratchLogs = $scratchLogsRoot.Replace('\', '/')
    $commonLabels = @"
      ${ownerLabel}: "$owner"
      ${runLabel}: "$runId"
"@
    $preflight = @"
services:
  bot_api:
    image: "$($Images.bot_api)"
    pull_policy: never
    container_name: "$composeProject-bot-api"
    restart: "no"
    logging: { driver: "none" }
    labels:
$commonLabels      ${roleLabel}: "bot-api"
    environment:
      EVELYN_EXPECTED_SOURCE_REVISION: "$sourceDigest"
      EVELYN_CONVERSATION_ARCHIVE_ENABLED: "false"
    volumes:
      - "${scratchBotMemory}:/app/bot_memory"
      - "${scratchBotProfiles}:/app/bot_profiles"
      - "${scratchGuildSettings}:/app/guild_settings"
      - "${scratchRuntime}:/app/runtime_artifacts"
      - "${scratchLogs}:/app/logs"
      - "${sourceExternal}:/app/external:ro"
      - "${sourceAssets}:/app/assets:ro"
      - "${sourceDocs}:/app/docs:ro"
  control_page:
    image: "$($Images.control_page)"
    pull_policy: never
    container_name: "$composeProject-control-page"
    restart: "no"
    logging: { driver: "none" }
    labels:
$commonLabels      ${roleLabel}: "control-page"
    environment:
      EVELYN_EXPECTED_SOURCE_REVISION: "$sourceDigest"
      EVELYN_CONVERSATION_ARCHIVE_ENABLED: "false"
    volumes:
      - "${scratchBotMemory}:/app/bot_memory"
      - "${scratchGuildSettings}:/app/guild_settings"
      - "${scratchRuntime}:/app/runtime_artifacts"
      - "${scratchLogs}:/app/logs"
      - "${sourceExternal}:/app/external:ro"
      - "${sourceAssets}:/app/assets:ro"
      - "${sourceDocs}:/app/docs:ro"
  discord_bot:
    image: "$($Images.discord_bot)"
    pull_policy: never
  main_llm_gateway:
    image: "$($Images.bot_api)"
    pull_policy: never
"@
    $live = @"
services:
  bot_api:
    image: "$($Images.bot_api)"
    pull_policy: never
    container_name: "$composeProject-bot-api"
    restart: "no"
    logging: { driver: "none" }
    labels:
$commonLabels      ${roleLabel}: "bot-api"
    environment:
      EVELYN_EXPECTED_SOURCE_REVISION: "$sourceDigest"
    volumes:
      - "${scratchBotMemory}:/app/bot_memory"
      - "${scratchBotProfiles}:/app/bot_profiles"
      - "${scratchGuildSettings}:/app/guild_settings"
      - "${scratchRuntime}:/app/runtime_artifacts"
      - "${scratchLogs}:/app/logs"
      - "${sourceExternal}:/app/external:ro"
      - "${sourceAssets}:/app/assets:ro"
      - "${sourceDocs}:/app/docs:ro"
  discord_bot:
    image: "$($Images.discord_bot)"
    pull_policy: never
  main_llm:
    image: "$MainLlmImage"
    pull_policy: never
    container_name: "$composeProject-main-llm"
    labels:
$commonLabels      ${roleLabel}: "main-llm"
  main_llm_gateway:
    image: "$($Images.bot_api)"
    pull_policy: never
    container_name: "$composeProject-main-llm-gateway"
    labels:
$commonLabels      ${roleLabel}: "main-llm-gateway"
    environment:
      EVELYN_EXPECTED_SOURCE_REVISION: "$sourceDigest"
  router_llm:
    image: "$RouterLlmImage"
    pull_policy: never
    container_name: "$composeProject-router-llm"
    labels:
$commonLabels      ${roleLabel}: "router-llm"
    volumes:
      - "`${EVELYN_LLAMA_CPP_DIR:-`${USERPROFILE}/llama.cpp}:/llama:ro"
  sub_llm:
    image: "$SubLlmImage"
    pull_policy: never
    container_name: "$composeProject-sub-llm"
    labels:
$commonLabels      ${roleLabel}: "sub-llm"
    volumes:
      - "`${EVELYN_LLAMA_CPP_DIR:-`${USERPROFILE}/llama.cpp}:/llama:ro"
  tts:
    container_name: "$composeProject-tts"
    labels:
$commonLabels      ${roleLabel}: "tts"
  stt:
    image: "$SttImage"
    pull_policy: never
    container_name: "$composeProject-stt"
    labels:
$commonLabels      ${roleLabel}: "stt"
"@
    [IO.File]::WriteAllText($preflightOverlay, $preflight, [Text.UTF8Encoding]::new($false))
    [IO.File]::WriteAllText($liveOverlay, $live, [Text.UTF8Encoding]::new($false))
}

function Get-ComposeEnvironment {
    return [ordered]@{
        DISCORD_BOT_TOKEN = $discordInterpolationPlaceholder
        EVELYN_SOURCE_REVISION = $sourceDigest
        LOCAL_BRIDGE_STATUS_AUTH_TOKEN = $script:localBridgeStatusToken
        EVELYN_INTERNAL_CONTROL_TOKEN = $script:internalControlToken
        EVELYN_WORKSPACE_MUTATION_AUTH_TOKEN = $script:workspaceMutationToken
        EVELYN_WORKSPACE_SANDBOX_AUTH_TOKEN = $script:workspaceSandboxToken
        EVELYN_VOICE_CAPTURE_HOST_AUTH_TOKEN = $script:voiceCaptureToken
        EVELYN_VOICE_INPUT_LEASE_TOKEN = $script:voiceLeaseToken
        EVELYN_CONVERSATION_ARCHIVE_COMMAND_GUILD_ID = $GuildId
        EVELYN_CONVERSATION_ARCHIVE_ADMIN_SID = $ExpectedAdminSid
        EVELYN_CONVERSATION_ARCHIVE_ADMIN_ACCOUNT = $ExpectedAdminAccount
        EVELYN_CONVERSATION_ARCHIVE_ADMIN_DISCORD_USER_ID = $AdminDiscordUserId
        EVELYN_CONVERSATION_ARCHIVE_HOST_ATTESTATION_FILE = $validationAttestationPath
        EVELYN_CONVERSATION_ARCHIVE_HOST_SESSION_FILE = $scratchHostSessionPlaceholder
    }
}

function Get-ComposeArguments {
    param([switch]$Archive, [string]$Overlay)
    $arguments = @('compose', '-p', $composeProject, '-f', $baseCompose)
    if ($Archive) { $arguments += @('-f', $archiveCompose) }
    $arguments += @('-f', $Overlay)
    return $arguments
}

function Assert-ComposeBind {
    param(
        [object]$Service,
        [string]$Target,
        [string]$ExpectedSource,
        [bool]$ReadOnly
    )
    $matches = @($Service.volumes | Where-Object { [string]$_.target -ceq $Target })
    if ($matches.Count -ne 1 -or [string]$matches[0].type -cne 'bind') {
        throw 'compose_runtime_isolation_invalid'
    }
    $actual = [IO.Path]::GetFullPath(([string]$matches[0].source).Replace('/', '\'))
    $expected = [IO.Path]::GetFullPath($ExpectedSource)
    if (
        -not $actual.Equals($expected, [StringComparison]::OrdinalIgnoreCase) -or
        [bool]$matches[0].read_only -ne $ReadOnly
    ) { throw 'compose_runtime_isolation_invalid' }
}

function Assert-ComposeContract {
    param([switch]$Archive, [string]$Overlay, [hashtable]$Images)
    $arguments = Get-ComposeArguments -Archive:$Archive -Overlay $Overlay
    $rendered = Invoke-Docker `
        -Arguments ($arguments + @('config', '--format', 'json')) `
        -Environment (Get-ComposeEnvironment)
    $config = ConvertFrom-Json $rendered.Stdout
    if ($config.services.discord_bot.environment.DISCORD_BOT_TOKEN -cne $discordInterpolationPlaceholder) {
        throw 'compose_discord_placeholder_invalid'
    }
    if (
        [string]$config.services.bot_api.image -cne $Images.bot_api -or
        [string]$config.services.control_page.image -cne $Images.control_page -or
        [string]$config.services.main_llm_gateway.image -cne $Images.bot_api -or
        [string]$config.services.discord_bot.image -cne $Images.discord_bot
    ) { throw 'compose_image_identity_invalid' }
    foreach ($serviceName in @('bot_api', 'control_page')) {
        $service = $config.services.$serviceName
        foreach ($binding in @(
            @('/app/bot_memory', $scratchBotMemoryRoot, $false),
            @('/app/guild_settings', $scratchGuildSettingsRoot, $false),
            @('/app/runtime_artifacts', $scratchRuntimeRoot, $false),
            @('/app/logs', $scratchLogsRoot, $false),
            @('/app/external', (Join-Path $sourceRoot 'external'), $true),
            @('/app/assets', (Join-Path $sourceRoot 'assets'), $true),
            @('/app/docs', (Join-Path $sourceRoot 'docs'), $true)
        )) {
            Assert-ComposeBind `
                -Service $service `
                -Target $binding[0] `
                -ExpectedSource $binding[1] `
                -ReadOnly $binding[2]
        }
    }
    Assert-ComposeBind `
        -Service $config.services.bot_api `
        -Target '/app/bot_profiles' `
        -ExpectedSource $scratchBotProfilesRoot `
        -ReadOnly $false
    if ($Archive -and (
        [string]$config.services.bot_api.environment.EVELYN_CONVERSATION_ARCHIVE_ENABLED -cne 'true' -or
        [string]$config.services.discord_bot.environment.EVELYN_CONVERSATION_ARCHIVE_COMMAND_GUILD_ID -cne $GuildId
    )) { throw 'compose_archive_overlay_missing' }
}

function Invoke-Compose {
    param(
        [switch]$Archive,
        [string]$Overlay,
        [string[]]$Arguments,
        [int]$TimeoutSec = 900,
        [switch]$AllowFailure
    )
    $base = Get-ComposeArguments -Archive:$Archive -Overlay $Overlay
    return Invoke-Docker `
        -Arguments ($base + $Arguments) `
        -Environment (Get-ComposeEnvironment) `
        -TimeoutSec $TimeoutSec `
        -AllowFailure:$AllowFailure
}

function Resolve-HostPython {
    foreach ($candidate in @(
        (Join-Path $projectRoot '.venv-host\Scripts\python.exe'),
        (Join-Path $projectRoot '.venv\Scripts\python.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python311\python.exe')
    )) {
        if ($candidate -and (Test-Path -LiteralPath $candidate -PathType Leaf)) { return $candidate }
    }
    $command = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($null -eq $command) { $command = Get-Command python -ErrorAction SilentlyContinue }
    if ($null -eq $command) { throw 'host_python_unavailable' }
    return $command.Source
}

function Start-HostSupervisor {
    $python = Resolve-HostPython
    $startInfo = [Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $python
    $startInfo.WorkingDirectory = $sourceRoot
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    foreach ($argument in @(
        '-m', 'evelyn_core.host_supervisor',
        '--project-root', $sourceRoot,
        '--artifacts-root', $scratchRuntimeRoot
    )) { $null = $startInfo.ArgumentList.Add($argument) }
    $null = $startInfo.Environment.Remove('DISCORD_BOT_TOKEN')
    $startInfo.Environment['PYTHONPATH'] = Join-Path $sourceRoot 'evelyn_core\runtime'
    $startInfo.Environment['EVELYN_PROJECT_ROOT'] = $sourceRoot
    $startInfo.Environment['EVELYN_CORE_ROOT'] = Join-Path $sourceRoot 'evelyn_core'
    $startInfo.Environment['EVELYN_CORE_RUNTIME'] = Join-Path $sourceRoot 'evelyn_core\runtime'
    $startInfo.Environment['EVELYN_RUNTIME_ARTIFACTS_DIR'] = $scratchRuntimeRoot
    $startInfo.Environment['LOCAL_BRIDGE_BOT_API_BASE'] = 'http://127.0.0.1:8798'
    $startInfo.Environment['LOCAL_BRIDGE_STATUS_AUTH_TOKEN'] = $localBridgeStatusToken
    $startInfo.Environment['EVELYN_WORKSPACE_MUTATION_AUTH_TOKEN'] = $workspaceMutationToken
    $startInfo.Environment['EVELYN_WORKSPACE_SANDBOX_AUTH_TOKEN'] = $workspaceSandboxToken
    $startInfo.Environment['EVELYN_VOICE_CAPTURE_HOST_AUTH_TOKEN'] = $voiceCaptureToken
    $startInfo.Environment['EVELYN_VOICE_INPUT_LEASE_TOKEN'] = $voiceLeaseToken
    $startInfo.Environment['LOCAL_MIC_ENABLED'] = 'false'
    $startInfo.Environment['VOICE_INPUT_MODE'] = 'discord'
    $startInfo.Environment['LOCAL_TTS_OUTPUT_ENABLED'] = 'false'
    $startInfo.Environment['LOCAL_BRIDGE_TTS_WARMUP_ENABLED'] = 'false'
    $startInfo.Environment['VOICE_DEBUG_SAVE_AUDIO'] = 'false'
    $process = [Diagnostics.Process]::new()
    $process.StartInfo = $startInfo
    if (-not $process.Start()) { throw 'host_supervisor_start_failed' }
    return [pscustomobject]@{
        Process = $process
        Stdout = $process.StandardOutput.ReadToEndAsync()
        Stderr = $process.StandardError.ReadToEndAsync()
    }
}

function Get-BoundedJsonFile {
    param([string]$Path, [int]$MaximumBytes = 262144)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $null }
    $item = Get-Item -LiteralPath $Path -Force
    if ($item.Length -gt $MaximumBytes -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw 'runtime_evidence_invalid'
    }
    return ConvertFrom-Json ([IO.File]::ReadAllText($Path, [Text.Encoding]::UTF8))
}

function Get-ValidationAdminWatcherCount {
    $launcherName = [IO.Path]::GetFileName($attestationLauncher)
    return @(
        Get-CimInstance -ClassName Win32_Process -ErrorAction Stop |
            Where-Object {
                $_.Name -in @('powershell.exe', 'pwsh.exe') -and
                -not [string]::IsNullOrWhiteSpace([string]$_.CommandLine) -and
                ([string]$_.CommandLine).Contains($launcherName) -and
                ([string]$_.CommandLine).Contains('-HostSessionWatcher')
            }
    ).Count
}

function Get-ExactValidationAttestation {
    $payload = Get-BoundedJsonFile -Path $validationAttestationPath -MaximumBytes 65536
    $expectedNames = @(
        'schema', 'purpose', 'adminSid', 'adminAccount',
        'registeredDiscordUserId', 'hostId', 'bootId', 'bootstrapNonce',
        'issuedAt', 'expiresAt', 'elevated', 'administratorMember',
        'primary', 'replica', 'anchor', 'authAlgorithm', 'authTag'
    ) | Sort-Object
    if (
        $null -eq $payload -or
        (@($payload.PSObject.Properties.Name | Sort-Object) -join "`n") -cne
            ($expectedNames -join "`n") -or
        $payload.schema -cne 'conversation_archive.admin-host-attestation.v1' -or
        $payload.purpose -cne 'conversation_archive.admin.control' -or
        [string]$payload.adminSid -cne $ExpectedAdminSid -or
        -not ([string]$payload.adminAccount).Equals(
            $ExpectedAdminAccount,
            [StringComparison]::OrdinalIgnoreCase
        ) -or
        [string]$payload.registeredDiscordUserId -cne $AdminDiscordUserId -or
        [string]$payload.bootstrapNonce -cne $validationAttestationNonce -or
        $payload.elevated -ne $true -or
        $payload.administratorMember -ne $true -or
        $payload.authAlgorithm -cne 'hmac-sha256' -or
        [string]$payload.authTag -notmatch '^[0-9a-f]{64}$'
    ) { throw 'validation_attestation_identity_invalid' }
    return $payload
}

function Assert-ValidationAttestationReady {
    if (
        (Test-Path -LiteralPath $validationHostSessionPath) -or
        (Get-ValidationAdminWatcherCount) -ne 0
    ) { throw 'validation_admin_session_present' }
    $null = Get-ExactValidationAttestation
    $hash = (Get-FileHash -LiteralPath $validationAttestationPath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($hash -notmatch '^[0-9a-f]{64}$') { throw 'validation_attestation_hash_invalid' }
    $script:validationAttestationHash = $hash
}

function Remove-ValidationAttestation {
    if (-not $validationAttestationOwned) { return }
    if (Test-Path -LiteralPath $validationAttestationPath -PathType Leaf) {
        $null = Get-ExactValidationAttestation
        $currentHash = (Get-FileHash -LiteralPath $validationAttestationPath -Algorithm SHA256).Hash.ToLowerInvariant()
        if (
            -not [string]::IsNullOrWhiteSpace($validationAttestationHash) -and
            $currentHash -cne $validationAttestationHash
        ) { throw 'validation_attestation_changed' }
        [IO.File]::Delete($validationAttestationPath)
    }
    if (
        (Test-Path -LiteralPath $validationAttestationPath) -or
        (Test-Path -LiteralPath $validationHostSessionPath) -or
        (Get-ValidationAdminWatcherCount) -ne 0
    ) { throw 'validation_admin_artifact_cleanup_unverified' }
    $script:validationAttestationOwned = $false
}

function Invoke-LocalJson {
    param([string]$Url, [hashtable]$Headers = @{})
    try {
        return Invoke-RestMethod -Method Get -Uri $Url -Headers $Headers -TimeoutSec 5
    } catch { return $null }
}

function Wait-FreshPhysicalMicOff {
    $statusPath = Join-Path $scratchRuntimeRoot 'host_supervisor\status.json'
    $deadline = [DateTime]::UtcNow.AddSeconds(90)
    do {
        if ($hostSupervisor.Process.HasExited) { throw 'host_supervisor_exited_early' }
        $consent = Invoke-LocalJson 'http://127.0.0.1:8799/api/control-page/voice-capture-consent'
        $mic = Invoke-LocalJson `
            'http://127.0.0.1:8798/api/local-bridge/mic' `
            @{ 'X-Evelyn-Internal-Control-Token' = $internalControlToken }
        $supervisor = Get-BoundedJsonFile $statusPath
        $bridge = if ($null -ne $mic) { $mic.localBridge } else { $null }
        $request = if ($null -ne $mic) { $mic.request } else { $null }
        $nested = if ($null -ne $bridge) { $bridge.mic } else { $null }
        $stopEvidence = if ($null -ne $supervisor) { $supervisor.localBridge.voiceCaptureStop } else { $null }
        if (
            $null -ne $consent -and $consent.ok -eq $true -and
            $consent.consent.state -ceq 'inactive' -and
            $consent.consent.active -eq $false -and
            $consent.consent.recoveryRequired -eq $false -and
            $null -ne $mic -and $mic.ok -eq $true -and
            $bridge.enabled -eq $true -and $bridge.stale -eq $false -and
            $bridge.micEnabled -eq $false -and $bridge.micCaptureStopped -eq $true -and
            $nested.enabled -eq $false -and $nested.captureReady -eq $false -and
            $nested.captureActive -eq $false -and $nested.captureStopped -eq $true -and
            $request.enabled -eq $false -and
            $request.revision -eq $bridge.micControlRevision -and
            $request.actionId -ceq $bridge.micControlActionId -and
            $bridge.micControlPendingRevision -eq 0 -and
            [string]::IsNullOrEmpty([string]$bridge.micControlPendingActionId) -and
            $bridge.micControlState -ceq 'applied' -and
            $null -ne $supervisor -and $supervisor.state -ceq 'running' -and
            $supervisor.localBridge.running -eq $true -and
            $stopEvidence.state -in @('verified', 'not_required') -and
            $stopEvidence.micEnabled -eq $false -and
            $stopEvidence.captureStopped -eq $true
        ) { return }
        Start-Sleep -Milliseconds 500
    } while ([DateTime]::UtcNow -lt $deadline)
    throw 'fresh_physical_mic_off_unverified'
}

function Stop-HostSupervisor {
    if ($null -eq $hostSupervisor) { return }
    $stopPath = Join-Path $scratchRuntimeRoot 'host_supervisor\stop.request'
    [IO.Directory]::CreateDirectory((Split-Path -Parent $stopPath)) | Out-Null
    [IO.File]::WriteAllText($stopPath, '', [Text.UTF8Encoding]::new($false))
    if (-not $hostSupervisor.Process.WaitForExit(30000)) {
        try { $hostSupervisor.Process.Kill($true) } catch { }
        throw 'host_supervisor_stop_failed'
    }
    $hostSupervisor.Process.Dispose()
    $script:hostSupervisor = $null
}

function Start-DiscordWithTokenBytes {
    param([byte[]]$TokenBytes)
    Assert-EvelynDiscordTokenBytes -TokenBytes $TokenBytes
    $startInfo = [Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $dockerCommand.Source
    $startInfo.WorkingDirectory = $projectRoot
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardInput = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    $null = $startInfo.Environment.Remove('DISCORD_BOT_TOKEN')
    foreach ($argument in @(
        '--context', $dockerContextName,
        'start', '--attach', '--interactive', $discordContainerId
    )) {
        $null = $startInfo.ArgumentList.Add($argument)
    }
    $process = [Diagnostics.Process]::new()
    $process.StartInfo = $startInfo
    if (-not $process.Start()) { throw 'discord_attach_start_failed' }
    $stdout = $process.StandardOutput.ReadToEndAsync()
    $stderr = $process.StandardError.ReadToEndAsync()
    $stream = $process.StandardInput.BaseStream
    $stream.Write($TokenBytes, 0, $TokenBytes.Length)
    $stream.WriteByte(10)
    $stream.Flush()
    $stream.Close()
    return [pscustomobject]@{ Process=$process; Stdout=$stdout; Stderr=$stderr }
}

function Start-CommandGuardWithTokenBytes {
    param([byte[]]$TokenBytes)
    Assert-EvelynDiscordTokenBytes -TokenBytes $TokenBytes
    $lifetimeSec = [Math]::Min(1500, $ValidationWindowSec + 300)
    $action = [ordered]@{
        guildId = $GuildId
        statusPath = '/run/evelyn-command-guard/status.json'
        cleanupPath = '/run/evelyn-command-guard/cleanup.request'
        ownershipPath = '/run/evelyn-command-guard/ownership.json'
        runId = $runId
        publishTimeoutSec = 120
        lifetimeSec = $lifetimeSec
    } | ConvertTo-Json -Compress
    [byte[]]$actionBytes = [Text.UTF8Encoding]::new($false).GetBytes($action)
    if ($actionBytes.Length -gt 4096) { throw 'command_guard_action_invalid' }

    $startInfo = [Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $dockerCommand.Source
    $startInfo.WorkingDirectory = $projectRoot
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardInput = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    $null = $startInfo.Environment.Remove('DISCORD_BOT_TOKEN')
    foreach ($argument in @(
        '--context', $dockerContextName,
        'start', '--attach', '--interactive', $commandGuardContainerId
    )) {
        $null = $startInfo.ArgumentList.Add($argument)
    }
    $process = [Diagnostics.Process]::new()
    $process.StartInfo = $startInfo
    $processStarted = $false
    try {
        if (-not $process.Start()) { throw 'command_guard_attach_start_failed' }
        $processStarted = $true
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        $stream = $process.StandardInput.BaseStream
        $stream.Write($TokenBytes, 0, $TokenBytes.Length)
        $stream.WriteByte(10)
        $stream.Write($actionBytes, 0, $actionBytes.Length)
        $stream.WriteByte(10)
        $stream.Flush()
        $stream.Close()
        return [pscustomobject]@{ Process=$process; Stdout=$stdout; Stderr=$stderr }
    } catch {
        if ($processStarted -and -not $process.HasExited) { try { $process.Kill($true) } catch { } }
        $process.Dispose()
        throw
    } finally {
        [Array]::Clear($actionBytes, 0, $actionBytes.Length)
        $action = $null
    }
}

function New-CommandGuardContainer {
    param([string]$Image)
    $null = New-Item -ItemType Directory -Path $commandGuardRoot -Force
    $arguments = @(
        'create', '--interactive', '--name', "$composeProject-command-registry-guard",
        '--label', "$ownerLabel=$owner", '--label', "$runLabel=$runId", '--label', "$roleLabel=command-registry-guard",
        '--network', "${composeProject}_default",
        '--restart', 'no', '--log-driver', 'none', '--read-only',
        '--security-opt', 'no-new-privileges', '--cap-drop', 'ALL',
        '--pids-limit', '64', '--memory', '128m', '--cpus', '1',
        '--tmpfs', '/tmp:rw,nosuid,nodev,noexec,size=16m',
        '--mount', "type=bind,source=$commandGuardRoot,target=/run/evelyn-command-guard",
        '--env', "EVELYN_EXPECTED_SOURCE_REVISION=$sourceDigest",
        $Image, 'python', '/app/docker/discord_command_registry_guard.py'
    )
    $script:commandGuardContainerId = (Invoke-Docker -Arguments $arguments).Stdout.Trim()
    $item = Assert-OwnedContainer -ContainerId $commandGuardContainerId -Role 'command-registry-guard'
    if (
        @($item.Config.Env | Where-Object { $_ -like 'DISCORD_BOT_TOKEN=*' }).Count -ne 0 -or
        [string]$item.HostConfig.RestartPolicy.Name -cne 'no' -or
        [string]$item.HostConfig.LogConfig.Type -cne 'none' -or
        @($item.HostConfig.PortBindings.PSObject.Properties).Count -ne 0 -or
        @($item.HostConfig.DeviceRequests).Count -ne 0 -or
        @($item.Config.Cmd).Count -ne 2 -or
        [string]$item.Config.Cmd[0] -cne 'python' -or
        [string]$item.Config.Cmd[1] -cne '/app/docker/discord_command_registry_guard.py'
    ) { throw 'command_guard_container_contract_invalid' }
}

function Wait-CommandGuardState {
    param([string]$ExpectedState, [int]$TimeoutSec)
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSec)
    do {
        $marker = Get-BoundedJsonFile -Path $commandGuardStatusPath -MaximumBytes 16384
        if ($null -ne $marker) {
            if (
                $marker.schema -cne 'discord_command_registry.guard-status.v1' -or
                $marker.contentFree -ne $true -or
                $marker.state -notin @('baseline_ready', 'published_ready', 'restored', 'failed')
            ) { throw 'command_guard_status_invalid' }
            if ($marker.state -ceq 'failed') {
                throw (Get-PublicFailureCode ([string]$marker.failure))
            }
            if ($marker.state -ceq $ExpectedState) { return }
        }
        if ($null -ne $commandGuard -and $commandGuard.Process.HasExited) {
            throw 'command_guard_exited_early'
        }
        Start-Sleep -Milliseconds 250
    } while ([DateTime]::UtcNow -lt $deadline)
    throw 'command_guard_state_timeout'
}

function Request-CommandGuardCleanup {
    if ($null -eq $commandGuard) { return }
    [IO.File]::WriteAllText($commandGuardCleanupPath, '', [Text.UTF8Encoding]::new($false))
    $deadline = [DateTime]::UtcNow.AddSeconds(90)
    $restored = $false
    do {
        $marker = Get-BoundedJsonFile -Path $commandGuardStatusPath -MaximumBytes 16384
        if ($null -ne $marker) {
            if (
                $marker.schema -cne 'discord_command_registry.guard-status.v1' -or
                $marker.contentFree -ne $true -or
                $marker.state -notin @('baseline_ready', 'published_ready', 'restored', 'failed')
            ) { throw 'command_guard_status_invalid' }
            if ($marker.state -ceq 'restored') {
                $restored = $true
                break
            }
        }
        if ($commandGuard.Process.HasExited) {
            if (
                $null -ne $marker -and
                $marker.state -ceq 'failed' -and
                $marker.failure -ceq 'guard_baseline_managed_commands_present' -and
                $null -eq $discordAttach
            ) {
                $commandGuard.Process.Dispose()
                $script:commandGuard = $null
                return
            }
            if ($null -ne $marker -and $marker.state -ceq 'failed') {
                throw (Get-PublicFailureCode ([string]$marker.failure))
            }
            throw 'command_guard_cleanup_failed'
        }
        Start-Sleep -Milliseconds 250
    } while ([DateTime]::UtcNow -lt $deadline)
    if (-not $restored) { throw 'command_guard_cleanup_timeout' }
    if (-not $commandGuard.Process.WaitForExit(15000)) {
        throw 'command_guard_exit_timeout'
    }
    if ($commandGuard.Process.ExitCode -ne 0) { throw 'command_guard_cleanup_failed' }
    $commandGuard.Process.Dispose()
    $script:commandGuard = $null
}

function Wait-OperatorReceipt {
    Write-Host '[Evelyn] 대화 내용은 절대 입력하지 마세요. 실제 Discord 검증 결과로 정확히 PASS 또는 FAIL 한 줄만 입력하세요.'
    $readTask = [Console]::In.ReadLineAsync()
    $deadline = [DateTime]::UtcNow.AddSeconds($ValidationWindowSec)
    do {
        if ($discordAttach.Process.HasExited) { throw 'discord_runtime_exited_during_validation' }
        if ($readTask.IsCompleted) {
            $receipt = $readTask.GetAwaiter().GetResult()
            if ($null -eq $receipt) { throw 'window_closed_unverified' }
            if ($receipt -ceq 'PASS') {
                $script:operatorConfirmed = $true
                return
            }
            if ($receipt -ceq 'FAIL') { throw 'operator_reported_validation_failure' }
            $receipt = $null
            throw 'window_closed_unverified'
        }
        Start-Sleep -Milliseconds 200
    } while ([DateTime]::UtcNow -lt $deadline)
    throw 'window_closed_unverified'
}

function New-DiscordContainer {
    param([string]$Image)
    foreach ($directory in @(
        $scratchRuntimeRoot,
        $scratchBotMemoryRoot,
        $scratchBotProfilesRoot,
        $scratchGuildSettingsRoot,
        $scratchLogsRoot
    )) {
        $null = New-Item -ItemType Directory -Path $directory -Force
    }
    $defaultNetwork = "${composeProject}_default"
    $admissionNetwork = "${composeProject}_main_llm_admission"
    $mainEpochVolume = "${composeProject}_main_llm_epoch"
    $brokerVolume = "${composeProject}_mindcraft_llm_broker_token"
    $arguments = @(
        'create', '--interactive', '--name', "$composeProject-discord-bot",
        '--label', "$ownerLabel=$owner", '--label', "$runLabel=$runId", '--label', "$roleLabel=discord-bot",
        '--network', $defaultNetwork, '--network-alias', 'discord_bot',
        '--restart', 'no', '--stop-signal', 'SIGINT', '--log-driver', 'none',
        '--read-only', '--security-opt', 'no-new-privileges', '--cap-drop', 'ALL',
        '--pids-limit', '512', '--memory', '8g', '--cpus', '4',
        '--tmpfs', '/tmp:rw,nosuid,nodev,noexec,size=128m',
        '--tmpfs', '/run/evelyn-private/voice-debug:rw,nosuid,nodev,noexec,size=64m',
        '--mount', "type=bind,source=$scratchBotMemoryRoot,target=/app/bot_memory",
        '--mount', "type=bind,source=$scratchBotProfilesRoot,target=/app/bot_profiles",
        '--mount', "type=bind,source=$scratchGuildSettingsRoot,target=/app/guild_settings",
        '--mount', "type=bind,source=$scratchLogsRoot,target=/app/logs",
        '--mount', "type=bind,source=$scratchRuntimeRoot,target=/app/runtime_artifacts",
        '--mount', "type=bind,source=$(Join-Path $sourceRoot 'external'),target=/app/external,readonly",
        '--mount', "type=bind,source=$(Join-Path $sourceRoot 'assets'),target=/app/assets,readonly",
        '--mount', "type=bind,source=$(Join-Path $sourceRoot 'docs'),target=/app/docs,readonly",
        '--mount', "type=volume,source=$mainEpochVolume,target=/main-llm-epoch,readonly",
        '--mount', "type=volume,source=$brokerVolume,target=/mindcraft-llm-broker,readonly",
        '--mount', "type=bind,source=$(Join-Path $archiveSecretsRoot 'ingest.key'),target=/run/secrets/evelyn-conversation-archive/ingest.key,readonly",
        '--mount', "type=bind,source=$(Join-Path $archiveSecretsRoot 'user-view.key'),target=/run/secrets/evelyn-conversation-archive/user-view.key,readonly",
        '--mount', "type=bind,source=$commandGuardRoot,target=/run/evelyn-command-guard",
        '--env', "EVELYN_EXPECTED_SOURCE_REVISION=$sourceDigest",
        '--env', 'DISCORD_ENABLED=true', '--env', 'LOCAL_ONLY=false',
        '--env', 'LOCAL_MIC_ENABLED=false', '--env', 'VOICE_INPUT_MODE=discord',
        '--env', 'LOCAL_TTS_OUTPUT_ENABLED=false', '--env', 'VOICE_DEBUG_SAVE_AUDIO=false',
        '--env', "EVELYN_VOICE_INPUT_LEASE_TOKEN=$voiceLeaseToken",
        '--env', 'EVELYN_CONVERSATION_ARCHIVE_ENABLED=true',
        '--env', 'EVELYN_CONVERSATION_ARCHIVE_BOT_API_URL=http://bot_api:8798',
        '--env', "EVELYN_CONVERSATION_ARCHIVE_COMMAND_GUILD_ID=$GuildId",
        '--env', 'EVELYN_CONVERSATION_ARCHIVE_COMMAND_OWNERSHIP_LEDGER=/run/evelyn-command-guard/ownership.json',
        '--env', "EVELYN_CONVERSATION_ARCHIVE_COMMAND_RUN_ID=$runId",
        '--env', 'EVELYN_CONVERSATION_ARCHIVE_INGEST_KEY_FILE=/run/secrets/evelyn-conversation-archive/ingest.key',
        '--env', 'EVELYN_CONVERSATION_ARCHIVE_USER_VIEW_KEY_FILE=/run/secrets/evelyn-conversation-archive/user-view.key',
        '--env', 'CONTROL_PAGE_ENABLED=false', '--env', 'CONTROL_PAGE_BOT_API_HOST=bot_api',
        '--env', 'CONTROL_PAGE_BOT_API_PORT=8798',
        '--env', 'MAIN_LLM_URL=http://main_llm_gateway:9819/v1/chat/completions',
        '--env', 'LLM_SERVER_URL=http://main_llm_gateway:9819/v1/chat/completions',
        '--env', 'MAIN_LLM_ADMISSION_GATEWAY_URL=http://main_llm_gateway:9819/v1/chat/completions',
        '--env', 'MAIN_LLM_ADMISSION_CLIENT_MODE=gateway',
        '--env', 'MAIN_LLM_EPOCH_FILE=/main-llm-epoch/epoch',
        '--env', 'MAIN_LLM_IDENTITY_FILE=/main-llm-epoch/identity',
        '--env', 'MAIN_LLM_SERVER_IDENTITY_FILE=/main-llm-epoch/server-identity',
        '--env', 'MAIN_LLM_RUNTIME_TEMPLATE_IDENTITY_FILE=/main-llm-epoch/runtime-template-identity',
        '--env', 'MAIN_LLM_PROMPT_ASSETS_EMBEDDED=true',
        '--env', 'MAIN_LLM_REQUIRE_EXACT_PROMPT_ABI=true',
        '--env', 'ROUTER_LLM_URL=http://router_llm:9822/v1/chat/completions',
        '--env', 'SUMMARY_LLM_URL=http://sub_llm:9821/v1/chat/completions',
        '--env', 'OMNIVOICE_SERVER_URL=http://tts:8880', '--env', 'STT_SERVICE_URL=http://stt:8892',
        $Image, 'python', '/app/docker/discord_token_stdin_entrypoint.py'
    )
    $script:discordContainerId = (Invoke-Docker -Arguments $arguments).Stdout.Trim()
    $item = Assert-OwnedContainer -ContainerId $discordContainerId -Role 'discord-bot'
    if (
        @($item.Config.Env | Where-Object { $_ -like 'DISCORD_BOT_TOKEN=*' }).Count -ne 0 -or
        [string]$item.HostConfig.RestartPolicy.Name -cne 'no' -or
        [string]$item.HostConfig.LogConfig.Type -cne 'none' -or
        @($item.HostConfig.PortBindings.PSObject.Properties).Count -ne 0 -or
        @($item.HostConfig.DeviceRequests).Count -ne 0 -or
        @($item.Config.Cmd).Count -ne 2 -or
        [string]$item.Config.Cmd[0] -cne 'python' -or
        [string]$item.Config.Cmd[1] -cne '/app/docker/discord_token_stdin_entrypoint.py'
    ) { throw 'discord_container_secret_contract_invalid' }
    foreach ($binding in @(
        @('/app/bot_memory', $scratchBotMemoryRoot, $false),
        @('/app/bot_profiles', $scratchBotProfilesRoot, $false),
        @('/app/guild_settings', $scratchGuildSettingsRoot, $false),
        @('/app/logs', $scratchLogsRoot, $false),
        @('/app/runtime_artifacts', $scratchRuntimeRoot, $false),
        @('/app/external', (Join-Path $sourceRoot 'external'), $true),
        @('/app/assets', (Join-Path $sourceRoot 'assets'), $true),
        @('/app/docs', (Join-Path $sourceRoot 'docs'), $true)
    )) {
        Assert-ContainerBind `
            -Container $item `
            -Destination $binding[0] `
            -ExpectedSource $binding[1] `
            -ReadOnly $binding[2]
    }
    $null = Invoke-Docker -Arguments @('network', 'connect', $admissionNetwork, $discordContainerId)
}

function Stop-Discord {
    if ([string]::IsNullOrWhiteSpace($discordContainerId)) { return }
    $current = Assert-OwnedContainer -ContainerId $discordContainerId -Role 'discord-bot'
    if ($current.State.Running -eq $true) {
        $null = Invoke-Docker -Arguments @('stop', '--timeout', '30', $discordContainerId) -TimeoutSec 45
    }
    $stopped = Assert-OwnedContainer -ContainerId $discordContainerId -Role 'discord-bot'
    if ($stopped.State.Running -ne $false) { throw 'discord_container_stop_unverified' }
    if ($null -ne $discordAttach) {
        if (-not $discordAttach.Process.WaitForExit(15000)) {
            throw 'discord_attach_stop_failed'
        }
        $discordAttach.Process.Dispose()
        $script:discordAttach = $null
    }
}

function Assert-VoiceLeaseReleased {
    $deadline = [DateTime]::UtcNow.AddSeconds(20)
    $path = Join-Path $scratchRuntimeRoot 'voice_input_lease\owner.json'
    do {
        $payload = Get-BoundedJsonFile $path 16384
        if ($null -ne $payload -and $payload.schema -ceq 'voice_input_lease.owner.v1' -and $payload.state -ceq 'unowned') {
            return
        }
        Start-Sleep -Milliseconds 250
    } while ([DateTime]::UtcNow -lt $deadline)
    throw 'discord_voice_lease_release_unverified'
}

function Remove-OwnedDockerResources {
    if (-not (Test-DockerReady)) { throw 'docker_cleanup_engine_unavailable' }
    $filters = @('--filter', "label=$ownerLabel=$owner", '--filter', "label=$runLabel=$runId")
    $containerIds = @(((Invoke-Docker -Arguments (@('ps', '-aq') + $filters)).Stdout -split "`r?`n") | Where-Object { $_ })
    foreach ($id in $containerIds) {
        $inspected = @(ConvertFrom-Json (Invoke-Docker -Arguments @('inspect', $id)).Stdout)[0]
        $role = Get-LabelValue -Labels $inspected.Config.Labels -Name $roleLabel
        $null = Assert-OwnedContainer -ContainerId $id -Role $role
        $null = Invoke-Docker -Arguments @('rm', '-f', $id)
    }
    foreach ($kind in @('network', 'volume')) {
        $ids = @(((Invoke-Docker -Arguments @($kind, 'ls', '-q', '--filter', "label=com.docker.compose.project=$composeProject")).Stdout -split "`r?`n") | Where-Object { $_ })
        foreach ($id in $ids) { $null = Invoke-Docker -Arguments @($kind, 'rm', $id) }
    }
    $imageIds = @(((Invoke-Docker -Arguments (@('image', 'ls', '-aq') + $filters)).Stdout -split "`r?`n") | Where-Object { $_ } | Select-Object -Unique)
    $imageRecords = foreach ($id in $imageIds) {
        $image = @(ConvertFrom-Json (Invoke-Docker -Arguments @('image', 'inspect', $id)).Stdout)[0]
        if (
            (Get-LabelValue $image.Config.Labels $ownerLabel) -cne $owner -or
            (Get-LabelValue $image.Config.Labels $runLabel) -cne $runId
        ) { throw 'docker_image_cleanup_scope_invalid' }
        $role = Get-LabelValue $image.Config.Labels $roleLabel
        [pscustomobject]@{ Id=$id; Stage=$role.EndsWith('-stage') }
    }
    foreach ($record in @($imageRecords | Sort-Object Stage)) {
        $null = Invoke-Docker -Arguments @('image', 'rm', '-f', $record.Id)
    }

    for ($sample = 0; $sample -lt 3; $sample++) {
        $remainingContainers = @(((Invoke-Docker -Arguments (@('ps', '-aq') + $filters)).Stdout -split "`r?`n") | Where-Object { $_ })
        $remainingImages = @(((Invoke-Docker -Arguments (@('image', 'ls', '-aq') + $filters)).Stdout -split "`r?`n") | Where-Object { $_ })
        if ($remainingContainers.Count -ne 0 -or $remainingImages.Count -ne 0) {
            throw 'docker_owned_resource_cleanup_unverified'
        }
        foreach ($kind in @('network', 'volume')) {
            $remaining = @(((Invoke-Docker -Arguments @($kind, 'ls', '-q', '--filter', "label=com.docker.compose.project=$composeProject")).Stdout -split "`r?`n") | Where-Object { $_ })
            if ($remaining.Count -ne 0) { throw 'docker_owned_resource_cleanup_unverified' }
        }
        if ($sample -lt 2) { Start-Sleep -Milliseconds 1000 }
    }
}

function Clear-OwnedRunArtifacts {
    if (-not (Test-Path -LiteralPath $runRoot -PathType Container)) { return }
    $normalizedRoot = [IO.Path]::GetFullPath($runRoot).TrimEnd(
        [IO.Path]::DirectorySeparatorChar,
        [IO.Path]::AltDirectorySeparatorChar
    )
    $prefix = $normalizedRoot + [IO.Path]::DirectorySeparatorChar
    foreach ($item in @(Get-ChildItem -LiteralPath $normalizedRoot -Force)) {
        $target = [IO.Path]::GetFullPath($item.FullName)
        if (
            -not $target.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase) -or
            -not [string]::Equals(
                [IO.Path]::GetDirectoryName($target),
                $normalizedRoot,
                [StringComparison]::OrdinalIgnoreCase
            )
        ) { throw 'run_artifact_cleanup_scope_invalid' }
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            Remove-Item -LiteralPath $target -Force
        } elseif ($item.PSIsContainer) {
            [IO.Directory]::Delete($target, $true)
        } else {
            [IO.File]::Delete($target)
        }
    }
    if (@(Get-ChildItem -LiteralPath $normalizedRoot -Force).Count -ne 0) {
        throw 'run_artifact_cleanup_unverified'
    }
}

if (-not $RunLive) {
    Write-PublicResult -Ok $true -State 'live_confirmation_required'
    exit 0
}
if (
    [string]::IsNullOrWhiteSpace($GuildId) -or
    [string]::IsNullOrWhiteSpace($ExpectedAdminSid) -or
    [string]::IsNullOrWhiteSpace($ExpectedAdminAccount) -or
    [string]::IsNullOrWhiteSpace($AdminDiscordUserId)
) { throw 'live_identity_parameters_required' }
if ($null -eq $dockerCommand) { throw 'docker_cli_unavailable' }

$script:localBridgeStatusToken = New-ChannelToken
$script:internalControlToken = New-ChannelToken
$script:workspaceMutationToken = New-ChannelToken
$script:workspaceSandboxToken = New-ChannelToken
$script:voiceCaptureToken = New-ChannelToken
$script:voiceLeaseToken = New-ChannelToken
$script:validationAttestationNonce = New-ChannelToken
$runFailure = ''
$succeeded = $false

try {
    $null = New-Item -ItemType Directory -Path $runRoot -Force
    if (-not (Test-Path -LiteralPath $provisioner -PathType Leaf)) {
        throw 'archive_provisioner_missing'
    }
    foreach ($required in @('auth.key', 'ingest.key', 'user-view.key', 'proxy.key', 'minecraft.key', 'control-page-cert.pem', 'control-page-key.pem')) {
        if (-not (Test-Path -LiteralPath (Join-Path $archiveSecretsRoot $required) -PathType Leaf)) {
            throw 'archive_not_provisioned'
        }
    }
    if (
        (Test-Path -LiteralPath $validationAttestationPath) -or
        (Test-Path -LiteralPath $validationHostSessionPath) -or
        (Get-ValidationAdminWatcherCount) -ne 0
    ) { throw 'validation_admin_state_conflict' }

    Initialize-DockerContext
    Start-DockerIfNeeded
    Capture-DockerHostSnapshot
    $images = Build-CurrentSourceImages
    $imageMap = @{}
    foreach ($image in $images) { $imageMap[$image.Role] = $image.FinalTag }
    Export-ExactSourceSnapshot -Image $imageMap.control_page
    Write-ComposeOverlays -Images $imageMap
    Assert-ComposeContract -Overlay $preflightOverlay -Images $imageMap
    Assert-ComposeContract -Archive -Overlay $liveOverlay -Images $imageMap

    $null = Invoke-Compose -Overlay $preflightOverlay -Arguments @(
        'up', '-d', '--no-build', '--no-deps', '--wait', '--wait-timeout', '120', 'bot_api', 'control_page'
    ) -TimeoutSec 180
    $script:hostSupervisor = Start-HostSupervisor
    Wait-FreshPhysicalMicOff
    Stop-HostSupervisor
    $null = Invoke-Compose -Overlay $preflightOverlay -Arguments @(
        'stop', '--timeout', '130', 'control_page', 'bot_api'
    ) -TimeoutSec 180 -AllowFailure
    $null = Invoke-Compose -Overlay $preflightOverlay -Arguments @('rm', '-f', 'control_page', 'bot_api') -AllowFailure

    $null = Invoke-Compose -Archive -Overlay $liveOverlay -Arguments @(
        '--profile', 'llm', '--profile', 'tts', '--profile', 'stt',
        'up', '-d', '--no-build', '--wait', '--wait-timeout', '900',
        'main_llm', 'main_llm_gateway', 'router_llm', 'sub_llm', 'tts', 'stt'
    ) -TimeoutSec 960

    $identityPayload = [ordered]@{
        schema = 'conversation_archive.validation-identity.v1'
        adminSid = $ExpectedAdminSid
        adminAccount = $ExpectedAdminAccount
        discordUserId = $AdminDiscordUserId
        runId = $runId
        attestationNonce = $validationAttestationNonce
    } | ConvertTo-Json -Compress
    [byte[]]$identityBytes = [Text.UTF8Encoding]::new($false).GetBytes($identityPayload)
    $script:validationAttestationOwned = $true
    try {
        $null = Invoke-External `
            -FilePath (Join-Path $PSHOME 'pwsh.exe') `
            -Arguments @(
                '-NoProfile', '-File', $attestationLauncher,
                '-ValidationAttestationOnly', '-LifetimeSeconds', '90'
            ) `
            -InputBytes $identityBytes `
            -TimeoutSec 180
    }
    finally {
        [Array]::Clear($identityBytes, 0, $identityBytes.Length)
        $identityBytes = $null
        $identityPayload = $null
    }
    Assert-ValidationAttestationReady
    $null = Invoke-Compose -Archive -Overlay $liveOverlay -Arguments @(
        'up', '-d', '--no-build', '--no-deps', '--wait', '--wait-timeout', '120', 'bot_api'
    ) -TimeoutSec 180

    New-CommandGuardContainer -Image $imageMap.discord_bot
    New-DiscordContainer -Image $imageMap.discord_bot
    Import-Module -Name $credentialModule -Force -ErrorAction Stop
    $localAppDataRoot = [Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)
    $credentialRoot = Join-Path $localAppDataRoot 'Evelyn\discord-capture-credential-v1'
    [byte[]]$tokenBytes = Read-EvelynDiscordTokenCache -TrustedRoot $localAppDataRoot -CredentialRoot $credentialRoot
    try {
        $script:commandGuard = Start-CommandGuardWithTokenBytes -TokenBytes $tokenBytes
        Wait-CommandGuardState -ExpectedState 'baseline_ready' -TimeoutSec 45
        $script:discordAttach = Start-DiscordWithTokenBytes -TokenBytes $tokenBytes
    } finally {
        if ($null -ne $tokenBytes) { [Array]::Clear($tokenBytes, 0, $tokenBytes.Length) }
        $tokenBytes = $null
    }
    Wait-CommandGuardState -ExpectedState 'published_ready' -TimeoutSec 125
    if ($discordAttach.Process.HasExited) { throw 'discord_runtime_start_failed' }
    Write-Host '[Evelyn] 기존 서버에서 합성 문장으로 응답을 받은 뒤 /피드백제출의 호출자·채널·세션 범위를 검증하세요.'
    Write-Host "[Evelyn] $ValidationWindowSec 초 안에 검증하고 PASS/FAIL을 입력하세요. 실제 토큰·대화 내용·명령 원본은 결과 파일에 기록하지 않습니다."
    Wait-OperatorReceipt
    Stop-Discord
    Request-CommandGuardCleanup
    Assert-VoiceLeaseReleased

    $null = Invoke-Compose -Overlay $preflightOverlay -Arguments @(
        'up', '-d', '--no-build', '--no-deps', '--wait', '--wait-timeout', '120', 'control_page'
    ) -TimeoutSec 180
    $script:hostSupervisor = Start-HostSupervisor
    Wait-FreshPhysicalMicOff
    Stop-HostSupervisor
    $null = Invoke-Compose -Overlay $preflightOverlay -Arguments @('stop', '--timeout', '30', 'control_page') -AllowFailure
    $succeeded = $true
} catch {
    $runFailure = Get-PublicFailureCode ([string]$_.Exception.Message)
} finally {
    try { Ensure-DockerForCleanup } catch { $cleanupFailures.Add('docker_cleanup_start_failed') }
    try { Stop-Discord } catch { $cleanupFailures.Add('discord_stop_failed') }
    try { Request-CommandGuardCleanup } catch { $cleanupFailures.Add('command_registry_cleanup_failed') }
    try { Stop-HostSupervisor } catch { $cleanupFailures.Add('host_supervisor_stop_failed') }
    try {
        if (
            (Test-DockerReady) -and
            (Test-Path -LiteralPath $liveOverlay -PathType Leaf)
        ) {
            $null = Invoke-Compose -Archive -Overlay $liveOverlay -Arguments @(
                '--profile', 'llm', '--profile', 'tts', '--profile', 'stt',
                'stop', '--timeout', '130'
            ) -TimeoutSec 180 -AllowFailure
        }
    } catch { $cleanupFailures.Add('compose_stop_failed') }
    if ($null -ne $initialDockerState) {
        try { Remove-OwnedDockerResources } catch { $cleanupFailures.Add('docker_cleanup_failed') }
    }
    try { Assert-DockerHostSnapshotUnchanged } catch { $cleanupFailures.Add('non_owned_docker_state_drift') }
    try { Remove-ValidationAttestation } catch { $cleanupFailures.Add('validation_attestation_cleanup_failed') }
    try { Restore-DockerInitialState } catch { $cleanupFailures.Add('docker_state_restore_failed') }
    try { Clear-OwnedRunArtifacts } catch { $cleanupFailures.Add('run_artifact_cleanup_failed') }
    $script:localBridgeStatusToken = $null
    $script:internalControlToken = $null
    $script:workspaceMutationToken = $null
    $script:workspaceSandboxToken = $null
    $script:voiceCaptureToken = $null
    $script:voiceLeaseToken = $null
    $script:validationAttestationNonce = $null
}

if ($succeeded -and $cleanupFailures.Count -eq 0) {
    Write-PublicResult -Ok $true -State 'operator_confirmed_pass'
    exit 0
}
if ([string]::IsNullOrWhiteSpace($runFailure) -and $cleanupFailures.Count -gt 0) {
    $runFailure = 'cleanup_failed'
}
$failureState = if ($runFailure -ceq 'window_closed_unverified') {
    'window_closed_unverified'
} elseif ($cleanupFailures -contains 'command_registry_cleanup_failed') {
    'cleanup_failed'
} else {
    'validation_failed'
}
Write-PublicResult -Ok $false -State $failureState -Failure $runFailure
exit 1
