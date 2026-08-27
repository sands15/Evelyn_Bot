#Requires -Version 7.2

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9]{17,20}$')]
    [string]$ChannelId,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$')]
    [string]$AttemptId,

    [ValidateRange(60, 1800)]
    [int]$CaptureTimeoutSec = 1800
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$owner = 'evelyn.discord-capture-lab.v1'
$ownerLabel = 'ai.evelyn.owner'
$runLabel = 'ai.evelyn.run-id'
$roleLabel = 'ai.evelyn.role'
$toolHashLabel = 'ai.evelyn.capture-tool-sha256'
$markerName = '.evelyn-owned-discord-capture.json'
$markerSchema = 'evelyn.discord-capture-owned-lab.v1'
$stagingMarkerSchema = 'evelyn.discord-capture-staging.v1'
$clipCount = 10
$maxCommandOutputBytes = 1MB
$dockerWaitSec = 180
$captureOuterGraceSec = 60

$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$captureTool = Join-Path $projectRoot 'tools\discord_voice_corpus_capture.py'
$botDockerfile = Join-Path $projectRoot 'docker\Dockerfile.bot-api'
$discordDockerfile = Join-Path $projectRoot 'docker\Dockerfile.discord-capture'
$discordRequirements = Join-Path $projectRoot 'docker\requirements.discord-capture.txt'
$runtimeArtifactsRoot = Join-Path $projectRoot 'runtime_artifacts'
$validationRoot = Join-Path $runtimeArtifactsRoot 'validation'
$voiceAsrStagingRoot = Join-Path $validationRoot 'voice_asr_staging'
$labRoot = Join-Path $voiceAsrStagingRoot 'capture-labs'
$stagingRoot = Join-Path $voiceAsrStagingRoot 'captures'
$hostLocalBridgeRoot = Join-Path $runtimeArtifactsRoot 'local_bridge'
$hostInstanceLockPaths = @(
    (Join-Path $projectRoot '.evelyn_bot.lock'),
    (Join-Path $hostLocalBridgeRoot 'instance.lock')
)

$runId = [Guid]::NewGuid().ToString('N')
$shortRunId = $runId.Substring(0, 16)
$botName = "evelyn-cap-$shortRunId-bot"
$captureName = "evelyn-cap-$shortRunId-discord"
$networkName = "evelyn-cap-$shortRunId-net"
$botImage = "evelyn-capture-lab-bot-api:$runId"
$captureImage = "evelyn-capture-lab-discord:$runId"
$labAttempt = Join-Path $labRoot $runId
$labRuntime = Join-Path $labAttempt 'runtime_artifacts'
$captureHostDir = Join-Path $labRuntime 'private-capture'
$stagingAttempt = Join-Path $stagingRoot $AttemptId
$labMarker = Join-Path $labAttempt $markerName

$dockerCommand = $null
$gitCommand = $null

$sourceRevision = ''
$captureToolSha256 = ''
$initialDockerRunning = $null
$dockerStartAttemptedByLauncher = $false
$dockerStartedByLauncher = $false
$baselineContainers = ''
$protectedImageSnapshot = ''
$botImageId = ''
$captureImageId = ''
$networkId = ''
$botContainerId = ''
$captureContainerId = ''
$captureProcess = $null
$discordToken = $null
$captureMutex = $null
$captureMutexOwned = $false
$hostInstanceLocks = [System.Collections.Generic.List[System.IO.FileStream]]::new()
$captureSucceeded = $false
$runFailed = $false
$runFailureCode = 'capture_run_failed'
$hostSnapshotCaptured = $false
$ownedDockerResourcesZero = $false
$hostDockerStateUnchanged = $false
$cleanupFailures = [System.Collections.Generic.List[string]]::new()

function Invoke-ExternalProcess {
    param(
        [Parameter(Mandatory = $true)]
        [string]$FilePath,
        [Parameter(Mandatory = $true)]
        [string[]]$ArgumentList,
        [int]$TimeoutSec = 60,
        [switch]$AllowFailure,
        [string]$WorkingDirectory = $projectRoot
    )

    $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $FilePath
    $startInfo.WorkingDirectory = $WorkingDirectory
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardInput = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    foreach ($argument in $ArgumentList) {
        $null = $startInfo.ArgumentList.Add([string]$argument)
    }

    $process = [System.Diagnostics.Process]::new()
    $process.StartInfo = $startInfo
    $processStarted = $false
    try {
        if (-not $process.Start()) {
            throw 'process_start_failed'
        }
        $processStarted = $true
        $process.StandardInput.Close()
        $stdoutTask = $process.StandardOutput.ReadToEndAsync()
        $stderrTask = $process.StandardError.ReadToEndAsync()
        $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSec)
        while (-not $process.WaitForExit(250)) {
            if ([DateTime]::UtcNow -lt $deadline) {
                continue
            }
            try {
                $process.Kill($true)
            } catch {
                $process.Kill()
            }
            $process.WaitForExit()
            throw 'process_timeout'
        }
        $process.WaitForExit()
        $stdout = $stdoutTask.GetAwaiter().GetResult()
        $stderr = $stderrTask.GetAwaiter().GetResult()
        if (
            [Text.Encoding]::UTF8.GetByteCount($stdout) +
            [Text.Encoding]::UTF8.GetByteCount($stderr) -gt
            $maxCommandOutputBytes
        ) {
            throw 'process_output_too_large'
        }
        $result = [pscustomobject]@{
            ExitCode = $process.ExitCode
            Stdout = $stdout
            Stderr = $stderr
        }
        if ($result.ExitCode -ne 0 -and -not $AllowFailure.IsPresent) {
            throw 'external_command_failed'
        }
        return $result
    } finally {
        if ($processStarted -and -not $process.HasExited) {
            try {
                $process.Kill($true)
            } catch {
                try {
                    $process.Kill()
                } catch {
                }
            }
            try {
                $process.WaitForExit()
            } catch {
            }
        }
        $process.Dispose()
    }
}

function Invoke-Docker {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments,
        [int]$TimeoutSec = 60,
        [switch]$AllowFailure
    )

    return Invoke-ExternalProcess `
        -FilePath $dockerCommand.Source `
        -ArgumentList $Arguments `
        -TimeoutSec $TimeoutSec `
        -AllowFailure:$AllowFailure
}

function Invoke-Git {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)

    return Invoke-ExternalProcess `
        -FilePath $gitCommand.Source `
        -ArgumentList (@('-C', $projectRoot) + $Arguments) `
        -TimeoutSec 60
}

function Test-DockerReady {
    try {
        $result = Invoke-Docker `
            -Arguments @('version', '--format', '{{.Server.Version}}') `
            -TimeoutSec 15 `
            -AllowFailure
        return $result.ExitCode -eq 0 -and -not [string]::IsNullOrWhiteSpace(
            $result.Stdout
        )
    } catch {
        return $false
    }
}

function Wait-DockerState {
    param([Parameter(Mandatory = $true)][bool]$Running)

    $deadline = [DateTime]::UtcNow.AddSeconds($dockerWaitSec)
    do {
        if ((Test-DockerReady) -eq $Running) {
            return
        }
        Start-Sleep -Milliseconds 1000
    } while ([DateTime]::UtcNow -lt $deadline)
    throw 'docker_state_timeout'
}

function Get-DockerInitialState {
    if (Test-DockerReady) {
        return $true
    }
    $status = $null
    try {
        $status = Invoke-Docker `
            -Arguments @('desktop', 'status') `
            -TimeoutSec 10 `
            -AllowFailure
    } catch {
        if ([string]$_.Exception.Message -cne 'process_timeout') {
            throw
        }
    }
    if (
        $null -ne $status -and
        $status.ExitCode -eq 0 -and
        $status.Stdout.Trim().ToLowerInvariant() -match 'stopped|not running'
    ) {
        return $false
    }
    $desktopProcesses = @(Get-DockerDesktopOwnerProcesses)
    if ($desktopProcesses.Count -eq 0) {
        if (Test-DockerDesktopWslStopped) {
            return $false
        }
    }
    throw 'docker_initial_state_unknown'
}

function Start-DockerDesktop {
    $null = Invoke-Docker `
        -Arguments @('desktop', 'start', '--detach', '--timeout', '30') `
        -TimeoutSec 45
    Wait-DockerState -Running $true
}

function Stop-DockerDesktop {
    $null = Invoke-Docker `
        -Arguments @('desktop', 'stop', '--detach', '--timeout', '30') `
        -TimeoutSec 45
    Wait-DockerDesktopFullyStopped
}

function Assert-FixedDescendant {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Root
    )

    $resolvedPath = [System.IO.Path]::GetFullPath($Path)
    $resolvedRoot = [System.IO.Path]::GetFullPath($Root).TrimEnd(
        [System.IO.Path]::DirectorySeparatorChar,
        [System.IO.Path]::AltDirectorySeparatorChar
    )
    $prefix = $resolvedRoot + [System.IO.Path]::DirectorySeparatorChar
    if (-not $resolvedPath.StartsWith(
        $prefix,
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        throw 'owned_path_outside_fixed_root'
    }
    return $resolvedPath
}

function Get-DockerDesktopOwnerProcesses {
    return @(
        Get-Process -Name @(
            'Docker Desktop',
            'com.docker.backend',
            'com.docker.build',
            'docker-sandboxd',
            'vpnkit'
        ) -ErrorAction SilentlyContinue
    )
}

function Test-DockerDesktopWslStopped {
    $wslCommand = Get-Command wsl.exe -ErrorAction SilentlyContinue
    if ($null -eq $wslCommand) {
        throw 'docker_wsl_state_unknown'
    }
    $result = Invoke-ExternalProcess `
        -FilePath $wslCommand.Source `
        -ArgumentList @('--list', '--running', '--quiet') `
        -TimeoutSec 15 `
        -AllowFailure
    if ($result.ExitCode -ne 0) {
        throw 'docker_wsl_state_unknown'
    }
    $runningDistributions = @(
        (($result.Stdout -replace "`0", '') -split "`r?`n") |
            ForEach-Object { $_.Trim() } |
            Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
    )
    $runningDockerDistributions = @(
        $runningDistributions | Where-Object {
            $_ -in @('docker-desktop', 'docker-desktop-data')
        }
    )
    return $runningDockerDistributions.Count -eq 0
}

function Test-DockerDesktopFullyStopped {
    if (Test-DockerReady) {
        return $false
    }
    if (@(Get-DockerDesktopOwnerProcesses).Count -ne 0) {
        return $false
    }
    return Test-DockerDesktopWslStopped
}

function Wait-DockerDesktopFullyStopped {
    $deadline = [DateTime]::UtcNow.AddSeconds($dockerWaitSec)
    $stableChecks = 0
    do {
        if (Test-DockerDesktopFullyStopped) {
            $stableChecks += 1
            if ($stableChecks -ge 2) {
                return
            }
        } else {
            $stableChecks = 0
        }
        Start-Sleep -Milliseconds 500
    } while ([DateTime]::UtcNow -lt $deadline)
    throw 'docker_state_timeout'
}

function Assert-StaleDockerSocketDirectory {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string[]]$AllowedNames
    )

    if (-not (Test-Path -LiteralPath $Path)) {
        return $false
    }
    $directory = Get-Item -Force -LiteralPath $Path
    if (
        -not $directory.PSIsContainer -or
        ($directory.Attributes -band [System.IO.FileAttributes]::ReparsePoint)
    ) {
        throw 'docker_runtime_socket_directory_unsafe'
    }
    $entries = @(Get-ChildItem -Force -LiteralPath $Path)
    if ($entries.Count -eq 0) {
        return $false
    }
    foreach ($entry in $entries) {
        if (
            $AllowedNames -cnotcontains $entry.Name -or
            $entry.PSIsContainer -or
            [int64]$entry.Length -ne 0 -or
            -not (
                $entry.Attributes -band [System.IO.FileAttributes]::ReparsePoint
            ) -or
            $null -ne $entry.LinkType -or
            $null -ne $entry.Target
        ) {
            throw 'docker_runtime_socket_entry_unsafe'
        }
    }
    return $true
}

function Quarantine-StaleDockerRuntimeSockets {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet('prestart', 'poststop', 'failed-start')]
        [string]$Phase
    )

    if (-not (Test-DockerDesktopFullyStopped)) {
        throw 'docker_runtime_not_fully_stopped'
    }
    Start-Sleep -Milliseconds 500
    if (-not (Test-DockerDesktopFullyStopped)) {
        throw 'docker_runtime_not_fully_stopped'
    }

    $localAppDataRoot = [System.IO.Path]::GetFullPath(
        [Environment]::GetFolderPath(
            [Environment+SpecialFolder]::LocalApplicationData
        )
    )
    $dockerLocalRoot = Join-Path $localAppDataRoot 'Docker'
    foreach ($fixedRoot in @($localAppDataRoot, $dockerLocalRoot)) {
        $rootItem = Get-Item -Force -LiteralPath $fixedRoot
        if (
            -not $rootItem.PSIsContainer -or
            ($rootItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint)
        ) {
            throw 'docker_runtime_socket_directory_unsafe'
        }
    }
    $specifications = @(
        [pscustomobject]@{
            Source = Join-Path $localAppDataRoot 'Docker\run'
            DestinationName = 'run'
            AllowedNames = @(
                'dockerEthernetVfkit',
                'dockerInference',
                'userAnalyticsOtlpHttp.sock'
            )
        },
        [pscustomobject]@{
            Source = Join-Path $localAppDataRoot 'docker-secrets-engine'
            DestinationName = 'docker-secrets-engine'
            AllowedNames = @('engine.sock')
        }
    )
    $directoriesToMove = [System.Collections.Generic.List[object]]::new()
    foreach ($specification in $specifications) {
        $source = Assert-FixedDescendant `
            -Path $specification.Source `
            -Root $localAppDataRoot
        if (
            Assert-StaleDockerSocketDirectory `
                -Path $source `
                -AllowedNames $specification.AllowedNames
        ) {
            $directoriesToMove.Add($specification)
        }
    }
    if ($directoriesToMove.Count -eq 0) {
        return
    }

    $quarantineRoot = Assert-FixedDescendant `
        -Path (Join-Path $dockerLocalRoot (
            "evelyn-stale-runtime\$runId-$Phase"
        )) `
        -Root $localAppDataRoot
    if (Test-Path -LiteralPath $quarantineRoot) {
        throw 'docker_runtime_quarantine_exists'
    }
    $null = New-Item -ItemType Directory -Path $quarantineRoot
    foreach ($specification in $directoriesToMove) {
        $destination = Join-Path `
            $quarantineRoot `
            $specification.DestinationName
        $null = Assert-FixedDescendant `
            -Path $destination `
            -Root $quarantineRoot
        Move-Item `
            -LiteralPath $specification.Source `
            -Destination $destination
        $null = New-Item -ItemType Directory -Path $specification.Source
    }
    Write-Output (
        'docker_runtime_sockets_quarantined phase={0} directories={1}' -f
        $Phase,
        $directoriesToMove.Count
    )
}

function Assert-CaptureRootsSafe {
    foreach ($path in @(
        $runtimeArtifactsRoot,
        $validationRoot,
        $voiceAsrStagingRoot,
        $labRoot,
        $stagingRoot,
        $hostLocalBridgeRoot
    )) {
        if (-not (Test-Path -LiteralPath $path)) {
            continue
        }
        $item = Get-Item -Force -LiteralPath $path
        if (
            -not $item.PSIsContainer -or
            ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint)
        ) {
            throw 'capture_root_unsafe'
        }
    }
}

function Acquire-HostVoiceExclusion {
    Assert-CaptureRootsSafe
    if (-not (Test-Path -LiteralPath $hostLocalBridgeRoot -PathType Container)) {
        $null = New-Item -ItemType Directory -Path $hostLocalBridgeRoot -Force
    }
    Assert-CaptureRootsSafe
    foreach ($path in $hostInstanceLockPaths) {
        $stream = $null
        try {
            if (Test-Path -LiteralPath $path) {
                $item = Get-Item -Force -LiteralPath $path
                if (
                    $item.PSIsContainer -or
                    ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint)
                ) {
                    throw 'host_voice_owner_active'
                }
            }
            $stream = [System.IO.File]::Open(
                $path,
                [System.IO.FileMode]::OpenOrCreate,
                [System.IO.FileAccess]::ReadWrite,
                [System.IO.FileShare]::ReadWrite
            )
            $stream.Lock(0, 1)
            $hostInstanceLocks.Add($stream)
            $stream = $null
        } catch {
            if ($null -ne $stream) {
                $stream.Dispose()
            }
            throw 'host_voice_owner_active'
        }
    }
}

function Release-HostVoiceExclusion {
    $failed = $false
    for ($index = $hostInstanceLocks.Count - 1; $index -ge 0; $index--) {
        $stream = $hostInstanceLocks[$index]
        try {
            $stream.Unlock(0, 1)
        } catch {
            $failed = $true
        }
        try {
            $stream.Dispose()
        } catch {
            $failed = $true
        }
    }
    $hostInstanceLocks.Clear()
    if ($failed) {
        throw 'host_voice_exclusion_release_failed'
    }
}

function Set-PrivateDirectoryAcl {
    param([Parameter(Mandatory = $true)][string]$Path)

    $currentSid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
    $systemSid = [System.Security.Principal.SecurityIdentifier]::new('S-1-5-18')
    $inheritance = (
        [System.Security.AccessControl.InheritanceFlags]::ContainerInherit -bor
        [System.Security.AccessControl.InheritanceFlags]::ObjectInherit
    )
    $acl = [System.Security.AccessControl.DirectorySecurity]::new()
    $acl.SetOwner($currentSid)
    $acl.SetAccessRuleProtection($true, $false)
    foreach ($sid in @($currentSid, $systemSid)) {
        $rule = [System.Security.AccessControl.FileSystemAccessRule]::new(
            $sid,
            [System.Security.AccessControl.FileSystemRights]::FullControl,
            $inheritance,
            [System.Security.AccessControl.PropagationFlags]::None,
            [System.Security.AccessControl.AccessControlType]::Allow
        )
        $null = $acl.AddAccessRule($rule)
    }
    Set-Acl -LiteralPath $Path -AclObject $acl
}

function Write-LabMarker {
    $payload = [ordered]@{
        schema = $markerSchema
        owner = $owner
        runId = $runId
        attemptId = $AttemptId
        sourceRevision = $sourceRevision
        captureToolSha256 = $captureToolSha256
        botImageId = $botImageId
        captureImageId = $captureImageId
        networkId = $networkId
        botContainerId = $botContainerId
        captureContainerId = $captureContainerId
    } | ConvertTo-Json -Compress
    $temporary = Join-Path $labAttempt '.owner-marker.part'
    Set-Content -LiteralPath $temporary -Value $payload -NoNewline -Encoding utf8
    Move-Item -LiteralPath $temporary -Destination $labMarker -Force
}

function Test-ExactMarker {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Schema
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return $false
    }
    try {
        $value = Get-Content -Raw -LiteralPath $Path | ConvertFrom-Json
    } catch {
        return $false
    }
    return (
        [string]$value.schema -ceq $Schema -and
        [string]$value.owner -ceq $owner -and
        [string]$value.runId -ceq $runId -and
        [string]$value.attemptId -ceq $AttemptId
    )
}

function Remove-OwnedDirectory {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$AllowedRoot,
        [Parameter(Mandatory = $true)][string]$Marker,
        [Parameter(Mandatory = $true)][string]$Schema
    )

    if (-not (Test-Path -LiteralPath $Path)) {
        return
    }
    Assert-CaptureRootsSafe
    $resolved = Assert-FixedDescendant -Path $Path -Root $AllowedRoot
    $item = Get-Item -Force -LiteralPath $resolved
    if (
        -not $item.PSIsContainer -or
        ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint)
    ) {
        throw 'owned_directory_unsafe'
    }
    if (-not (Test-ExactMarker -Path $Marker -Schema $Schema)) {
        throw 'owned_directory_marker_mismatch'
    }
    Remove-Item -LiteralPath $resolved -Recurse -Force
}

function Convert-InspectArray {
    param([Parameter(Mandatory = $true)][string]$Raw)

    $parsed = $Raw | ConvertFrom-Json
    return @($parsed)
}

function Assert-OwnedImage {
    param(
        [Parameter(Mandatory = $true)][string]$ImageId,
        [Parameter(Mandatory = $true)][string]$ExpectedTag,
        [Parameter(Mandatory = $true)][string]$ExpectedRole,
        [switch]$CleanupIdentity
    )

    $items = Convert-InspectArray -Raw (
        Invoke-Docker -Arguments @('image', 'inspect', $ImageId)
    ).Stdout
    if ($items.Count -ne 1) {
        throw 'owned_image_identity_invalid'
    }
    $image = $items[0]
    $labels = $image.Config.Labels
    $sourceEntries = @(
        $image.Config.Env |
            Where-Object { $_ -like 'EVELYN_IMAGE_SOURCE_REVISION=*' }
    )
    if (
        [string]$image.Id -cne $ImageId -or
        [string]$labels.$ownerLabel -cne $owner -or
        [string]$labels.$runLabel -cne $runId -or
        [string]$labels.$roleLabel -cne $ExpectedRole -or
        @($image.RepoTags).Count -ne 1 -or
        [string]$image.RepoTags[0] -cne $ExpectedTag
    ) {
        throw 'owned_image_contract_invalid'
    }
    if (-not $CleanupIdentity -and (
        [string]$labels.'org.opencontainers.image.revision' -cne $sourceRevision -or
        $sourceEntries.Count -ne 1 -or
        [string]$sourceEntries[0] -cne "EVELYN_IMAGE_SOURCE_REVISION=$sourceRevision" -or
        (
        $ExpectedRole -ceq 'discord-capture' -and
        [string]$labels.$toolHashLabel -cne $captureToolSha256
        )
    )) {
        throw 'owned_image_contract_invalid'
    }
}

function Build-OwnedImage {
    param(
        [Parameter(Mandatory = $true)][string]$Dockerfile,
        [Parameter(Mandatory = $true)][string]$Tag,
        [Parameter(Mandatory = $true)][string]$Role
    )

    $arguments = @(
        'build',
        '--quiet',
        '--pull=false',
        '--file', $Dockerfile,
        '--tag', $Tag,
        '--build-arg', "EVELYN_SOURCE_REVISION=$sourceRevision",
        '--label', "org.opencontainers.image.revision=$sourceRevision",
        '--label', "$ownerLabel=$owner",
        '--label', "$runLabel=$runId",
        '--label', "$roleLabel=$Role"
    )
    if ($Role -ceq 'discord-capture') {
        $arguments += @('--label', "$toolHashLabel=$captureToolSha256")
    }
    $arguments += '.'
    $result = Invoke-Docker -Arguments $arguments -TimeoutSec 1800
    $imageId = $result.Stdout.Trim()
    if ($imageId -notmatch '^sha256:[0-9a-f]{64}$') {
        throw 'owned_image_id_invalid'
    }
    if ($Role -ceq 'bot-api') {
        $script:botImageId = $imageId
    } else {
        $script:captureImageId = $imageId
    }
    Assert-OwnedImage -ImageId $imageId -ExpectedTag $Tag -ExpectedRole $Role
    return $imageId
}

function Assert-OwnedNetwork {
    param(
        [Parameter(Mandatory = $true)][string]$ExpectedId,
        [switch]$CleanupIdentity
    )

    $items = Convert-InspectArray -Raw (
        Invoke-Docker -Arguments @('network', 'inspect', $ExpectedId)
    ).Stdout
    if ($items.Count -ne 1) {
        throw 'owned_network_identity_invalid'
    }
    $network = $items[0]
    if (
        [string]$network.Id -cne $ExpectedId -or
        [string]$network.Name -cne $networkName -or
        [string]$network.Labels.$ownerLabel -cne $owner -or
        [string]$network.Labels.$runLabel -cne $runId
    ) {
        throw 'owned_network_contract_invalid'
    }
    if (-not $CleanupIdentity -and $network.Internal -ne $false) {
        throw 'owned_network_contract_invalid'
    }
}

function Assert-OwnedContainer {
    param(
        [Parameter(Mandatory = $true)][string]$ContainerId,
        [Parameter(Mandatory = $true)][string]$ExpectedName,
        [Parameter(Mandatory = $true)][string]$ExpectedRole,
        [switch]$CleanupIdentity
    )

    $items = Convert-InspectArray -Raw (
        Invoke-Docker -Arguments @('container', 'inspect', $ContainerId)
    ).Stdout
    if ($items.Count -ne 1) {
        throw 'owned_container_identity_invalid'
    }
    $container = $items[0]
    $labels = $container.Config.Labels
    if (
        [string]$container.Id -cne $ContainerId -or
        [string]$container.Name -cne "/$ExpectedName" -or
        [string]$labels.$ownerLabel -cne $owner -or
        [string]$labels.$runLabel -cne $runId -or
        [string]$labels.$roleLabel -cne $ExpectedRole
    ) {
        throw 'owned_container_contract_invalid'
    }
    if (-not $CleanupIdentity -and (
        @($container.HostConfig.PortBindings.PSObject.Properties).Count -ne 0 -or
        (
            $null -ne $container.HostConfig.DeviceRequests -and
            @($container.HostConfig.DeviceRequests).Count -ne 0
        ) -or
        (
            $null -ne $container.HostConfig.Devices -and
            @($container.HostConfig.Devices).Count -ne 0
        ) -or
        [string]$container.HostConfig.RestartPolicy.Name -cne 'no'
    )) {
        throw 'owned_container_contract_invalid'
    }
}

function New-BaseContainerArguments {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Role
    )

    return @(
        'create',
        '--name', $Name,
        '--label', "$ownerLabel=$owner",
        '--label', "$runLabel=$runId",
        '--label', "$roleLabel=$Role",
        '--network', $networkName,
        '--restart', 'no',
        '--pull', 'never',
        '--read-only',
        '--security-opt', 'no-new-privileges',
        '--cap-drop', 'ALL',
        '--pids-limit', '256',
        '--memory', '1g',
        '--cpus', '2',
        '--log-driver', 'none',
        '--tmpfs', '/tmp:rw,nosuid,nodev,noexec,size=128m',
        '--mount', "type=bind,source=$labRuntime,target=/app/runtime_artifacts"
    )
}

function New-BotContainer {
    $arguments = New-BaseContainerArguments -Name $botName -Role 'bot-api'
    $arguments += @(
        '--tmpfs', '/app/bot_memory:rw,nosuid,nodev,noexec,size=64m',
        '--tmpfs', '/app/bot_profiles:rw,nosuid,nodev,noexec,size=16m',
        '--tmpfs', '/app/guild_settings:rw,nosuid,nodev,noexec,size=16m',
        '--tmpfs', '/app/logs:rw,nosuid,nodev,noexec,size=16m',
        '--env', 'EVELYN_VOICE_INPUT_LEASE_TOKEN',
        '--env', "EVELYN_EXPECTED_SOURCE_REVISION=$sourceRevision",
        '--env', 'CONTROL_PAGE_HOST=0.0.0.0',
        '--env', 'CONTROL_PAGE_PORT=8798',
        '--env', 'CONTROL_PAGE_BOT_API_PORT=8798',
        '--env', 'FAST_CONTROL_CONTINUITY_ENABLED=false',
        '--env', 'CROSS_SURFACE_CONTINUITY_ENABLED=false',
        '--env', 'DISCORD_ENABLED=false',
        '--env', 'LOCAL_ONLY=true',
        '--env', 'MINECRAFT_WORLD_LEASE_OWNER_ENABLED=false',
        '--env', 'MAIN_LLM_URL=http://127.0.0.1:9/v1/chat/completions',
        '--env', 'LLM_SERVER_URL=http://127.0.0.1:9/v1/chat/completions',
        '--env', 'ROUTER_LLM_URL=http://127.0.0.1:9/v1/chat/completions',
        '--env', 'SUMMARY_LLM_URL=http://127.0.0.1:9/v1/chat/completions',
        '--env', 'STT_SERVICE_URL=http://127.0.0.1:9',
        '--env', 'OMNIVOICE_SERVER_URL=http://127.0.0.1:9',
        $botImage
    )
    $id = (Invoke-Docker -Arguments $arguments).Stdout.Trim()
    if ($id -notmatch '^[0-9a-f]{64}$') {
        throw 'owned_container_id_invalid'
    }
    $script:botContainerId = $id
    Assert-OwnedContainer -ContainerId $id -ExpectedName $botName -ExpectedRole 'bot-api'
    return $id
}

function New-CaptureContainer {
    $arguments = New-BaseContainerArguments -Name $captureName -Role 'discord-capture'
    $arguments += @(
        '--label', "$toolHashLabel=$captureToolSha256",
        '--interactive',
        '--tmpfs', '/app/logs:rw,nosuid,nodev,noexec,size=16m',
        '--env', 'EVELYN_VOICE_INPUT_LEASE_TOKEN',
        '--env', "EVELYN_EXPECTED_SOURCE_REVISION=$sourceRevision",
        '--env', "CONTROL_PAGE_BOT_API_HOST=$botName",
        '--env', 'CONTROL_PAGE_BOT_API_PORT=8798',
        $captureImage,
        'python', '/app/tools/discord_voice_corpus_capture.py',
        '--channel-id', $ChannelId,
        '--output-dir', '/app/runtime_artifacts/private-capture',
        '--count', [string]$clipCount,
        '--ttl-seconds', [string]$CaptureTimeoutSec,
        '--token-stdin'
    )
    $id = (Invoke-Docker -Arguments $arguments).Stdout.Trim()
    if ($id -notmatch '^[0-9a-f]{64}$') {
        throw 'owned_container_id_invalid'
    }
    $script:captureContainerId = $id
    Assert-OwnedContainer `
        -ContainerId $id `
        -ExpectedName $captureName `
        -ExpectedRole 'discord-capture'
    $items = Convert-InspectArray -Raw (
        Invoke-Docker -Arguments @('container', 'inspect', $id)
    ).Stdout
    if (
        [string]$items[0].Config.Labels.$toolHashLabel -cne $captureToolSha256 -or
        @($items[0].Config.Env | Where-Object { $_ -like 'DISCORD_BOT_TOKEN=*' }).Count -ne 0
    ) {
        throw 'capture_container_secret_contract_invalid'
    }
    return $id
}

function Wait-BotApiReady {
    $probe = "import json,urllib.request; p=json.loads(urllib.request.urlopen('http://127.0.0.1:8798/health',timeout=3).read()); raise SystemExit(0 if p.get('ok') is True and p.get('role') == 'fast-control-bot-api' else 1)"
    $deadline = [DateTime]::UtcNow.AddSeconds(90)
    do {
        $result = Invoke-Docker `
            -Arguments @('exec', $botContainerId, 'python', '-c', $probe) `
            -TimeoutSec 10 `
            -AllowFailure
        if ($result.ExitCode -eq 0) {
            return
        }
        Start-Sleep -Milliseconds 1000
    } while ([DateTime]::UtcNow -lt $deadline)
    throw 'bot_api_readiness_timeout'
}

function Read-HiddenDiscordToken {
    $secureToken = $null
    $tokenValid = $false
    try {
        $secureToken = Read-Host 'Discord bot token' -AsSecureString
        if (
            $null -eq $secureToken -or
            $secureToken.Length -le 0 -or
            $secureToken.Length -gt 512
        ) {
            throw 'discord_token_invalid'
        }

        $secureToken.MakeReadOnly()
        $tokenValid = $true
        return $secureToken
    } finally {
        if (-not $tokenValid -and $null -ne $secureToken) {
            $secureToken.Dispose()
        }
    }
}

function Start-CaptureWithHiddenToken {
    param(
        [Parameter(Mandatory = $true)]
        [Security.SecureString]$SecureToken
    )

    $tokenChars = $null
    $tokenBytes = $null
    $bstr = [IntPtr]::Zero
    $process = $null
    $processStarted = $false
    $handleReturned = $false
    try {
        $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($SecureToken)
        $tokenChars = [char[]]::new($SecureToken.Length)
        [Runtime.InteropServices.Marshal]::Copy(
            $bstr,
            $tokenChars,
            0,
            $tokenChars.Length
        )
        $containsWhitespace = $false
        foreach ($tokenChar in $tokenChars) {
            if ([char]::IsWhiteSpace($tokenChar)) {
                $containsWhitespace = $true
                break
            }
        }
        $tokenBytes = [Text.Encoding]::UTF8.GetBytes($tokenChars)
        if ($tokenBytes.Length -le 0 -or $tokenBytes.Length -gt 512 -or $containsWhitespace) {
            throw 'discord_token_invalid'
        }

        $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
        $startInfo.FileName = $dockerCommand.Source
        $startInfo.WorkingDirectory = $projectRoot
        $startInfo.UseShellExecute = $false
        $startInfo.CreateNoWindow = $true
        $startInfo.RedirectStandardInput = $true
        $startInfo.RedirectStandardOutput = $true
        $startInfo.RedirectStandardError = $true
        foreach ($argument in @('start', '--attach', '--interactive', $captureContainerId)) {
            $null = $startInfo.ArgumentList.Add($argument)
        }

        $process = [System.Diagnostics.Process]::new()
        $process.StartInfo = $startInfo
        if (-not $process.Start()) {
            throw 'capture_process_start_failed'
        }
        $processStarted = $true
        $stdoutTask = $process.StandardOutput.ReadToEndAsync()
        $stderrTask = $process.StandardError.ReadToEndAsync()
        $stdinStream = $process.StandardInput.BaseStream
        $stdinStream.Write($tokenBytes, 0, $tokenBytes.Length)
        $stdinStream.WriteByte(10)
        $stdinStream.Flush()
        $stdinStream.Close()

        $handle = [pscustomobject]@{
            Process = $process
            StdoutTask = $stdoutTask
            StderrTask = $stderrTask
        }
        $handleReturned = $true
        return $handle
    } finally {
        if ($null -ne $tokenChars) {
            [Array]::Clear($tokenChars, 0, $tokenChars.Length)
        }
        if ($null -ne $tokenBytes) {
            [Array]::Clear($tokenBytes, 0, $tokenBytes.Length)
        }
        if ($bstr -ne [IntPtr]::Zero) {
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
        }
        if (-not $handleReturned -and $null -ne $process) {
            try {
                if ($processStarted) {
                    try {
                        $process.StandardInput.BaseStream.Close()
                    } catch {
                    }
                    if (-not $process.HasExited) {
                        try {
                            $process.Kill($true)
                        } catch {
                            $process.Kill()
                        }
                        $process.WaitForExit()
                    }
                }
            } catch {
                Add-CleanupFailure -Code 'capture_client_cleanup_failed'
            } finally {
                $process.Dispose()
            }
        }
    }
}

function Wait-CaptureProcess {
    param([Parameter(Mandatory = $true)]$CaptureHandle)

    $process = $CaptureHandle.Process
    $deadline = [DateTime]::UtcNow.AddSeconds(
        $CaptureTimeoutSec + $captureOuterGraceSec
    )
    while (-not $process.WaitForExit(250)) {
        if ([DateTime]::UtcNow -ge $deadline) {
            throw 'capture_outer_timeout'
        }
    }
    $process.WaitForExit()
    $stdout = $CaptureHandle.StdoutTask.GetAwaiter().GetResult()
    $stderr = $CaptureHandle.StderrTask.GetAwaiter().GetResult()
    if (
        [Text.Encoding]::UTF8.GetByteCount($stdout) +
        [Text.Encoding]::UTF8.GetByteCount($stderr) -gt 4096
    ) {
        throw 'capture_output_too_large'
    }
    if (
        $process.ExitCode -ne 0 -or
        $stdout.Trim() -cne 'capture_complete clips=10' -or
        -not [string]::IsNullOrWhiteSpace($stderr)
    ) {
        throw 'capture_process_failed'
    }
}

function Assert-VoiceLeaseReleased {
    $leasePath = Join-Path $labRuntime 'voice_input_lease\owner.json'
    if (-not (Test-Path -LiteralPath $leasePath -PathType Leaf)) {
        throw 'voice_lease_release_unconfirmed'
    }
    $file = Get-Item -Force -LiteralPath $leasePath
    if (
        ($file.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -or
        $file.Length -le 0 -or
        $file.Length -gt 65536
    ) {
        throw 'voice_lease_release_unconfirmed'
    }
    try {
        $lease = Get-Content -Raw -LiteralPath $leasePath | ConvertFrom-Json
    } catch {
        throw 'voice_lease_release_unconfirmed'
    }
    $expectedFields = @(
        'schema',
        'state',
        'source',
        'instanceId',
        'leaseId',
        'lastReleasedSource',
        'lastReleasedInstanceId',
        'lastReleasedLeaseId',
        'updatedAt'
    )
    $actualFields = @($lease.PSObject.Properties.Name | Sort-Object)
    if (
        @(Compare-Object -CaseSensitive $actualFields ($expectedFields | Sort-Object)).Count -ne 0 -or
        [string]$lease.schema -cne 'voice_input_lease.owner.v1' -or
        [string]$lease.state -cne 'unowned' -or
        [string]$lease.source -cne '' -or
        [string]$lease.instanceId -cne '' -or
        [string]$lease.leaseId -cne ''
    ) {
        throw 'voice_lease_release_unconfirmed'
    }
}

function Test-CanonicalWaveFile {
    param([Parameter(Mandatory = $true)][System.IO.FileInfo]$File)

    if (
        ($File.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -or
        $File.Length -le 44 -or
        $File.Length -gt 960044
    ) {
        throw 'capture_wav_invalid'
    }
    $stream = [System.IO.File]::Open(
        $File.FullName,
        [System.IO.FileMode]::Open,
        [System.IO.FileAccess]::Read,
        [System.IO.FileShare]::Read
    )
    try {
        $header = [byte[]]::new(44)
        if ($stream.Read($header, 0, $header.Length) -ne $header.Length) {
            throw 'capture_wav_invalid'
        }
    } finally {
        $stream.Dispose()
    }
    $ascii = [Text.Encoding]::ASCII
    $dataBytes = $File.Length - 44
    if (
        $ascii.GetString($header, 0, 4) -cne 'RIFF' -or
        $ascii.GetString($header, 8, 4) -cne 'WAVE' -or
        $ascii.GetString($header, 12, 4) -cne 'fmt ' -or
        $ascii.GetString($header, 36, 4) -cne 'data' -or
        [BitConverter]::ToUInt32($header, 4) -ne $File.Length - 8 -or
        [BitConverter]::ToUInt32($header, 16) -ne 16 -or
        [BitConverter]::ToUInt16($header, 20) -ne 1 -or
        [BitConverter]::ToUInt16($header, 22) -ne 1 -or
        [BitConverter]::ToUInt32($header, 24) -ne 16000 -or
        [BitConverter]::ToUInt32($header, 28) -ne 32000 -or
        [BitConverter]::ToUInt16($header, 32) -ne 2 -or
        [BitConverter]::ToUInt16($header, 34) -ne 16 -or
        [BitConverter]::ToUInt32($header, 40) -ne $dataBytes -or
        $dataBytes -le 0 -or
        $dataBytes -gt 960000
    ) {
        throw 'capture_wav_invalid'
    }
}

function Publish-ValidatedCapture {
    $capturePath = Assert-FixedDescendant -Path $captureHostDir -Root $labAttempt
    if (-not (Test-Path -LiteralPath $capturePath -PathType Container)) {
        throw 'capture_output_missing'
    }
    $directory = Get-Item -Force -LiteralPath $capturePath
    if ($directory.Attributes -band [System.IO.FileAttributes]::ReparsePoint) {
        throw 'capture_output_unsafe'
    }
    $items = @(Get-ChildItem -Force -LiteralPath $capturePath)
    $expectedNames = @(1..$clipCount | ForEach-Object { 'clip-{0:D4}.wav' -f $_ })
    if (
        $items.Count -ne $clipCount -or
        @($items | Where-Object { -not $_.PSIsContainer }).Count -ne $clipCount -or
        @(Compare-Object -CaseSensitive ($items.Name | Sort-Object) $expectedNames).Count -ne 0
    ) {
        throw 'capture_output_count_invalid'
    }
    $hashes = [System.Collections.Generic.List[string]]::new()
    foreach ($name in $expectedNames) {
        $file = Get-Item -Force -LiteralPath (Join-Path $capturePath $name)
        Test-CanonicalWaveFile -File $file
        $hash = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($hash -notmatch '^[0-9a-f]{64}$') {
            throw 'capture_wav_hash_invalid'
        }
        $hashes.Add($hash)
    }
    if (@($hashes | Select-Object -Unique).Count -ne $clipCount) {
        throw 'capture_wav_duplicate'
    }

    $stagingMarker = Join-Path $capturePath $markerName
    [ordered]@{
        schema = $stagingMarkerSchema
        owner = $owner
        runId = $runId
        attemptId = $AttemptId
        sourceRevision = $sourceRevision
        captureToolSha256 = $captureToolSha256
        itemCount = $clipCount
        audioSha256 = @($hashes)
    } | ConvertTo-Json -Compress | Set-Content `
        -LiteralPath $stagingMarker `
        -NoNewline `
        -Encoding utf8

    if (Test-Path -LiteralPath $stagingAttempt) {
        throw 'staging_attempt_exists'
    }
    $null = Assert-FixedDescendant -Path $stagingAttempt -Root $stagingRoot
    Move-Item -LiteralPath $capturePath -Destination $stagingAttempt
    $publishedMarker = Join-Path $stagingAttempt $markerName
    if (-not (Test-ExactMarker -Path $publishedMarker -Schema $stagingMarkerSchema)) {
        throw 'staging_marker_invalid'
    }
}

function Get-ContainerSnapshot {
    $ids = @(
        (Invoke-Docker -Arguments @('container', 'ls', '--all', '--quiet', '--no-trunc')).Stdout -split '\s+' |
            Where-Object { $_ }
    )
    $snapshot = [System.Collections.Generic.List[object]]::new()
    foreach ($id in ($ids | Sort-Object)) {
        $items = Convert-InspectArray -Raw (
            Invoke-Docker -Arguments @('container', 'inspect', $id)
        ).Stdout
        if ($items.Count -ne 1) {
            throw 'container_snapshot_failed'
        }
        $container = $items[0]
        $snapshot.Add([ordered]@{
            id = [string]$container.Id
            name = [string]$container.Name
            image = [string]$container.Image
            status = [string]$container.State.Status
            running = [bool]$container.State.Running
            paused = [bool]$container.State.Paused
            restarting = [bool]$container.State.Restarting
            dead = [bool]$container.State.Dead
            exitCode = [int]$container.State.ExitCode
            restartCount = [int]$container.RestartCount
        })
    }
    return ConvertTo-Json -InputObject @($snapshot) -Compress -Depth 4
}

function Get-ProtectedImageSnapshot {
    $tags = @(
        'evelyn-fast-control-bot_api:latest',
        'evelyn-fast-control-discord_bot:latest'
    )
    $snapshot = [ordered]@{}
    foreach ($tag in $tags) {
        $result = Invoke-Docker `
            -Arguments @('image', 'inspect', '--format', '{{.Id}}', $tag) `
            -AllowFailure
        $snapshot[$tag] = if ($result.ExitCode -eq 0) {
            $result.Stdout.Trim()
        } else {
            ''
        }
    }
    return $snapshot | ConvertTo-Json -Compress
}

function Test-ProductionContainersStopped {
    $running = (
        Invoke-Docker -Arguments @(
            'container', 'ls', '--quiet', '--no-trunc',
            '--filter', 'label=com.docker.compose.project=evelyn-fast-control'
        )
    ).Stdout.Trim()
    if ($running) {
        throw 'production_container_running'
    }
}

function Remove-OwnedContainer {
    param(
        [string]$ContainerId,
        [string]$ExpectedName,
        [string]$ExpectedRole
    )

    if (-not $ContainerId) {
        return
    }
    $exists = Invoke-Docker `
        -Arguments @('container', 'inspect', $ContainerId) `
        -AllowFailure
    if ($exists.ExitCode -ne 0) {
        return
    }
    Assert-OwnedContainer `
        -ContainerId $ContainerId `
        -ExpectedName $ExpectedName `
        -ExpectedRole $ExpectedRole `
        -CleanupIdentity
    $running = (
        Invoke-Docker -Arguments @(
            'container', 'inspect', '--format', '{{.State.Running}}', $ContainerId
        )
    ).Stdout.Trim()
    if ($running -ceq 'true') {
        $stopped = Invoke-Docker `
            -Arguments @('container', 'stop', '--time', '30', $ContainerId) `
            -TimeoutSec 45 `
            -AllowFailure
        if ($stopped.ExitCode -ne 0) {
            Assert-OwnedContainer `
                -ContainerId $ContainerId `
                -ExpectedName $ExpectedName `
                -ExpectedRole $ExpectedRole `
                -CleanupIdentity
            $null = Invoke-Docker `
                -Arguments @('container', 'kill', $ContainerId) `
                -TimeoutSec 15
        }
    }
    Assert-OwnedContainer `
        -ContainerId $ContainerId `
        -ExpectedName $ExpectedName `
        -ExpectedRole $ExpectedRole `
        -CleanupIdentity
    $null = Invoke-Docker -Arguments @('container', 'rm', $ContainerId)
}

function Remove-OwnedNetwork {
    if (-not $networkId) {
        return
    }
    $exists = Invoke-Docker `
        -Arguments @('network', 'inspect', $networkId) `
        -AllowFailure
    if ($exists.ExitCode -ne 0) {
        return
    }
    Assert-OwnedNetwork -ExpectedId $networkId -CleanupIdentity
    $null = Invoke-Docker -Arguments @('network', 'rm', $networkId)
}

function Remove-OwnedImage {
    param(
        [string]$ImageId,
        [string]$ExpectedTag,
        [string]$ExpectedRole
    )

    if (-not $ImageId) {
        return
    }
    $exists = Invoke-Docker `
        -Arguments @('image', 'inspect', $ImageId) `
        -AllowFailure
    if ($exists.ExitCode -ne 0) {
        return
    }
    Assert-OwnedImage `
        -ImageId $ImageId `
        -ExpectedTag $ExpectedTag `
        -ExpectedRole $ExpectedRole `
        -CleanupIdentity
    $null = Invoke-Docker -Arguments @('image', 'rm', $ImageId)
}

function Recover-OwnedResourceIds {
    $filters = @(
        '--filter', "label=$ownerLabel=$owner",
        '--filter', "label=$runLabel=$runId"
    )
    $invalid = $false

    $containerIds = @(
        (Invoke-Docker -Arguments (@(
            'container', 'ls', '--all', '--quiet', '--no-trunc'
        ) + $filters)).Stdout -split '\s+' | Where-Object { $_ }
    )
    if ($containerIds.Count -gt 2) {
        $invalid = $true
    }
    foreach ($id in $containerIds) {
        try {
            $container = (Convert-InspectArray -Raw (
                Invoke-Docker -Arguments @('container', 'inspect', $id)
            ).Stdout)[0]
            $role = [string]$container.Config.Labels.$roleLabel
            if ($role -ceq 'bot-api' -and [string]$container.Name -ceq "/$botName") {
                Assert-OwnedContainer -ContainerId $id -ExpectedName $botName `
                    -ExpectedRole $role -CleanupIdentity
                if ($botContainerId -and $botContainerId -cne $id) {
                    $invalid = $true
                } else {
                    $script:botContainerId = $id
                }
            } elseif (
                $role -ceq 'discord-capture' -and
                [string]$container.Name -ceq "/$captureName"
            ) {
                Assert-OwnedContainer -ContainerId $id -ExpectedName $captureName `
                    -ExpectedRole $role -CleanupIdentity
                if ($captureContainerId -and $captureContainerId -cne $id) {
                    $invalid = $true
                } else {
                    $script:captureContainerId = $id
                }
            } else {
                $invalid = $true
            }
        } catch {
            $invalid = $true
        }
    }

    $networkIds = @(
        (Invoke-Docker -Arguments (@(
            'network', 'ls', '--quiet', '--no-trunc'
        ) + $filters)).Stdout -split '\s+' | Where-Object { $_ }
    )
    if ($networkIds.Count -gt 1) {
        $invalid = $true
    }
    foreach ($id in $networkIds) {
        try {
            Assert-OwnedNetwork -ExpectedId $id -CleanupIdentity
            if ($networkId -and $networkId -cne $id) {
                $invalid = $true
            } else {
                $script:networkId = $id
            }
        } catch {
            $invalid = $true
        }
    }

    $imageIds = @(
        (Invoke-Docker -Arguments (@(
            'image', 'ls', '--all', '--quiet', '--no-trunc'
        ) + $filters)).Stdout -split '\s+' |
            Where-Object { $_ } | Select-Object -Unique
    )
    if ($imageIds.Count -gt 2) {
        $invalid = $true
    }
    foreach ($id in $imageIds) {
        try {
            $image = (Convert-InspectArray -Raw (
                Invoke-Docker -Arguments @('image', 'inspect', $id)
            ).Stdout)[0]
            $role = [string]$image.Config.Labels.$roleLabel
            if ($role -ceq 'bot-api') {
                Assert-OwnedImage -ImageId $id -ExpectedTag $botImage `
                    -ExpectedRole $role -CleanupIdentity
                if ($botImageId -and $botImageId -cne $id) {
                    $invalid = $true
                } else {
                    $script:botImageId = $id
                }
            } elseif ($role -ceq 'discord-capture') {
                Assert-OwnedImage -ImageId $id -ExpectedTag $captureImage `
                    -ExpectedRole $role -CleanupIdentity
                if ($captureImageId -and $captureImageId -cne $id) {
                    $invalid = $true
                } else {
                    $script:captureImageId = $id
                }
            } else {
                $invalid = $true
            }
        } catch {
            $invalid = $true
        }
    }

    $volumeIds = @(
        (Invoke-Docker -Arguments (@(
            'volume', 'ls', '--quiet'
        ) + $filters)).Stdout -split '\s+' | Where-Object { $_ }
    )
    if ($volumeIds.Count -ne 0 -or $invalid) {
        throw 'owned_resource_recovery_invalid'
    }
}

function Assert-OwnedDockerResourcesZero {
    $filters = @(
        '--filter', "label=$ownerLabel=$owner",
        '--filter', "label=$runLabel=$runId"
    )
    for ($sample = 0; $sample -lt 3; $sample++) {
        $containerCount = @(
            (Invoke-Docker -Arguments (@('container', 'ls', '--all', '--quiet') + $filters)).Stdout -split '\s+' |
                Where-Object { $_ }
        ).Count
        $networkCount = @(
            (Invoke-Docker -Arguments (@('network', 'ls', '--quiet') + $filters)).Stdout -split '\s+' |
                Where-Object { $_ }
        ).Count
        $volumeCount = @(
            (Invoke-Docker -Arguments (@('volume', 'ls', '--quiet') + $filters)).Stdout -split '\s+' |
                Where-Object { $_ }
        ).Count
        $imageCount = @(
            (Invoke-Docker -Arguments (@('image', 'ls', '--quiet') + $filters)).Stdout -split '\s+' |
                Where-Object { $_ }
        ).Count
        if ($containerCount -or $networkCount -or $volumeCount -or $imageCount) {
            throw 'owned_docker_resource_residual'
        }
        if ($sample -lt 2) {
            Start-Sleep -Milliseconds 1000
        }
    }
}

function Assert-NoExistingCaptureOwner {
    $filter = @('--filter', "label=$ownerLabel=$owner")
    $observed = @(
        (Invoke-Docker -Arguments (@('container', 'ls', '--all', '--quiet') + $filter)).Stdout,
        (Invoke-Docker -Arguments (@('network', 'ls', '--quiet') + $filter)).Stdout,
        (Invoke-Docker -Arguments (@('volume', 'ls', '--quiet') + $filter)).Stdout,
        (Invoke-Docker -Arguments (@('image', 'ls', '--quiet') + $filter)).Stdout
    )
    if (@($observed | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }).Count) {
        throw 'capture_owner_busy'
    }
}

function Get-AllowlistedRunFailureCode {
    param([string]$RawCode)

    $allowed = @(
        'required_source_missing',
        'docker_command_missing',
        'git_command_missing',
        'source_tree_not_clean',
        'source_revision_invalid',
        'capture_tool_hash_invalid',
        'staging_attempt_exists',
        'owned_path_outside_fixed_root',
        'capture_root_unsafe',
        'docker_initial_state_unknown',
        'docker_state_timeout',
        'docker_wsl_state_unknown',
        'docker_runtime_not_fully_stopped',
        'docker_runtime_socket_directory_unsafe',
        'docker_runtime_socket_entry_unsafe',
        'docker_runtime_quarantine_exists',
        'capture_owner_busy',
        'host_voice_owner_active',
        'production_container_running',
        'owned_image_id_invalid',
        'owned_image_identity_invalid',
        'owned_image_contract_invalid',
        'owned_network_id_invalid',
        'owned_network_identity_invalid',
        'owned_network_contract_invalid',
        'owned_container_id_invalid',
        'owned_container_identity_invalid',
        'owned_container_contract_invalid',
        'capture_container_secret_contract_invalid',
        'lease_token_generation_failed',
        'bot_api_readiness_timeout',
        'discord_token_invalid',
        'capture_process_start_failed',
        'capture_outer_timeout',
        'capture_output_too_large',
        'capture_process_failed',
        'voice_lease_release_unconfirmed',
        'capture_output_missing',
        'capture_output_unsafe',
        'capture_output_count_invalid',
        'capture_wav_invalid',
        'capture_wav_hash_invalid',
        'capture_wav_duplicate',
        'staging_marker_invalid',
        'external_command_failed',
        'process_start_failed',
        'process_timeout',
        'process_output_too_large'
    )
    if ($allowed -ccontains $RawCode) {
        return $RawCode
    }
    return 'capture_run_failed'
}

function Add-CleanupFailure {
    param([Parameter(Mandatory = $true)][string]$Code)
    $cleanupFailures.Add($Code)
}

try {
    $dockerCommand = Get-Command docker.exe -ErrorAction SilentlyContinue
    if ($null -eq $dockerCommand) {
        $dockerCommand = Get-Command docker -ErrorAction SilentlyContinue
    }
    if ($null -eq $dockerCommand) {
        throw 'docker_command_missing'
    }
    $gitCommand = Get-Command git.exe -ErrorAction SilentlyContinue
    if ($null -eq $gitCommand) {
        $gitCommand = Get-Command git -ErrorAction SilentlyContinue
    }
    if ($null -eq $gitCommand) {
        throw 'git_command_missing'
    }
    [Environment]::SetEnvironmentVariable(
        'DISCORD_BOT_TOKEN',
        $null,
        [EnvironmentVariableTarget]::Process
    )
    [Environment]::SetEnvironmentVariable(
        'EVELYN_VOICE_INPUT_LEASE_TOKEN',
        $null,
        [EnvironmentVariableTarget]::Process
    )
    foreach ($requiredPath in @(
        $captureTool,
        $botDockerfile,
        $discordDockerfile,
        $discordRequirements
    )) {
        if (-not (Test-Path -LiteralPath $requiredPath -PathType Leaf)) {
            throw 'required_source_missing'
        }
    }
    $sourceStatus = (Invoke-Git -Arguments @(
        'status', '--porcelain', '--untracked-files=all', '--', '.'
    )).Stdout
    if (-not [string]::IsNullOrWhiteSpace($sourceStatus)) {
        throw 'source_tree_not_clean'
    }
    $sourceRevision = (Invoke-Git -Arguments @('rev-parse', 'HEAD')).Stdout.Trim().ToLowerInvariant()
    if ($sourceRevision -notmatch '^[0-9a-f]{40}$') {
        throw 'source_revision_invalid'
    }
    $captureToolSha256 = (
        Get-FileHash -LiteralPath $captureTool -Algorithm SHA256
    ).Hash.ToLowerInvariant()
    if ($captureToolSha256 -notmatch '^[0-9a-f]{64}$') {
        throw 'capture_tool_hash_invalid'
    }

    $captureMutex = [Threading.Mutex]::new(
        $false,
        'Local\EvelynDiscordVoiceCorpusCaptureV1'
    )
    try {
        $captureMutexOwned = $captureMutex.WaitOne(0)
    } catch [Threading.AbandonedMutexException] {
        $captureMutexOwned = $true
    }
    if (-not $captureMutexOwned) {
        throw 'capture_owner_busy'
    }
    Acquire-HostVoiceExclusion

    if (Test-Path -LiteralPath $stagingAttempt) {
        throw 'staging_attempt_exists'
    }
    Assert-CaptureRootsSafe
    $null = Assert-FixedDescendant -Path $labAttempt -Root $labRoot
    $null = Assert-FixedDescendant -Path $stagingAttempt -Root $stagingRoot
    $null = New-Item -ItemType Directory -Path $labAttempt -ErrorAction Stop
    Assert-CaptureRootsSafe
    Set-PrivateDirectoryAcl -Path $labAttempt
    $null = New-Item -ItemType Directory -Path $labRuntime -ErrorAction Stop
    if (-not (Test-Path -LiteralPath $stagingRoot -PathType Container)) {
        $null = New-Item -ItemType Directory -Path $stagingRoot -Force
    }
    Write-LabMarker

    $initialDockerRunning = Get-DockerInitialState
    if (-not $initialDockerRunning) {
        $null = Quarantine-StaleDockerRuntimeSockets -Phase 'prestart'
        $dockerStartAttemptedByLauncher = $true
        Start-DockerDesktop
        $dockerStartedByLauncher = $true
    }
    $baselineContainers = Get-ContainerSnapshot
    $protectedImageSnapshot = Get-ProtectedImageSnapshot
    $hostSnapshotCaptured = $true
    Test-ProductionContainersStopped
    Assert-NoExistingCaptureOwner

    $botImageId = Build-OwnedImage `
        -Dockerfile $botDockerfile `
        -Tag $botImage `
        -Role 'bot-api'
    Write-LabMarker
    $captureImageId = Build-OwnedImage `
        -Dockerfile $discordDockerfile `
        -Tag $captureImage `
        -Role 'discord-capture'
    Write-LabMarker

    $networkId = (
        Invoke-Docker -Arguments @(
            'network', 'create',
            '--driver', 'bridge',
            '--label', "$ownerLabel=$owner",
            '--label', "$runLabel=$runId",
            $networkName
        )
    ).Stdout.Trim()
    if ($networkId -notmatch '^[0-9a-f]{64}$') {
        throw 'owned_network_id_invalid'
    }
    Assert-OwnedNetwork -ExpectedId $networkId
    Write-LabMarker

    $leaseBytes = [byte[]]::new(48)
    $leaseRng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $leaseRng.GetBytes($leaseBytes)
        $leaseToken = [Convert]::ToBase64String($leaseBytes).TrimEnd('=')
        if ($leaseToken.Length -lt 32) {
            throw 'lease_token_generation_failed'
        }
        [Environment]::SetEnvironmentVariable(
            'EVELYN_VOICE_INPUT_LEASE_TOKEN',
            $leaseToken,
            [EnvironmentVariableTarget]::Process
        )
        $botContainerId = New-BotContainer
        Write-LabMarker
        $captureContainerId = New-CaptureContainer
        Write-LabMarker
    } finally {
        [Environment]::SetEnvironmentVariable(
            'EVELYN_VOICE_INPUT_LEASE_TOKEN',
            $null,
            [EnvironmentVariableTarget]::Process
        )
        if ($null -ne $leaseBytes) {
            [Array]::Clear($leaseBytes, 0, $leaseBytes.Length)
        }
        if ($null -ne $leaseRng) {
            $leaseRng.Dispose()
        }
        $leaseToken = $null
    }

    $null = Invoke-Docker -Arguments @('container', 'start', $botContainerId)
    Wait-BotApiReady
    $discordToken = Read-HiddenDiscordToken
    try {
        $captureProcess = Start-CaptureWithHiddenToken -SecureToken $discordToken
    } finally {
        $discordToken.Dispose()
        $discordToken = $null
    }
    Wait-CaptureProcess -CaptureHandle $captureProcess
    Assert-VoiceLeaseReleased
    Publish-ValidatedCapture
    $captureSucceeded = $true
} catch {
    $runFailed = $true
    $runFailureCode = Get-AllowlistedRunFailureCode -RawCode (
        [string]$_.Exception.Message
    )
} finally {
    if ($null -ne $discordToken) {
        try {
            $discordToken.Dispose()
            $discordToken = $null
        } catch {
            Add-CleanupFailure -Code 'discord_token_dispose_failed'
        }
    }
    try {
        [Environment]::SetEnvironmentVariable(
            'DISCORD_BOT_TOKEN',
            $null,
            [EnvironmentVariableTarget]::Process
        )
        [Environment]::SetEnvironmentVariable(
            'EVELYN_VOICE_INPUT_LEASE_TOKEN',
            $null,
            [EnvironmentVariableTarget]::Process
        )
    } catch {
        Add-CleanupFailure -Code 'process_environment_clear_failed'
    }

    if (
        $null -ne $initialDockerRunning -and
        $initialDockerRunning -and
        -not (Test-DockerReady)
    ) {
        try {
            Start-DockerDesktop
        } catch {
            Add-CleanupFailure -Code 'docker_cleanup_start_failed'
        }
    }
    if (Test-DockerReady) {
        try {
            Recover-OwnedResourceIds
        } catch {
            Add-CleanupFailure -Code 'owned_resource_recovery_failed'
        }
    }

    if ($null -ne $captureProcess) {
        try {
            if (-not $captureProcess.Process.HasExited) {
                Remove-OwnedContainer `
                    -ContainerId $captureContainerId `
                    -ExpectedName $captureName `
                    -ExpectedRole 'discord-capture'
                $captureContainerId = ''
            }
        } catch {
            Add-CleanupFailure -Code 'capture_process_cleanup_failed'
        }
        try {
            if (
                -not $captureProcess.Process.HasExited -and
                -not $captureProcess.Process.WaitForExit(5000)
            ) {
                $captureProcess.Process.Kill($true)
                $captureProcess.Process.WaitForExit()
            }
        } catch {
            Add-CleanupFailure -Code 'capture_client_cleanup_failed'
        }
        $captureProcess.Process.Dispose()
    }
    try {
        Remove-OwnedContainer `
            -ContainerId $captureContainerId `
            -ExpectedName $captureName `
            -ExpectedRole 'discord-capture'
        $captureContainerId = ''
    } catch {
        Add-CleanupFailure -Code 'capture_container_cleanup_failed'
    }
    try {
        Remove-OwnedContainer `
            -ContainerId $botContainerId `
            -ExpectedName $botName `
            -ExpectedRole 'bot-api'
        $botContainerId = ''
    } catch {
        Add-CleanupFailure -Code 'bot_container_cleanup_failed'
    }
    try {
        Remove-OwnedNetwork
        $networkId = ''
    } catch {
        Add-CleanupFailure -Code 'network_cleanup_failed'
    }
    try {
        Remove-OwnedImage `
            -ImageId $captureImageId `
            -ExpectedTag $captureImage `
            -ExpectedRole 'discord-capture'
        $captureImageId = ''
    } catch {
        Add-CleanupFailure -Code 'capture_image_cleanup_failed'
    }
    try {
        Remove-OwnedImage `
            -ImageId $botImageId `
            -ExpectedTag $botImage `
            -ExpectedRole 'bot-api'
        $botImageId = ''
    } catch {
        Add-CleanupFailure -Code 'bot_image_cleanup_failed'
    }

    if (Test-DockerReady) {
        try {
            Assert-OwnedDockerResourcesZero
            $ownedDockerResourcesZero = $true
        } catch {
            Add-CleanupFailure -Code 'owned_resource_proof_failed'
        }
        if ($hostSnapshotCaptured) {
            try {
                if ((Get-ContainerSnapshot) -cne $baselineContainers) {
                    throw 'non_owned_container_drift'
                }
                if ((Get-ProtectedImageSnapshot) -cne $protectedImageSnapshot) {
                    throw 'protected_image_drift'
                }
                $hostDockerStateUnchanged = $true
            } catch {
                Add-CleanupFailure -Code 'non_owned_resource_drift'
            }
        }
    }
    if (Test-Path -LiteralPath $labAttempt) {
        try {
            Remove-OwnedDirectory `
                -Path $labAttempt `
                -AllowedRoot $labRoot `
                -Marker $labMarker `
                -Schema $markerSchema
        } catch {
            Add-CleanupFailure -Code 'lab_artifact_cleanup_failed'
        }
    }

    if ($null -ne $initialDockerRunning) {
        try {
            if ($initialDockerRunning) {
                if (-not (Test-DockerReady)) {
                    Start-DockerDesktop
                }
            } elseif ($dockerStartAttemptedByLauncher) {
                if (-not (Test-DockerDesktopFullyStopped)) {
                    Stop-DockerDesktop
                }
                $quarantinePhase = if ($dockerStartedByLauncher) {
                    'poststop'
                } else {
                    'failed-start'
                }
                $null = Quarantine-StaleDockerRuntimeSockets `
                    -Phase $quarantinePhase
            }
            if ($initialDockerRunning) {
                if (-not (Test-DockerReady)) {
                    throw 'docker_restore_failed'
                }
            } elseif (-not (Test-DockerDesktopFullyStopped)) {
                throw 'docker_restore_failed'
            }
        } catch {
            Add-CleanupFailure -Code 'docker_state_restore_failed'
        }
    }

    try {
        Release-HostVoiceExclusion
    } catch {
        Add-CleanupFailure -Code 'host_voice_exclusion_release_failed'
    }
    if ($captureMutexOwned) {
        try {
            $captureMutex.ReleaseMutex()
        } catch {
            Add-CleanupFailure -Code 'capture_mutex_release_failed'
        }
        $captureMutexOwned = $false
    }
    if ($null -ne $captureMutex) {
        try {
            $captureMutex.Dispose()
        } catch {
            Add-CleanupFailure -Code 'capture_mutex_dispose_failed'
        }
    }

    if (
        ($runFailed -or -not $captureSucceeded -or $cleanupFailures.Count -ne 0) -and
        (Test-Path -LiteralPath $stagingAttempt)
    ) {
        try {
            Remove-OwnedDirectory `
                -Path $stagingAttempt `
                -AllowedRoot $stagingRoot `
                -Marker (Join-Path $stagingAttempt $markerName) `
                -Schema $stagingMarkerSchema
        } catch {
            Add-CleanupFailure -Code 'failed_staging_cleanup_failed'
        }
    }
}

if ($runFailed -or -not $captureSucceeded -or $cleanupFailures.Count -ne 0) {
    if ($cleanupFailures.Count -ne 0) {
        $runFailureCode = 'capture_cleanup_failed'
    }
    Write-Error "discord_capture_failed code=$runFailureCode"
    exit 1
}

Write-Host "capture_complete clips=$clipCount attempt=$AttemptId"
exit 0
