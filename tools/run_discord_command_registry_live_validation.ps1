#Requires -Version 7.2

[CmdletBinding()]
param(
    [switch]$RunLive,
    [switch]$TargetGuildFromStdin,
    [switch]$TargetGuildNameFromStdin
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$schema = 'discord_command_registry.live-validation.v1'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$pythonScript = Join-Path $PSScriptRoot 'run_discord_command_registry_live_validation.py'
$credentialModule = Join-Path $PSScriptRoot 'discord_capture_credential.psm1'

function Public-Result(
    [string]$State,
    [string]$Failure,
    [bool]$PublishedVerified = $false,
    [bool]$RestoredVerified = $false,
    [bool]$RecoveryRequired = $true
) {
    [ordered]@{
        schema = $schema
        state = $State
        contentFree = $true
        publishedVerified = $PublishedVerified
        restoredVerified = $RestoredVerified
        recoveryRequired = $RecoveryRequired
        failure = $Failure
    } | ConvertTo-Json -Compress
}

function Read-BoundedTargetInput([int]$MaximumLength) {
    $maximumBytes = $MaximumLength * 4
    [byte[]]$buffer = [byte[]]::new($maximumBytes + 3)
    try {
        $stream = [Console]::OpenStandardInput()
        $count = 0
        while ($true) {
            $value = $stream.ReadByte()
            if ($value -eq -1) { break }
            if ($count -ge $maximumBytes + 2) { throw 'target_input_too_large' }
            $buffer[$count] = [byte]$value
            $count += 1
        }
        $contentLength = $count
        if (
            $contentLength -ge 2 -and
            $buffer[$contentLength - 2] -eq 13 -and
            $buffer[$contentLength - 1] -eq 10
        ) {
            $contentLength -= 2
        } elseif ($contentLength -ge 1 -and $buffer[$contentLength - 1] -eq 10) {
            $contentLength -= 1
        }
        if (
            $contentLength -lt 1 -or $contentLength -gt $maximumBytes -or
            @($buffer[0..($contentLength - 1)] | Where-Object {
                $_ -in @([byte]0, [byte]10, [byte]13)
            }).Count -ne 0
        ) { throw 'target_input_invalid' }
        $raw = [Text.UTF8Encoding]::new($false, $true).GetString(
            $buffer, 0, $contentLength
        )
        if (
            $raw.Length -gt $MaximumLength -or
            @($raw.ToCharArray() | Where-Object { [char]::IsControl($_) }).Count -ne 0
        ) { throw 'target_input_invalid' }
        return $raw
    } finally {
        [Array]::Clear($buffer, 0, $buffer.Length)
    }
}

function Test-ExactPrivateDirectoryAcl(
    [string]$Path,
    [Security.Principal.SecurityIdentifier]$CurrentSid,
    [Security.Principal.SecurityIdentifier]$SystemSid
) {
    $item = Get-Item -Force -LiteralPath $Path
    if (-not $item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        return $false
    }
    $verifiedAcl = Get-Acl -LiteralPath $Path
    $verifiedOwner = $verifiedAcl.GetOwner([Security.Principal.SecurityIdentifier])
    $verifiedRules = @($verifiedAcl.GetAccessRules(
        $true, $false, [Security.Principal.SecurityIdentifier]
    ))
    $expectedSids = @($CurrentSid.Value, $SystemSid.Value) | Sort-Object
    $actualSids = @($verifiedRules | ForEach-Object { $_.IdentityReference.Value }) | Sort-Object
    return (
        $verifiedAcl.AreAccessRulesProtected -and
        $verifiedOwner.Value -ceq $CurrentSid.Value -and
        $verifiedRules.Count -eq 2 -and
        @(Compare-Object $expectedSids $actualSids).Count -eq 0 -and
        @($verifiedRules | Where-Object {
            $_.IsInherited -or
            $_.AccessControlType -ne [Security.AccessControl.AccessControlType]::Allow -or
            $_.FileSystemRights -ne [Security.AccessControl.FileSystemRights]::FullControl -or
            $_.InheritanceFlags -ne [Security.AccessControl.InheritanceFlags]'ContainerInherit, ObjectInherit' -or
            $_.PropagationFlags -ne [Security.AccessControl.PropagationFlags]::None
        }).Count -eq 0
    )
}

function Set-ExactPrivateDirectoryAcl(
    [string]$Path,
    [Security.Principal.SecurityIdentifier]$CurrentSid,
    [Security.Principal.SecurityIdentifier]$SystemSid
) {
    $acl = [Security.AccessControl.DirectorySecurity]::new()
    $acl.SetOwner($CurrentSid)
    $acl.SetAccessRuleProtection($true, $false)
    foreach ($sid in @($CurrentSid, $SystemSid)) {
        $rule = [Security.AccessControl.FileSystemAccessRule]::new(
            $sid,
            [Security.AccessControl.FileSystemRights]::FullControl,
            [Security.AccessControl.InheritanceFlags]'ContainerInherit, ObjectInherit',
            [Security.AccessControl.PropagationFlags]::None,
            [Security.AccessControl.AccessControlType]::Allow
        )
        $null = $acl.AddAccessRule($rule)
    }
    # Set-Acl can try to write the SACL under PowerShell 7 and fail without
    # SeSecurityPrivilege. Apply only the directory owner/DACL represented by
    # DirectorySecurity; live validation must work as the current user.
    [IO.FileSystemAclExtensions]::SetAccessControl(
        [IO.DirectoryInfo]::new($Path),
        $acl
    )
    if (-not (Test-ExactPrivateDirectoryAcl $Path $CurrentSid $SystemSid)) {
        throw 'private_directory_acl_invalid'
    }
}

function Test-ExactPrivateFileAcl(
    [string]$Path,
    [Security.Principal.SecurityIdentifier]$CurrentSid,
    [Security.Principal.SecurityIdentifier]$SystemSid
) {
    $item = Get-Item -Force -LiteralPath $Path
    if ($item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        return $false
    }
    $acl = Get-Acl -LiteralPath $Path
    $owner = $acl.GetOwner([Security.Principal.SecurityIdentifier])
    $rules = @($acl.GetAccessRules(
        $true, $true, [Security.Principal.SecurityIdentifier]
    ))
    $expectedSids = @($CurrentSid.Value, $SystemSid.Value) | Sort-Object
    $actualSids = @($rules | ForEach-Object { $_.IdentityReference.Value }) | Sort-Object
    return (
        $owner.Value -ceq $CurrentSid.Value -and
        $rules.Count -eq 2 -and
        @(Compare-Object $expectedSids $actualSids).Count -eq 0 -and
        @($rules | Where-Object {
            $_.AccessControlType -ne [Security.AccessControl.AccessControlType]::Allow -or
            $_.FileSystemRights -ne [Security.AccessControl.FileSystemRights]::FullControl -or
            $_.InheritanceFlags -ne [Security.AccessControl.InheritanceFlags]::None -or
            $_.PropagationFlags -ne [Security.AccessControl.PropagationFlags]::None
        }).Count -eq 0
    )
}

function Invoke-RegistryChild(
    [string]$Mode,
    [string]$GuildId,
    [string]$RunId,
    [string]$LedgerPath,
    [string]$LeasePath,
    [byte[]]$TokenBytes,
    [string]$GuildName = ''
) {
    [byte[]]$localActionBytes = $null
    $process = $null
    try {
        $action = [ordered]@{
            guildId = $GuildId
            runId = $RunId
            ledgerPath = $LedgerPath
            leasePath = $LeasePath
            mode = $Mode
        }
        if (-not [string]::IsNullOrEmpty($GuildName)) {
            $action['guildName'] = $GuildName
        }
        $action = $action | ConvertTo-Json -Compress
        $localActionBytes = [Text.UTF8Encoding]::new($false).GetBytes($action)
        $python = Get-Command python -CommandType Application -ErrorAction Stop |
            Select-Object -First 1
        $startInfo = [Diagnostics.ProcessStartInfo]::new()
        $startInfo.FileName = $python.Source
        $startInfo.WorkingDirectory = $projectRoot
        $startInfo.UseShellExecute = $false
        $startInfo.CreateNoWindow = $true
        $startInfo.RedirectStandardInput = $true
        $startInfo.RedirectStandardOutput = $true
        $startInfo.RedirectStandardError = $true
        $null = $startInfo.Environment.Remove('DISCORD_BOT_TOKEN')
        foreach ($name in @(
            'PYTHONPATH', 'PYTHONHOME', 'PYTHONSTARTUP', 'PYTHONINSPECT',
            'PYTHONWARNINGS', 'PYTHONBREAKPOINT'
        )) { $null = $startInfo.Environment.Remove($name) }
        $null = $startInfo.ArgumentList.Add('-I')
        $null = $startInfo.ArgumentList.Add($pythonScript)
        $process = [Diagnostics.Process]::new()
        $process.StartInfo = $startInfo
        if (-not $process.Start()) { throw 'python_start_failed' }
        $stdoutTask = $process.StandardOutput.ReadToEndAsync()
        $stderrTask = $process.StandardError.ReadToEndAsync()
        $stream = $process.StandardInput.BaseStream
        $stream.Write($TokenBytes, 0, $TokenBytes.Length)
        $stream.WriteByte(10)
        $stream.Write($localActionBytes, 0, $localActionBytes.Length)
        $stream.WriteByte(10)
        $stream.Flush()
        $stream.Close()
        $process.WaitForExit()
        $stdout = $stdoutTask.GetAwaiter().GetResult()
        $stderr = $stderrTask.GetAwaiter().GetResult()
        if ($stderr.Length -ne 0 -or $stdout.Length -gt 4096) { throw 'python_output_invalid' }
        $parsed = $stdout | ConvertFrom-Json -ErrorAction Stop
        $expectedProperties = @(
            'contentFree', 'failure', 'publishedVerified', 'recoveryRequired',
            'restoredVerified', 'schema', 'state'
        )
        $actualProperties = @($parsed.PSObject.Properties.Name | Sort-Object)
        $allowedFailures = @(
            '', 'input_invalid', 'validation_failed', 'interrupted',
            'cleanup_failed', 'restore_mismatch', 'recovery_failed'
        )
        if (
            @(Compare-Object $expectedProperties $actualProperties).Count -ne 0 -or
            $parsed.schema -isnot [string] -or [string]$parsed.schema -cne $schema -or
            $parsed.contentFree -isnot [bool] -or $parsed.contentFree -ne $true -or
            $parsed.publishedVerified -isnot [bool] -or
            $parsed.restoredVerified -isnot [bool] -or
            $parsed.recoveryRequired -isnot [bool] -or
            $parsed.state -isnot [string] -or
            [string]$parsed.state -cnotin @('passed', 'failed') -or
            $parsed.failure -isnot [string] -or
            [string]$parsed.failure -cnotin $allowedFailures
        ) { throw 'python_output_invalid' }
        return [pscustomobject]@{
            ExitCode = $process.ExitCode
            Result = $parsed
        }
    } finally {
        if ($null -ne $localActionBytes) {
            [Array]::Clear($localActionBytes, 0, $localActionBytes.Length)
        }
        if ($null -ne $process) {
            if (-not $process.HasExited) { $process.WaitForExit() }
            $process.Dispose()
        }
    }
}

function Resolve-ExactGuildIdByName(
    [string]$GuildName,
    [byte[]]$TokenBytes
) {
    [byte[]]$nameBytes = $null
    $process = $null
    try {
        $script:launcherPhase = 'target_resolution_encode'
        $nameBytes = [Text.UTF8Encoding]::new($false, $true).GetBytes($GuildName)
        if ($nameBytes.Length -gt 400) { throw 'target_guild_name_invalid' }
        $script:launcherPhase = 'target_resolution_command'
        $python = Get-Command python -CommandType Application -ErrorAction Stop |
            Select-Object -First 1
        $startInfo = [Diagnostics.ProcessStartInfo]::new()
        $startInfo.FileName = $python.Source
        $startInfo.WorkingDirectory = $projectRoot
        $startInfo.UseShellExecute = $false
        $startInfo.CreateNoWindow = $true
        $startInfo.RedirectStandardInput = $true
        $startInfo.RedirectStandardOutput = $true
        $startInfo.RedirectStandardError = $true
        $null = $startInfo.Environment.Remove('DISCORD_BOT_TOKEN')
        foreach ($name in @(
            'PYTHONPATH', 'PYTHONHOME', 'PYTHONSTARTUP', 'PYTHONINSPECT',
            'PYTHONWARNINGS', 'PYTHONBREAKPOINT'
        )) { $null = $startInfo.Environment.Remove($name) }
        $null = $startInfo.ArgumentList.Add('-I')
        $null = $startInfo.ArgumentList.Add($pythonScript)
        $null = $startInfo.ArgumentList.Add('--resolve-guild-name')
        $process = [Diagnostics.Process]::new()
        $process.StartInfo = $startInfo
        $script:launcherPhase = 'target_resolution_start'
        if (-not $process.Start()) { throw 'python_start_failed' }
        $stdoutTask = $process.StandardOutput.ReadToEndAsync()
        $stderrTask = $process.StandardError.ReadToEndAsync()
        $stream = $process.StandardInput.BaseStream
        $stream.Write($TokenBytes, 0, $TokenBytes.Length)
        $stream.WriteByte(10)
        $stream.Write($nameBytes, 0, $nameBytes.Length)
        $stream.WriteByte(10)
        $stream.Flush()
        $stream.Close()
        $script:launcherPhase = 'target_resolution_wait'
        $process.WaitForExit()
        $stdout = $stdoutTask.GetAwaiter().GetResult()
        $stderr = $stderrTask.GetAwaiter().GetResult()
        if ($process.ExitCode -ne 0) {
            $script:launcherPhase = switch ($process.ExitCode) {
                64 { 'target_resolution_child_input' }
                65 { 'target_resolution_child_target' }
                69 { 'target_resolution_child_http' }
                default { 'target_resolution_child_internal' }
            }
            throw 'target_guild_name_unavailable'
        }
        if ($stderr.Length -ne 0) {
            $script:launcherPhase = 'target_resolution_stderr'
            throw 'target_guild_name_unavailable'
        }
        if ($stdout -cnotmatch '^[1-9]\d{16,19}\r?\n$') {
            $script:launcherPhase = 'target_resolution_output'
            throw 'target_guild_name_unavailable'
        }
        $script:launcherPhase = 'target_resolution_complete'
        return $stdout.TrimEnd([char[]]"`r`n")
    } finally {
        if ($null -ne $nameBytes) { [Array]::Clear($nameBytes, 0, $nameBytes.Length) }
        if ($null -ne $process) {
            if (-not $process.HasExited) { $process.WaitForExit() }
            $process.Dispose()
        }
    }
}

function Assert-RecoveryResult($Invocation) {
    $result = $Invocation.Result
    if (
        $Invocation.ExitCode -ne 0 -or
        [string]$result.state -cne 'passed' -or
        $result.publishedVerified -ne $false -or
        $result.restoredVerified -ne $true -or
        $result.recoveryRequired -ne $false -or
        [string]$result.failure -cne ''
    ) { throw 'recovery_failed' }
}

function Assert-NormalResult($Invocation) {
    $result = $Invocation.Result
    if (
        (
            [string]$result.state -ceq 'passed' -and
            ($Invocation.ExitCode -ne 0 -or -not $result.publishedVerified -or
             -not $result.restoredVerified -or $result.recoveryRequired -or
             [string]$result.failure -cne '')
        ) -or
        (
            [string]$result.state -ceq 'failed' -and
            ($Invocation.ExitCode -eq 0 -or [string]$result.failure -ceq '' -or
             $result.recoveryRequired -ne (-not $result.restoredVerified))
        )
    ) { throw 'python_output_invalid' }
}

function Get-ValidatedRunFiles(
    [string]$Root,
    [string]$RunId,
    [Security.Principal.SecurityIdentifier]$CurrentSid,
    [Security.Principal.SecurityIdentifier]$SystemSid
) {
    if (-not (Test-ExactPrivateDirectoryAcl $Root $CurrentSid $SystemSid)) {
        throw 'stale_run_acl_invalid'
    }
    $allowedNames = @('lease.lock', 'ownership.json', ".ownership.json.$RunId.tmp")
    $children = @(Get-ChildItem -Force -LiteralPath $Root)
    if (@($children | Where-Object { $_.Name -cnotin $allowedNames }).Count -ne 0) {
        throw 'stale_run_contents_invalid'
    }
    foreach ($child in $children) {
        if ($child.PSIsContainer -or ($child.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw 'stale_run_contents_invalid'
        }
        if (-not (Test-ExactPrivateFileAcl $child.FullName $CurrentSid $SystemSid)) {
            throw 'stale_run_file_acl_invalid'
        }
    }
    $lease = Join-Path $Root 'lease.lock'
    if (-not (Test-Path -LiteralPath $lease -PathType Leaf)) { throw 'stale_run_lease_missing' }
    if ((Get-Item -Force -LiteralPath $lease).Length -ne 1) { throw 'stale_run_lease_invalid' }
    return [pscustomobject]@{
        Lease = $lease
        Ledger = Join-Path $Root 'ownership.json'
        Atomic = Join-Path $Root ".ownership.json.$RunId.tmp"
    }
}

function Remove-QuarantinedRun(
    [string]$Root,
    [string]$RunId,
    [string]$StateBoundary,
    [Security.Principal.SecurityIdentifier]$CurrentSid,
    [Security.Principal.SecurityIdentifier]$SystemSid
) {
    $resolved = [IO.Path]::GetFullPath($Root)
    $item = Get-Item -Force -LiteralPath $resolved
    if (
        -not $resolved.StartsWith($StateBoundary, [StringComparison]::OrdinalIgnoreCase) -or
        (Split-Path -Parent $resolved) -cne $StateBoundary.TrimEnd(
            [IO.Path]::DirectorySeparatorChar
        ) -or
        $item.Name -cnotmatch "^recovered-$RunId-[0-9a-f]{32}$" -or
        -not $item.PSIsContainer -or
        ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)
    ) { throw 'cleanup_target_invalid' }
    if (-not (Test-ExactPrivateDirectoryAcl $resolved $CurrentSid $SystemSid)) {
        throw 'cleanup_target_invalid'
    }
    $allowedNames = @('lease.lock', 'ownership.json', ".ownership.json.$RunId.tmp")
    $children = @(Get-ChildItem -Force -LiteralPath $resolved)
    if (@($children | Where-Object {
        $_.Name -cnotin $allowedNames -or $_.PSIsContainer -or
        ($_.Attributes -band [IO.FileAttributes]::ReparsePoint)
    }).Count -ne 0) { throw 'cleanup_target_invalid' }
    foreach ($child in $children) {
        if (-not (Test-ExactPrivateFileAcl $child.FullName $CurrentSid $SystemSid)) {
            throw 'cleanup_target_invalid'
        }
        [IO.File]::Delete($child.FullName)
    }
    if (@(Get-ChildItem -Force -LiteralPath $resolved).Count -ne 0) {
        throw 'cleanup_target_invalid'
    }
    [IO.Directory]::Delete($resolved, $false)
    if (Test-Path -LiteralPath $resolved) { throw 'cleanup_failed' }
}

if (-not $RunLive) {
    Public-Result -State 'confirmation_required' -Failure '' -RecoveryRequired $false
    exit 0
}

$tokenBytes = $null
$mutex = $null
$mutexOwned = $false
$publicOutput = Public-Result -State 'failed' -Failure 'launcher_failed'
$exitCode = 1
$currentRunRoot = ''
$currentRunId = ''
$currentFiles = $null
$normalInvocation = $null
$stateLeaseStream = $null
$stateLeaseLocked = $false
$launcherPhase = 'target'

try {
    if ($TargetGuildFromStdin -and $TargetGuildNameFromStdin) {
        throw 'target_modes_conflict'
    }
    $GuildId = 'single'
    $GuildName = ''
    if ($TargetGuildFromStdin) {
        $guildInput = Read-BoundedTargetInput 20
        if ($guildInput -cnotmatch '^[1-9]\d{16,19}$') {
            throw 'target_guild_input_invalid'
        }
        $GuildId = $guildInput
    } elseif ($TargetGuildNameFromStdin) {
        $GuildName = Read-BoundedTargetInput 100
    }
    $launcherPhase = 'mutex'
    $mutex = [Threading.Mutex]::new(
        $false,
        'Local\EvelynCommandRegistryLiveValidationV1'
    )
    try {
        $mutexOwned = $mutex.WaitOne(0)
    } catch [Threading.AbandonedMutexException] {
        $mutexOwned = $true
    }
    if (-not $mutexOwned) { throw 'validation_already_running' }

    $launcherPhase = 'state_parent'
    $currentSid = [Security.Principal.WindowsIdentity]::GetCurrent().User
    $systemSid = [Security.Principal.SecurityIdentifier]::new('S-1-5-18')
    $localAppDataRoot = [Environment]::GetFolderPath(
        [Environment+SpecialFolder]::LocalApplicationData
    )
    $stateParent = [IO.Path]::GetFullPath((Join-Path $localAppDataRoot 'Evelyn'))
    if (Test-Path -LiteralPath $stateParent) {
        $stateParentItem = Get-Item -Force -LiteralPath $stateParent
        if (
            -not $stateParentItem.PSIsContainer -or
            ($stateParentItem.Attributes -band [IO.FileAttributes]::ReparsePoint)
        ) { throw 'state_parent_unsafe' }
    } else {
        $null = [IO.Directory]::CreateDirectory($stateParent)
    }
    $stateParentItem = Get-Item -Force -LiteralPath $stateParent
    if (
        -not $stateParentItem.PSIsContainer -or
        ($stateParentItem.Attributes -band [IO.FileAttributes]::ReparsePoint)
    ) { throw 'state_parent_unsafe' }
    $stateBase = [IO.Path]::GetFullPath((
        Join-Path $stateParent 'command-registry-live-validation-v1'
    ))
    if ((Split-Path -Parent $stateBase) -cne $stateParent) { throw 'state_root_unsafe' }
    if (Test-Path -LiteralPath $stateBase) {
        $stateBaseItem = Get-Item -Force -LiteralPath $stateBase
        if (
            -not $stateBaseItem.PSIsContainer -or
            ($stateBaseItem.Attributes -band [IO.FileAttributes]::ReparsePoint)
        ) { throw 'state_root_unsafe' }
    } else {
        $null = [IO.Directory]::CreateDirectory($stateBase)
    }
    $launcherPhase = 'state_acl'
    Set-ExactPrivateDirectoryAcl $stateBase $currentSid $systemSid
    $launcherPhase = 'state_boundary'
    $stateBoundary = $stateBase.TrimEnd([IO.Path]::DirectorySeparatorChar) + (
        [IO.Path]::DirectorySeparatorChar
    )
    $launcherPhase = 'state_lease'
    $stateLeasePath = Join-Path $stateBase 'state.lock'
    if (Test-Path -LiteralPath $stateLeasePath) {
        $stateLeaseItem = Get-Item -Force -LiteralPath $stateLeasePath
        if (
            $stateLeaseItem.PSIsContainer -or
            ($stateLeaseItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
            $stateLeaseItem.Length -ne 1
        ) { throw 'state_lease_invalid' }
    } else {
        [IO.File]::WriteAllBytes($stateLeasePath, [byte[]]@(49))
    }
    $stateLeaseItem = Get-Item -Force -LiteralPath $stateLeasePath
    if (
        $stateLeaseItem.PSIsContainer -or
        ($stateLeaseItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
        $stateLeaseItem.Length -ne 1
    ) { throw 'state_lease_invalid' }
    if (-not (Test-ExactPrivateFileAcl $stateLeasePath $currentSid $systemSid)) {
        throw 'state_lease_acl_invalid'
    }
    $launcherPhase = 'state_lock'
    $stateLeaseStream = [IO.File]::Open(
        $stateLeasePath,
        [IO.FileMode]::Open,
        [IO.FileAccess]::ReadWrite,
        [IO.FileShare]::None
    )
    $stateLeaseStream.Lock(0, 1)
    $stateLeaseLocked = $true

    $launcherPhase = 'credential'
    Import-Module -Name $credentialModule -Force -ErrorAction Stop
    $credentialRoot = Join-Path $localAppDataRoot 'Evelyn\discord-capture-credential-v1'
    [byte[]]$tokenBytes = Read-EvelynDiscordTokenCache `
        -TrustedRoot $localAppDataRoot `
        -CredentialRoot $credentialRoot
    if ($null -eq $tokenBytes) { throw 'credential_missing' }
    Assert-EvelynDiscordTokenBytes -TokenBytes $tokenBytes

    if ($TargetGuildNameFromStdin) {
        $launcherPhase = 'target_resolution'
        $GuildId = Resolve-ExactGuildIdByName $GuildName $tokenBytes
    }

    $launcherPhase = 'stale_recovery'
    $stateFiles = @(Get-ChildItem -Force -File -LiteralPath $stateBase)
    if ($stateFiles.Count -ne 1 -or $stateFiles[0].Name -cne 'state.lock') {
        throw 'state_root_contents_invalid'
    }
    $allStateRoots = @(Get-ChildItem -Force -Directory -LiteralPath $stateBase)
    $quarantineRoots = @($allStateRoots | Where-Object {
        $_.Name -cmatch '^recovered-([0-9a-f]{32})-[0-9a-f]{32}$'
    })
    foreach ($quarantineRoot in $quarantineRoots) {
        if ($quarantineRoot.Name -cmatch '^recovered-([0-9a-f]{32})-[0-9a-f]{32}$') {
            $quarantineRunId = $Matches[1]
        } else {
            throw 'state_root_contents_invalid'
        }
        Remove-QuarantinedRun `
            $quarantineRoot.FullName $quarantineRunId $stateBoundary $currentSid $systemSid
    }
    $staleRoots = @(Get-ChildItem -Force -Directory -LiteralPath $stateBase)
    foreach ($staleRootItem in $staleRoots) {
        if ($staleRootItem.Name -cnotmatch '^evelyn-command-registry-([0-9a-f]{32})$') {
            throw 'state_root_contents_invalid'
        }
        $staleRunId = $Matches[1]
        $staleRoot = [IO.Path]::GetFullPath($staleRootItem.FullName)
        if (
            -not $staleRoot.StartsWith($stateBoundary, [StringComparison]::OrdinalIgnoreCase) -or
            (Split-Path -Parent $staleRoot) -cne $stateBase
        ) { throw 'stale_run_path_invalid' }
        if (-not (Test-ExactPrivateDirectoryAcl $staleRoot $currentSid $systemSid)) {
            throw 'stale_run_acl_invalid'
        }
        $stagingChildren = @(Get-ChildItem -Force -LiteralPath $staleRoot)
        $stagingLeaseItem = @($stagingChildren | Where-Object { $_.Name -ceq 'lease.lock' })
        $stagingAtomicName = ".ownership.json.$staleRunId.tmp"
        $stagingAtomicItem = @($stagingChildren | Where-Object {
            $_.Name -ceq $stagingAtomicName
        })
        $stagingFileAclsValid = @($stagingChildren | Where-Object {
            -not (Test-ExactPrivateFileAcl $_.FullName $currentSid $systemSid)
        }).Count -eq 0
        $preMutationStaging = (
            $stagingFileAclsValid -and
            @($stagingChildren | Where-Object {
                $_.Name -cnotin @('lease.lock', $stagingAtomicName)
            }).Count -eq 0 -and
            $stagingAtomicItem.Count -le 1 -and
            @($stagingAtomicItem | Where-Object {
                $_.PSIsContainer -or
                ($_.Attributes -band [IO.FileAttributes]::ReparsePoint)
            }).Count -eq 0 -and
            (
                $stagingLeaseItem.Count -eq 0 -or
                (
                    $stagingLeaseItem.Count -eq 1 -and
                    -not $stagingLeaseItem[0].PSIsContainer -and
                    -not ($stagingLeaseItem[0].Attributes -band [IO.FileAttributes]::ReparsePoint) -and
                    $stagingLeaseItem[0].Length -eq 0
                )
            )
        )
        if ($preMutationStaging) {
            if ($stagingLeaseItem.Count -eq 1) {
                $leaseStream = [IO.File]::Open(
                    $stagingLeaseItem[0].FullName,
                    [IO.FileMode]::Open,
                    [IO.FileAccess]::ReadWrite,
                    [IO.FileShare]::Delete
                )
                $leaseLocked = $false
                try {
                    $leaseStream.Lock(0, 1)
                    $leaseLocked = $true
                    if ($stagingAtomicItem.Count -eq 1) {
                        [IO.File]::Delete($stagingAtomicItem[0].FullName)
                    }
                    [IO.File]::Delete($stagingLeaseItem[0].FullName)
                    $leaseStream.Unlock(0, 1)
                    $leaseLocked = $false
                } finally {
                    if ($leaseLocked) {
                        try { $leaseStream.Unlock(0, 1) } catch { }
                    }
                    $leaseStream.Dispose()
                }
            } elseif ($stagingAtomicItem.Count -eq 1) {
                [IO.File]::Delete($stagingAtomicItem[0].FullName)
            }
            $quarantine = Join-Path $stateBase (
                "recovered-$staleRunId-$([Guid]::NewGuid().ToString('N'))"
            )
            [IO.Directory]::Move($staleRoot, $quarantine)
            Remove-QuarantinedRun `
                $quarantine $staleRunId $stateBoundary $currentSid $systemSid
            continue
        }
        $staleFiles = Get-ValidatedRunFiles $staleRoot $staleRunId $currentSid $systemSid
        $quarantine = ''
        $ledgerPresent = Test-Path -LiteralPath $staleFiles.Ledger -PathType Leaf
        $runInvalidated = $false
        if (-not $ledgerPresent) {
            $leaseStream = [IO.File]::Open(
                $staleFiles.Lease,
                [IO.FileMode]::Open,
                [IO.FileAccess]::ReadWrite,
                [IO.FileShare]::Delete
            )
            $leaseLocked = $false
            try {
                $leaseStream.Lock(0, 1)
                $leaseLocked = $true
                $ledgerPresent = Test-Path -LiteralPath $staleFiles.Ledger -PathType Leaf
                if (-not $ledgerPresent) {
                    $lockedChildren = @(Get-ChildItem -Force -LiteralPath $staleRoot)
                    $allowedLockedNames = @(
                        'lease.lock',
                        ".ownership.json.$staleRunId.tmp"
                    )
                    if (@($lockedChildren | Where-Object {
                        $_.Name -cnotin $allowedLockedNames -or
                        $_.PSIsContainer -or
                        ($_.Attributes -band [IO.FileAttributes]::ReparsePoint)
                    }).Count -ne 0) { throw 'stale_run_contents_invalid' }
                    if (@($lockedChildren | Where-Object {
                        -not (Test-ExactPrivateFileAcl $_.FullName $currentSid $systemSid)
                    }).Count -ne 0) { throw 'stale_run_file_acl_invalid' }
                    if (Test-Path -LiteralPath $staleFiles.Atomic -PathType Leaf) {
                        [IO.File]::Delete($staleFiles.Atomic)
                    }
                    [IO.File]::Delete($staleFiles.Lease)
                    $runInvalidated = $true
                }
                $leaseStream.Unlock(0, 1)
                $leaseLocked = $false
            } finally {
                if ($leaseLocked) {
                    try { $leaseStream.Unlock(0, 1) } catch { }
                }
                $leaseStream.Dispose()
            }
        }
        if ($ledgerPresent) {
            $ledgerItem = Get-Item -Force -LiteralPath $staleFiles.Ledger
            if (-not (Test-ExactPrivateFileAcl `
                $staleFiles.Ledger $currentSid $systemSid
            )) { throw 'stale_ledger_acl_invalid' }
            if ($ledgerItem.Length -gt 262144) { throw 'stale_ledger_invalid' }
            $ledger = Get-Content -Raw -LiteralPath $staleFiles.Ledger | ConvertFrom-Json
            $ledgerProperties = @($ledger.PSObject.Properties.Name | Sort-Object)
            $expectedLedgerProperties = @(
                'applicationId', 'commands', 'guildId', 'recoveryRequired',
                'runId', 'schema'
            )
            if (
                @(Compare-Object $expectedLedgerProperties $ledgerProperties).Count -ne 0 -or
                $ledger.schema -isnot [string] -or
                [string]$ledger.schema -cne 'evelyn.discord-command-ownership.v2' -or
                $ledger.runId -isnot [string] -or [string]$ledger.runId -cne $staleRunId -or
                $ledger.guildId -isnot [string] -or
                [string]$ledger.guildId -cnotmatch '^[1-9]\d{16,19}$' -or
                $ledger.applicationId -isnot [string] -or
                [string]$ledger.applicationId -cnotmatch '^[1-9]\d{16,19}$' -or
                $ledger.recoveryRequired -isnot [bool] -or
                $ledger.commands -isnot [array]
            ) { throw 'stale_ledger_invalid' }
            if (
                $TargetGuildNameFromStdin -and
                [string]$ledger.guildId -cne $GuildId
            ) { throw 'stale_run_target_mismatch' }
            $recovery = Invoke-RegistryChild `
                -Mode 'recover' `
                -GuildId ([string]$ledger.guildId) `
                -RunId $staleRunId `
                -LedgerPath $staleFiles.Ledger `
                -LeasePath $staleFiles.Lease `
                -TokenBytes $tokenBytes
            Assert-RecoveryResult $recovery
        } elseif (-not $runInvalidated) {
            throw 'stale_run_invalidation_failed'
        }
        if ([string]::IsNullOrEmpty($quarantine)) {
            $quarantineName = "recovered-$staleRunId-$([Guid]::NewGuid().ToString('N'))"
            $quarantine = Join-Path $stateBase $quarantineName
            [IO.Directory]::Move($staleRoot, $quarantine)
        }
        if ([string]::IsNullOrEmpty($quarantine)) { throw 'recovery_failed' }
        Remove-QuarantinedRun `
            $quarantine $staleRunId $stateBoundary $currentSid $systemSid
    }

    $launcherPhase = 'run_prepare'
    $currentRunId = [Guid]::NewGuid().ToString('N')
    $currentRunRoot = [IO.Path]::GetFullPath((
        Join-Path $stateBase "evelyn-command-registry-$currentRunId"
    ))
    if (Test-Path -LiteralPath $currentRunRoot) { throw 'run_root_preexisting' }
    $null = [IO.Directory]::CreateDirectory($currentRunRoot)
    $currentRunItem = Get-Item -Force -LiteralPath $currentRunRoot
    if (
        -not $currentRunItem.PSIsContainer -or
        ($currentRunItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
        (Split-Path -Parent $currentRunItem.FullName) -cne $stateBase
    ) { throw 'run_root_unsafe' }
    Set-ExactPrivateDirectoryAcl $currentRunRoot $currentSid $systemSid
    $leasePath = Join-Path $currentRunRoot 'lease.lock'
    [IO.File]::WriteAllBytes($leasePath, [byte[]]@(49))
    $currentFiles = Get-ValidatedRunFiles `
        $currentRunRoot $currentRunId $currentSid $systemSid

    $launcherPhase = 'validation'
    try {
        $normalInvocation = Invoke-RegistryChild `
            -Mode 'validate' `
            -GuildId $GuildId `
            -RunId $currentRunId `
            -LedgerPath $currentFiles.Ledger `
            -LeasePath $currentFiles.Lease `
            -TokenBytes $tokenBytes `
            -GuildName $GuildName
        Assert-NormalResult $normalInvocation
    } catch {
        $normalInvocation = $null
    }

    $needsRecovery = (
        $null -eq $normalInvocation -or
        $normalInvocation.Result.recoveryRequired -eq $true
    )
    if ($needsRecovery) {
        $launcherPhase = 'cleanup'
        $quarantine = ''
        if (Test-Path -LiteralPath $currentFiles.Ledger -PathType Leaf) {
            $recovery = Invoke-RegistryChild `
                -Mode 'recover' `
                -GuildId $GuildId `
                -RunId $currentRunId `
                -LedgerPath $currentFiles.Ledger `
                -LeasePath $currentFiles.Lease `
                -TokenBytes $tokenBytes
            Assert-RecoveryResult $recovery
        } else {
            $leaseStream = [IO.File]::Open(
                $currentFiles.Lease,
                [IO.FileMode]::Open,
                [IO.FileAccess]::ReadWrite,
                [IO.FileShare]::Delete
            )
            $leaseLocked = $false
            try {
                $leaseStream.Lock(0, 1)
                $leaseLocked = $true
                if (Test-Path -LiteralPath $currentFiles.Atomic -PathType Leaf) {
                    [IO.File]::Delete($currentFiles.Atomic)
                }
                [IO.File]::Delete($currentFiles.Lease)
                $leaseStream.Unlock(0, 1)
                $leaseLocked = $false
            } finally {
                if ($leaseLocked) {
                    try { $leaseStream.Unlock(0, 1) } catch { }
                }
                $leaseStream.Dispose()
            }
        }
        if ([string]::IsNullOrEmpty($quarantine)) {
            $quarantine = Join-Path $stateBase (
                "recovered-$currentRunId-$([Guid]::NewGuid().ToString('N'))"
            )
            [IO.Directory]::Move($currentRunRoot, $quarantine)
        }
        Remove-QuarantinedRun `
            $quarantine $currentRunId $stateBoundary $currentSid $systemSid
        $currentRunRoot = ''
        $publicOutput = Public-Result `
            -State 'failed' `
            -Failure 'launcher_failed' `
            -RestoredVerified $true `
            -RecoveryRequired $false
        $exitCode = 1
    } else {
        $launcherPhase = 'cleanup'
        $result = $normalInvocation.Result
        $quarantine = Join-Path $stateBase (
            "recovered-$currentRunId-$([Guid]::NewGuid().ToString('N'))"
        )
        [IO.Directory]::Move($currentRunRoot, $quarantine)
        Remove-QuarantinedRun `
            $quarantine $currentRunId $stateBoundary $currentSid $systemSid
        $currentRunRoot = ''
        $publicOutput = Public-Result `
            -State ([string]$result.state) `
            -Failure ([string]$result.failure) `
            -PublishedVerified ([bool]$result.publishedVerified) `
            -RestoredVerified ([bool]$result.restoredVerified) `
            -RecoveryRequired ([bool]$result.recoveryRequired)
        $exitCode = $normalInvocation.ExitCode
    }
} catch {
    $publicOutput = Public-Result `
        -State 'failed' `
        -Failure "launcher_${launcherPhase}_failed"
    $exitCode = 1
} finally {
    $GuildName = ''
    if ($null -ne $tokenBytes) { [Array]::Clear($tokenBytes, 0, $tokenBytes.Length) }
    if ($stateLeaseLocked -and $null -ne $stateLeaseStream) {
        try { $stateLeaseStream.Unlock(0, 1) } catch { }
    }
    if ($null -ne $stateLeaseStream) { $stateLeaseStream.Dispose() }
    if ($mutexOwned -and $null -ne $mutex) {
        try { $mutex.ReleaseMutex() } catch { }
    }
    if ($null -ne $mutex) { $mutex.Dispose() }
}

[Console]::Out.WriteLine($publicOutput)
exit $exitCode
