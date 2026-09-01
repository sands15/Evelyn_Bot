[CmdletBinding()]
param(
    [ValidatePattern('^S-\d(?:-\d+)+$')]
    [string]$ExpectedAdminSid = '',

    [string]$ExpectedAdminAccount = '',

    [ValidatePattern('^[1-9]\d{4,23}$')]
    [string]$RegisteredDiscordUserId = '',

    [string]$PrimaryArchivePath = 'C:\ProgramData\Evelyn\private-audit',
    [string]$ReplicaArchivePath = 'D:\EvelynBackup\private-audit',
    [string]$AnchorPath = 'C:\ProgramData\Evelyn\private-audit-anchor',
    [string]$AttestationKeyPath = 'C:\ProgramData\Evelyn\private-audit-secrets\auth.key',
    [string]$IngestKeyPath = 'C:\ProgramData\Evelyn\private-audit-secrets\ingest.key',
    [string]$UserViewKeyPath = 'C:\ProgramData\Evelyn\private-audit-secrets\user-view.key',
    [string]$ProxyKeyPath = 'C:\ProgramData\Evelyn\private-audit-secrets\proxy.key',
    [string]$MinecraftKeyPath = 'C:\ProgramData\Evelyn\private-audit-secrets\minecraft.key',
    [string]$AttestationOutputPath = 'C:\ProgramData\Evelyn\private-audit-secrets\host-attestation.json',
    [string]$HostSessionStatePath = 'C:\ProgramData\Evelyn\private-audit-secrets\host-session.json',
    [string]$ControlPageTlsCertPath = 'C:\ProgramData\Evelyn\private-audit-secrets\control-page-cert.pem',
    [string]$ControlPageTlsKeyPath = 'C:\ProgramData\Evelyn\private-audit-secrets\control-page-key.pem',
    [ValidateRange(15, 90)]
    [int]$LifetimeSeconds = 60,
    [ValidatePattern('^https://(?:127\.0\.0\.1|\[::1\])(?::\d{1,5})?/archive/admin$')]
    [string]$ControlPageUrl = 'https://127.0.0.1:8800/archive/admin',
    [switch]$OpenControlPage,
    [switch]$ElevatedChild,
    [switch]$ValidationAttestationOnly,
    [string]$ValidationIdentityInputPath = '',
    [switch]$HostSessionWatcher,
    [string]$HostSessionNonce = '',
    [string]$HostSessionBootId = '',
    [long]$HostSessionExpiresAt = 0
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$validationNonce = ''
$validationRunId = ''

function Write-PublicStatus {
    param(
        [bool]$Ok,
        [string]$State,
        [bool]$Retryable = $false
    )
    [pscustomobject]@{
        schema = 'conversation_archive.admin-status.v1'
        ok = $Ok
        state = $State
        retryable = $Retryable
    } | ConvertTo-Json -Compress
}

function Test-ElevatedAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    return $principal.IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator
    )
}

function Quote-NativeArgument {
    param([string]$Value)
    return '"' + $Value.Replace('"', '\"') + '"'
}

function Set-ValidationIdentityFileAcl {
    param([string]$LiteralPath, [string]$AdminSid)
    $acl = [Security.AccessControl.FileSecurity]::new()
    $admin = [Security.Principal.SecurityIdentifier]::new($AdminSid)
    $system = [Security.Principal.SecurityIdentifier]::new('S-1-5-18')
    $acl.SetOwner($admin)
    $acl.SetAccessRuleProtection($true, $false)
    foreach ($sid in @($admin, $system)) {
        $null = $acl.AddAccessRule(
            [Security.AccessControl.FileSystemAccessRule]::new(
                $sid,
                [Security.AccessControl.FileSystemRights]::FullControl,
                [Security.AccessControl.AccessControlType]::Allow
            )
        )
    }
    [IO.FileSystemAclExtensions]::SetAccessControl(
        [IO.FileInfo]::new($LiteralPath),
        $acl
    )
}

function Read-ValidationIdentityJson {
    param([string]$Json)
    if (
        [string]::IsNullOrWhiteSpace($Json) -or
        [Text.Encoding]::UTF8.GetByteCount($Json) -gt 4096
    ) { throw 'archive_validation_identity_invalid' }
    try { $payload = ConvertFrom-Json $Json -ErrorAction Stop }
    catch { throw 'archive_validation_identity_invalid' }
    $names = @($payload.PSObject.Properties.Name | Sort-Object)
    $expectedNames = @(
        'adminAccount', 'adminSid', 'attestationNonce',
        'discordUserId', 'runId', 'schema'
    ) | Sort-Object
    if (
        ($names -join "`n") -cne ($expectedNames -join "`n") -or
        $payload.schema -cne 'conversation_archive.validation-identity.v1' -or
        [string]$payload.adminSid -notmatch '^S-\d(?:-\d+)+$' -or
        [string]$payload.discordUserId -notmatch '^[1-9]\d{4,23}$' -or
        [string]$payload.runId -notmatch '^[0-9a-f]{32}$' -or
        [string]$payload.attestationNonce -notmatch '^[A-Za-z0-9_-]{22,128}$' -or
        [string]::IsNullOrWhiteSpace([string]$payload.adminAccount) -or
        ([string]$payload.adminAccount).Length -gt 256 -or
        ([string]$payload.adminAccount).IndexOfAny([char[]]@("`0", "`r", "`n")) -ge 0
    ) { throw 'archive_validation_identity_invalid' }
    $script:ExpectedAdminSid = [string]$payload.adminSid
    $script:ExpectedAdminAccount = [string]$payload.adminAccount
    $script:RegisteredDiscordUserId = [string]$payload.discordUserId
    $script:validationRunId = [string]$payload.runId
    $script:validationNonce = [string]$payload.attestationNonce
}

function New-ProtectedValidationIdentityFile {
    $line = [Console]::In.ReadLine()
    Read-ValidationIdentityJson -Json $line
    $directory = [IO.Path]::GetFullPath((Split-Path -Parent $AttestationKeyPath))
    if (
        -not (Test-Path -LiteralPath $directory -PathType Container) -or
        (Test-ReparsePath $directory)
    ) { throw 'archive_validation_identity_transport_unavailable' }
    $directoryAcl = Get-Acl -LiteralPath $directory -ErrorAction Stop
    if (
        -not $directoryAcl.AreAccessRulesProtected -or
        -not (Test-NonAdminWriteDenied $directoryAcl $ExpectedAdminSid)
    ) { throw 'archive_validation_identity_transport_unavailable' }
    $path = Join-Path $directory (
        '.evelyn-validation-identity.' + [Guid]::NewGuid().ToString('N') + '.json'
    )
    try {
        [IO.File]::WriteAllText($path, $line, [Text.UTF8Encoding]::new($false))
        Set-ValidationIdentityFileAcl -LiteralPath $path -AdminSid $ExpectedAdminSid
        return $path
    }
    catch {
        if (
            (Test-Path -LiteralPath $path -PathType Leaf) -and
            -not (Test-ReparsePath $path)
        ) {
            [IO.File]::Delete($path)
        }
        throw
    }
}

function Import-ProtectedValidationIdentityFile {
    $directory = [IO.Path]::GetFullPath((Split-Path -Parent $AttestationKeyPath)).TrimEnd('\')
    $path = [IO.Path]::GetFullPath($ValidationIdentityInputPath)
    if (
        -not [string]::Equals(
            [IO.Path]::GetDirectoryName($path),
            $directory,
            [StringComparison]::OrdinalIgnoreCase
        ) -or
        [IO.Path]::GetFileName($path) -notmatch '^\.evelyn-validation-identity\.[0-9a-f]{32}\.json$' -or
        -not (Test-Path -LiteralPath $path -PathType Leaf) -or
        (Test-ReparsePath $path) -or
        (Get-Item -LiteralPath $path -Force).Length -gt 4096
    ) { throw 'archive_validation_identity_transport_invalid' }
    $json = [IO.File]::ReadAllText($path, [Text.Encoding]::UTF8)
    Read-ValidationIdentityJson -Json $json
    $acl = Get-Acl -LiteralPath $path -ErrorAction Stop
    $ownerSid = $acl.GetOwner([Security.Principal.SecurityIdentifier]).Value
    if (
        $ownerSid -cne $ExpectedAdminSid -or
        -not $acl.AreAccessRulesProtected -or
        -not (Test-NonAdminWriteDenied $acl $ExpectedAdminSid)
    ) { throw 'archive_validation_identity_transport_invalid' }
    [IO.File]::Delete($path)
    if (Test-Path -LiteralPath $path) {
        throw 'archive_validation_identity_transport_cleanup_failed'
    }
}

function Remove-ValidationIdentityTransport {
    if ([string]::IsNullOrWhiteSpace($ValidationIdentityInputPath)) { return }
    $directory = [IO.Path]::GetFullPath((Split-Path -Parent $AttestationKeyPath)).TrimEnd('\')
    $path = [IO.Path]::GetFullPath($ValidationIdentityInputPath)
    if (
        -not [string]::Equals(
            [IO.Path]::GetDirectoryName($path),
            $directory,
            [StringComparison]::OrdinalIgnoreCase
        ) -or
        [IO.Path]::GetFileName($path) -notmatch '^\.evelyn-validation-identity\.[0-9a-f]{32}\.json$'
    ) { throw 'archive_validation_identity_transport_invalid' }
    if (Test-Path -LiteralPath $path) {
        if (Test-ReparsePath $path) {
            throw 'archive_validation_identity_transport_cleanup_failed'
        }
        [IO.File]::Delete($path)
    }
}

function Start-ElevatedCopy {
    $arguments = [Collections.Generic.List[string]]::new()
    foreach ($argument in @(
        '-NoProfile',
        '-ExecutionPolicy', 'Bypass',
        '-File', (Quote-NativeArgument $PSCommandPath)
    )) { $arguments.Add($argument) }
    if ($ValidationAttestationOnly) {
        foreach ($argument in @(
            '-ValidationAttestationOnly',
            '-ValidationIdentityInputPath', (Quote-NativeArgument $ValidationIdentityInputPath)
        )) { $arguments.Add($argument) }
    }
    else {
        foreach ($argument in @(
            '-ExpectedAdminSid', (Quote-NativeArgument $ExpectedAdminSid),
            '-ExpectedAdminAccount', (Quote-NativeArgument $ExpectedAdminAccount),
            '-RegisteredDiscordUserId', (Quote-NativeArgument $RegisteredDiscordUserId)
        )) { $arguments.Add($argument) }
    }
    foreach ($argument in @(
        '-PrimaryArchivePath', (Quote-NativeArgument $PrimaryArchivePath),
        '-ReplicaArchivePath', (Quote-NativeArgument $ReplicaArchivePath),
        '-AnchorPath', (Quote-NativeArgument $AnchorPath),
        '-AttestationKeyPath', (Quote-NativeArgument $AttestationKeyPath),
        '-IngestKeyPath', (Quote-NativeArgument $IngestKeyPath),
        '-UserViewKeyPath', (Quote-NativeArgument $UserViewKeyPath),
        '-ProxyKeyPath', (Quote-NativeArgument $ProxyKeyPath),
        '-MinecraftKeyPath', (Quote-NativeArgument $MinecraftKeyPath),
        '-AttestationOutputPath', (Quote-NativeArgument $AttestationOutputPath),
        '-HostSessionStatePath', (Quote-NativeArgument $HostSessionStatePath),
        '-ControlPageTlsCertPath', (Quote-NativeArgument $ControlPageTlsCertPath),
        '-ControlPageTlsKeyPath', (Quote-NativeArgument $ControlPageTlsKeyPath),
        '-LifetimeSeconds', ([string]$LifetimeSeconds),
        '-ControlPageUrl', (Quote-NativeArgument $ControlPageUrl),
        '-ElevatedChild'
    )) { $arguments.Add($argument) }
    if ($OpenControlPage) {
        $arguments += '-OpenControlPage'
    }
    try {
        $process = Start-Process `
            -FilePath 'powershell.exe' `
            -ArgumentList @($arguments) `
            -Verb RunAs `
            -WindowStyle Hidden `
            -Wait `
            -PassThru
        exit $process.ExitCode
    }
    finally {
        if ($ValidationAttestationOnly) {
            Remove-ValidationIdentityTransport
        }
    }
}

function Get-HostSessionMessage {
    param([System.Collections.IDictionary]$Marker)
    $lines = @(
        $Marker.schema,
        $Marker.purpose,
        $Marker.adminSid,
        $Marker.hostId,
        $Marker.bootId,
        $Marker.bootstrapNonce,
        $Marker.state,
        [string]$Marker.updatedAt,
        [string]$Marker.expiresAt,
        $Marker.authAlgorithm
    )
    return 'evelyn.conversation-archive.admin-host-session.v1' + "`n" +
        ($lines -join "`n") + "`n"
}

function Set-HostSessionMarker {
    param(
        [ValidateSet('active', 'revoked')]
        [string]$State,
        [byte[]]$SigningKey,
        [string]$Nonce,
        [string]$BootId,
        [long]$ExpiresAt
    )
    $updatedAt = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
    $marker = [ordered]@{
        schema = 'conversation_archive.admin-host-session.v1'
        purpose = 'conversation_archive.admin.host-session'
        adminSid = $ExpectedAdminSid
        hostId = [string]$env:COMPUTERNAME
        bootId = $BootId
        bootstrapNonce = $Nonce
        state = $State
        updatedAt = $updatedAt
        expiresAt = $ExpiresAt
        authAlgorithm = 'hmac-sha256'
    }
    $hmac = [Security.Cryptography.HMACSHA256]::new($SigningKey)
    try {
        $message = [Text.Encoding]::UTF8.GetBytes((Get-HostSessionMessage $marker))
        $marker['authTag'] = ([BitConverter]::ToString(
            $hmac.ComputeHash($message)
        ) -replace '-', '').ToLowerInvariant()
    }
    finally {
        $hmac.Dispose()
    }
    $jsonBytes = [Text.Encoding]::UTF8.GetBytes(
        ($marker | ConvertTo-Json -Compress)
    )
    $stream = [IO.File]::Open(
        $HostSessionStatePath,
        [IO.FileMode]::OpenOrCreate,
        [IO.FileAccess]::Write,
        [IO.FileShare]::Read
    )
    try {
        $stream.SetLength(0)
        $stream.Write($jsonBytes, 0, $jsonBytes.Length)
        $stream.Flush($true)
    }
    finally {
        $stream.Dispose()
        [Array]::Clear($jsonBytes, 0, $jsonBytes.Length)
    }
}

function Start-HostSessionWatcher {
    param(
        [string]$Nonce,
        [string]$BootId,
        [long]$ExpiresAt
    )
    $arguments = @(
        '-NoProfile',
        '-ExecutionPolicy', 'Bypass',
        '-File', (Quote-NativeArgument $PSCommandPath),
        '-ExpectedAdminSid', (Quote-NativeArgument $ExpectedAdminSid),
        '-ExpectedAdminAccount', (Quote-NativeArgument $ExpectedAdminAccount),
        '-RegisteredDiscordUserId', (Quote-NativeArgument $RegisteredDiscordUserId),
        '-AttestationKeyPath', (Quote-NativeArgument $AttestationKeyPath),
        '-HostSessionStatePath', (Quote-NativeArgument $HostSessionStatePath),
        '-HostSessionNonce', (Quote-NativeArgument $Nonce),
        '-HostSessionBootId', (Quote-NativeArgument $BootId),
        '-HostSessionExpiresAt', ([string]$ExpiresAt),
        '-HostSessionWatcher'
    )
    $watcher = Start-Process `
        -FilePath 'powershell.exe' `
        -ArgumentList $arguments `
        -WindowStyle Hidden `
        -PassThru
    if ($null -eq $watcher -or $watcher.HasExited) {
        throw 'archive_host_session_watcher_unavailable'
    }
}

function Watch-HostSession {
    $key = [IO.File]::ReadAllBytes($AttestationKeyPath)
    if ($key.Length -lt 32) {
        throw 'archive_host_key_unavailable'
    }
    $eventSource = 'EvelynConversationArchiveAdminSessionSwitch'
    $registered = $false
    try {
        Register-ObjectEvent `
            -InputObject ([Microsoft.Win32.SystemEvents]) `
            -EventName SessionSwitch `
            -SourceIdentifier $eventSource | Out-Null
        $registered = $true
        while ([DateTimeOffset]::UtcNow.ToUnixTimeSeconds() -lt $HostSessionExpiresAt) {
            Set-HostSessionMarker `
                -State active `
                -SigningKey $key `
                -Nonce $HostSessionNonce `
                -BootId $HostSessionBootId `
                -ExpiresAt $HostSessionExpiresAt
            $event = Wait-Event -SourceIdentifier $eventSource -Timeout 5
            if ($null -eq $event) {
                continue
            }
            $reason = [string]$event.SourceEventArgs.Reason
            Remove-Event -EventIdentifier $event.EventIdentifier -ErrorAction SilentlyContinue
            if ($reason -in @('SessionLock', 'SessionLogoff', 'RemoteDisconnect')) {
                break
            }
        }
    }
    finally {
        try {
            Set-HostSessionMarker `
                -State revoked `
                -SigningKey $key `
                -Nonce $HostSessionNonce `
                -BootId $HostSessionBootId `
                -ExpiresAt $HostSessionExpiresAt
        }
        finally {
            if ($registered) {
                Unregister-Event -SourceIdentifier $eventSource -ErrorAction SilentlyContinue
            }
            [Array]::Clear($key, 0, $key.Length)
        }
    }
}

function Test-ReparsePath {
    param([string]$LiteralPath)
    $item = Get-Item -LiteralPath $LiteralPath -Force
    while ($null -ne $item) {
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            return $true
        }
        $item = if ($item -is [IO.DirectoryInfo]) {
            $item.Parent
        }
        else {
            $item.Directory
        }
    }
    return $false
}

function Test-NonAdminWriteDenied {
    param(
        [object]$Acl,
        [string]$ExpectedAdminSid
    )
    $allowedWriterSids = @(
        $ExpectedAdminSid,
        'S-1-5-18',
        'S-1-5-32-544'
    )
    $writeMask = [int64](`
        [Security.AccessControl.FileSystemRights]::WriteData -bor
        [Security.AccessControl.FileSystemRights]::AppendData -bor
        [Security.AccessControl.FileSystemRights]::CreateFiles -bor
        [Security.AccessControl.FileSystemRights]::CreateDirectories -bor
        [Security.AccessControl.FileSystemRights]::Delete -bor
        [Security.AccessControl.FileSystemRights]::ChangePermissions -bor
        [Security.AccessControl.FileSystemRights]::TakeOwnership
    )
    foreach ($rule in $Acl.Access) {
        if ($rule.AccessControlType -ne [Security.AccessControl.AccessControlType]::Allow) {
            continue
        }
        try {
            $sid = $rule.IdentityReference.Translate(
                [Security.Principal.SecurityIdentifier]
            ).Value
        }
        catch {
            return $false
        }
        if (
            (([int64]$rule.FileSystemRights -band $writeMask) -ne 0) -and
            $allowedWriterSids -cnotcontains $sid
        ) {
            return $false
        }
    }
    return $true
}

function Get-ArchiveVolumeEvidence {
    param(
        [ValidateSet('primary', 'replica', 'anchor')]
        [string]$Role,
        [ValidateSet('C', 'D')]
        [string]$DriveLetter,
        [string]$ArchivePath,
        [string]$ExpectedAdminSid
    )
    if (-not (Test-Path -LiteralPath $ArchivePath -PathType Container)) {
        throw 'archive_storage_preflight_failed'
    }
    $expectedRoot = "$DriveLetter`:"
    $fullPath = [IO.Path]::GetFullPath($ArchivePath)
    if (-not $fullPath.StartsWith("$expectedRoot\", [StringComparison]::OrdinalIgnoreCase)) {
        throw 'archive_storage_preflight_failed'
    }
    $volume = Get-Volume -DriveLetter $DriveLetter -ErrorAction Stop
    $partition = Get-Partition -DriveLetter $DriveLetter -ErrorAction Stop
    $disk = Get-Disk -Number $partition.DiskNumber -ErrorAction Stop
    $bitLocker = Get-BitLockerVolume -MountPoint $expectedRoot -ErrorAction Stop
    $acl = Get-Acl -LiteralPath $fullPath -ErrorAction Stop
    try {
        $ownerSid = ([Security.Principal.NTAccount]$acl.Owner).Translate(
            [Security.Principal.SecurityIdentifier]
        ).Value
    }
    catch {
        throw 'archive_storage_preflight_failed'
    }
    if ([string]::IsNullOrWhiteSpace([string]$volume.UniqueId)) {
        throw 'archive_storage_preflight_failed'
    }
    if ([string]::IsNullOrWhiteSpace([string]$disk.UniqueId)) {
        throw 'archive_storage_preflight_failed'
    }
    $mountNonce = New-Base64UrlNonce
    $bindingPath = Join-Path $fullPath '.evelyn-volume-binding'
    if (
        (Test-Path -LiteralPath $bindingPath) -and
        (Test-ReparsePath $bindingPath)
    ) {
        throw 'archive_storage_preflight_failed'
    }
    $bindingTemporary = Join-Path $fullPath (
        '.evelyn-volume-binding.' + $PID + '.' +
        [Guid]::NewGuid().ToString('N') + '.tmp'
    )
    [IO.File]::WriteAllText(
        $bindingTemporary,
        $mountNonce,
        [Text.UTF8Encoding]::new($false)
    )
    Move-Item -LiteralPath $bindingTemporary -Destination $bindingPath -Force
    if (Test-ReparsePath $bindingPath) {
        throw 'archive_storage_preflight_failed'
    }
    $bindingAcl = Get-Acl -LiteralPath $bindingPath -ErrorAction Stop
    try {
        $bindingOwnerSid = ([Security.Principal.NTAccount]$bindingAcl.Owner).Translate(
            [Security.Principal.SecurityIdentifier]
        ).Value
    }
    catch {
        throw 'archive_storage_preflight_failed'
    }
    if (
        @($ExpectedAdminSid, 'S-1-5-18', 'S-1-5-32-544') -cnotcontains $bindingOwnerSid -or
        -not (Test-NonAdminWriteDenied $bindingAcl $ExpectedAdminSid)
    ) {
        throw 'archive_storage_preflight_failed'
    }
    return [ordered]@{
        role = $Role
        driveLetter = $expectedRoot
        volumeId = [string]$volume.UniqueId
        diskId = [string]$disk.UniqueId
        driveType = [string]$volume.DriveType
        fileSystem = [string]$volume.FileSystem
        healthStatus = [string]$volume.HealthStatus
        bitLockerProtectionStatus = [string]$bitLocker.ProtectionStatus
        bitLockerVolumeStatus = [string]$bitLocker.VolumeStatus
        lockStatus = [string]$bitLocker.LockStatus
        ownerSid = [string]$ownerSid
        mountNonce = [string]$mountNonce
        archivePath = $fullPath.TrimEnd('\')
        pathExists = $true
        pathHasReparsePoint = [bool](Test-ReparsePath $fullPath)
        daclProtected = [bool]$acl.AreAccessRulesProtected
        nonAdminWriteDenied = [bool](Test-NonAdminWriteDenied $acl $ExpectedAdminSid)
    }
}

function New-Base64UrlNonce {
    $bytes = [byte[]]::new(32)
    $generator = [Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $generator.GetBytes($bytes)
        return [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
    }
    finally {
        $generator.Dispose()
    }
}

function Get-AttestationMessage {
    param([System.Collections.IDictionary]$Attestation)
    $lines = [Collections.Generic.List[string]]::new()
    foreach ($value in @(
        $Attestation.schema,
        $Attestation.purpose,
        $Attestation.adminSid,
        $Attestation.adminAccount,
        $Attestation.registeredDiscordUserId,
        $Attestation.hostId,
        $Attestation.bootId,
        $Attestation.bootstrapNonce,
        [string]$Attestation.issuedAt,
        [string]$Attestation.expiresAt,
        $(if ($Attestation.elevated) { '1' } else { '0' }),
        $(if ($Attestation.administratorMember) { '1' } else { '0' }),
        $Attestation.authAlgorithm
    )) {
        $lines.Add([string]$value)
    }
    foreach ($volume in @(
        $Attestation.primary,
        $Attestation.replica,
        $Attestation.anchor
    )) {
        foreach ($value in @(
            $volume.role,
            $volume.driveLetter,
            $volume.volumeId,
            $volume.diskId,
            $volume.driveType,
            $volume.fileSystem,
            $volume.healthStatus,
            $volume.bitLockerProtectionStatus,
            $volume.bitLockerVolumeStatus,
            $volume.lockStatus,
            $volume.ownerSid,
            $volume.mountNonce,
            $volume.archivePath,
            $(if ($volume.pathExists) { '1' } else { '0' }),
            $(if ($volume.pathHasReparsePoint) { '1' } else { '0' }),
            $(if ($volume.daclProtected) { '1' } else { '0' }),
            $(if ($volume.nonAdminWriteDenied) { '1' } else { '0' })
        )) {
            $lines.Add([string]$value)
        }
    }
    return 'evelyn.conversation-archive.admin-host-attestation.v1' + "`n" +
        ($lines -join "`n") + "`n"
}

try {
    try {
        $controlPageUri = [Uri]$ControlPageUrl
        $controlPageHost = $controlPageUri.DnsSafeHost
        $controlPagePort = $controlPageUri.Port
    }
    catch {
        throw 'archive_control_page_url_invalid'
    }
    if (
        -not $controlPageUri.IsAbsoluteUri -or
        $controlPageUri.Scheme -cne 'https' -or
        @('127.0.0.1', '::1') -cnotcontains $controlPageHost -or
        $controlPagePort -lt 1 -or
        $controlPagePort -gt 65535 -or
        $controlPageUri.AbsolutePath -cne '/archive/admin' -or
        -not [string]::IsNullOrEmpty($controlPageUri.Query) -or
        -not [string]::IsNullOrEmpty($controlPageUri.Fragment) -or
        -not [string]::IsNullOrEmpty($controlPageUri.UserInfo)
    ) {
        throw 'archive_control_page_url_invalid'
    }

    if ($ValidationAttestationOnly) {
        if (
            $OpenControlPage -or
            $HostSessionWatcher -or
            -not [string]::IsNullOrWhiteSpace($ExpectedAdminSid) -or
            -not [string]::IsNullOrWhiteSpace($ExpectedAdminAccount) -or
            -not [string]::IsNullOrWhiteSpace($RegisteredDiscordUserId) -or
            ($ElevatedChild -and [string]::IsNullOrWhiteSpace($ValidationIdentityInputPath)) -or
            (-not $ElevatedChild -and -not [string]::IsNullOrWhiteSpace($ValidationIdentityInputPath))
        ) {
            throw 'archive_validation_attestation_contract_invalid'
        }
        if ($ElevatedChild) {
            if (-not (Test-ElevatedAdministrator)) {
                Write-PublicStatus -Ok $false -State 'host_verification_failed'
                exit 1
            }
            Import-ProtectedValidationIdentityFile
        }
        else {
            $script:ValidationIdentityInputPath = New-ProtectedValidationIdentityFile
        }
    }
    elseif (
        -not [string]::IsNullOrWhiteSpace($ValidationIdentityInputPath) -or
        [string]::IsNullOrWhiteSpace($ExpectedAdminSid) -or
        [string]::IsNullOrWhiteSpace($ExpectedAdminAccount) -or
        [string]::IsNullOrWhiteSpace($RegisteredDiscordUserId)
    ) {
        throw 'archive_admin_identity_required'
    }

    if (-not (Test-ElevatedAdministrator)) {
        if ($ElevatedChild) {
            Write-PublicStatus -Ok $false -State 'host_verification_failed'
            exit 1
        }
        Start-ElevatedCopy
    }
    if ($ValidationAttestationOnly -and -not $ElevatedChild) {
        Import-ProtectedValidationIdentityFile
    }

    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    if (
        $identity.User.Value -cne $ExpectedAdminSid -or
        -not $identity.Name.Equals(
            $ExpectedAdminAccount,
            [StringComparison]::OrdinalIgnoreCase
        )
    ) {
        Write-PublicStatus -Ok $false -State 'host_verification_failed'
        exit 1
    }

    if ($HostSessionWatcher) {
        $watcherNow = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
        $sessionDirectory = Split-Path -Parent $HostSessionStatePath
        if (
            $HostSessionNonce -notmatch '^[A-Za-z0-9_-]{22,128}$' -or
            [string]::IsNullOrWhiteSpace($HostSessionBootId) -or
            $HostSessionBootId.IndexOfAny([char[]]@("`0", "`r", "`n")) -ge 0 -or
            $HostSessionExpiresAt -le $watcherNow -or
            $HostSessionExpiresAt -gt ($watcherNow + 330) -or
            -not (Test-Path -LiteralPath $AttestationKeyPath -PathType Leaf) -or
            (Test-ReparsePath $AttestationKeyPath) -or
            -not (Test-Path -LiteralPath $sessionDirectory -PathType Container) -or
            -not [IO.Path]::GetFullPath($sessionDirectory).TrimEnd('\').Equals(
                [IO.Path]::GetFullPath((Split-Path -Parent $AttestationKeyPath)).TrimEnd('\'),
                [StringComparison]::OrdinalIgnoreCase
            ) -or
            (Test-ReparsePath $sessionDirectory) -or
            (
                (Test-Path -LiteralPath $HostSessionStatePath) -and
                (Test-ReparsePath $HostSessionStatePath)
            )
        ) {
            exit 1
        }
        $sessionDirectoryAcl = Get-Acl -LiteralPath $sessionDirectory -ErrorAction Stop
        if (
            -not $sessionDirectoryAcl.AreAccessRulesProtected -or
            -not (Test-NonAdminWriteDenied $sessionDirectoryAcl $ExpectedAdminSid)
        ) {
            exit 1
        }
        Watch-HostSession
        exit 0
    }

    if ($ValidationAttestationOnly -and (
        (Test-Path -LiteralPath $AttestationOutputPath) -or
        (Test-Path -LiteralPath $HostSessionStatePath)
    )) {
        throw 'archive_validation_admin_state_conflict'
    }

    $primary = Get-ArchiveVolumeEvidence `
        -Role primary `
        -DriveLetter C `
        -ArchivePath $PrimaryArchivePath `
        -ExpectedAdminSid $ExpectedAdminSid
    $replica = Get-ArchiveVolumeEvidence `
        -Role replica `
        -DriveLetter D `
        -ArchivePath $ReplicaArchivePath `
        -ExpectedAdminSid $ExpectedAdminSid
    $anchor = Get-ArchiveVolumeEvidence `
        -Role anchor `
        -DriveLetter C `
        -ArchivePath $AnchorPath `
        -ExpectedAdminSid $ExpectedAdminSid
    if (
        $primary.volumeId.Equals($replica.volumeId, [StringComparison]::OrdinalIgnoreCase) -or
        $primary.diskId.Equals($replica.diskId, [StringComparison]::OrdinalIgnoreCase)
    ) {
        throw 'archive_storage_preflight_failed'
    }
    if (
        -not $anchor.volumeId.Equals($primary.volumeId, [StringComparison]::OrdinalIgnoreCase) -or
        -not $anchor.diskId.Equals($primary.diskId, [StringComparison]::OrdinalIgnoreCase)
    ) {
        throw 'archive_storage_preflight_failed'
    }
    foreach ($volume in @($primary, $replica, $anchor)) {
        if (
            $volume.driveType -cne 'Fixed' -or
            $volume.fileSystem -cne 'NTFS' -or
            $volume.healthStatus -cne 'Healthy' -or
            $volume.bitLockerProtectionStatus -cne 'On' -or
            $volume.bitLockerVolumeStatus -cne 'FullyEncrypted' -or
            $volume.lockStatus -cne 'Unlocked' -or
            @(
                $ExpectedAdminSid,
                'S-1-5-18',
                'S-1-5-32-544'
            ) -cnotcontains $volume.ownerSid -or
            -not $volume.pathExists -or
            $volume.pathHasReparsePoint -or
            -not $volume.daclProtected -or
            -not $volume.nonAdminWriteDenied
        ) {
            throw 'archive_storage_preflight_failed'
        }
    }

    foreach ($secretPath in @(
        $AttestationKeyPath,
        $IngestKeyPath,
        $UserViewKeyPath,
        $ProxyKeyPath,
        $MinecraftKeyPath
    )) {
        if (
            -not (Test-Path -LiteralPath $secretPath -PathType Leaf) -or
            (Test-ReparsePath $secretPath)
        ) {
            throw 'archive_host_key_unavailable'
        }
        $secretAcl = Get-Acl -LiteralPath $secretPath -ErrorAction Stop
        if (
            -not $secretAcl.AreAccessRulesProtected -or
            -not (Test-NonAdminWriteDenied $secretAcl $ExpectedAdminSid) -or
            ([IO.File]::ReadAllBytes($secretPath)).Length -lt 32
        ) {
            throw 'archive_host_key_unavailable'
        }
    }
    $key = [IO.File]::ReadAllBytes($AttestationKeyPath)
    if ($key.Length -lt 32) {
        throw 'archive_host_key_unavailable'
    }
    foreach ($tlsPath in @($ControlPageTlsCertPath, $ControlPageTlsKeyPath)) {
        if (
            -not (Test-Path -LiteralPath $tlsPath -PathType Leaf) -or
            (Test-ReparsePath $tlsPath)
        ) {
            throw 'archive_control_page_tls_unavailable'
        }
    }
    $tlsKeyAcl = Get-Acl -LiteralPath $ControlPageTlsKeyPath -ErrorAction Stop
    if (
        -not $tlsKeyAcl.AreAccessRulesProtected -or
        -not (Test-NonAdminWriteDenied $tlsKeyAcl $ExpectedAdminSid)
    ) {
        throw 'archive_control_page_tls_unavailable'
    }
    $boot = Get-CimInstance -ClassName Win32_OperatingSystem -ErrorAction Stop
    $issuedAt = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
    $attestation = [ordered]@{
        schema = 'conversation_archive.admin-host-attestation.v1'
        purpose = 'conversation_archive.admin.control'
        adminSid = $identity.User.Value
        adminAccount = $identity.Name
        registeredDiscordUserId = $RegisteredDiscordUserId
        hostId = [string]$env:COMPUTERNAME
        bootId = ([DateTimeOffset]$boot.LastBootUpTime).ToUniversalTime().ToString(
            'O', [Globalization.CultureInfo]::InvariantCulture
        )
        bootstrapNonce = if ($ValidationAttestationOnly) {
            $validationNonce
        } else {
            New-Base64UrlNonce
        }
        issuedAt = $issuedAt
        expiresAt = $issuedAt + $LifetimeSeconds
        elevated = $true
        administratorMember = $true
        primary = $primary
        replica = $replica
        anchor = $anchor
        authAlgorithm = 'hmac-sha256'
    }
    $hmac = [Security.Cryptography.HMACSHA256]::new($key)
    try {
        $message = [Text.Encoding]::UTF8.GetBytes((Get-AttestationMessage $attestation))
        $attestation['authTag'] = ([BitConverter]::ToString(
            $hmac.ComputeHash($message)
        ) -replace '-', '').ToLowerInvariant()
    }
    finally {
        $hmac.Dispose()
        [Array]::Clear($key, 0, $key.Length)
    }

    $outputDirectory = Split-Path -Parent $AttestationOutputPath
    if (-not (Test-Path -LiteralPath $outputDirectory -PathType Container)) {
        throw 'archive_attestation_output_unavailable'
    }
    if (
        -not [IO.Path]::GetFullPath($outputDirectory).TrimEnd('\').Equals(
            [IO.Path]::GetFullPath((Split-Path -Parent $AttestationKeyPath)).TrimEnd('\'),
            [StringComparison]::OrdinalIgnoreCase
        ) -or
        (Test-ReparsePath $outputDirectory) -or
        (
            (Test-Path -LiteralPath $AttestationOutputPath) -and
            (Test-ReparsePath $AttestationOutputPath)
        )
    ) {
        throw 'archive_attestation_output_unavailable'
    }
    $outputAcl = Get-Acl -LiteralPath $outputDirectory -ErrorAction Stop
    if (
        -not $outputAcl.AreAccessRulesProtected -or
        -not (Test-NonAdminWriteDenied $outputAcl $ExpectedAdminSid)
    ) {
        throw 'archive_attestation_output_unavailable'
    }
    $temporary = Join-Path $outputDirectory (
        '.' + [IO.Path]::GetFileName($AttestationOutputPath) + '.' +
        $PID + '.' + [Guid]::NewGuid().ToString('N') + '.tmp'
    )
    $json = $attestation | ConvertTo-Json -Depth 5 -Compress
    try {
        [IO.File]::WriteAllText(
            $temporary,
            $json,
            [Text.UTF8Encoding]::new($false)
        )
        if ($ValidationAttestationOnly) {
            if (Test-Path -LiteralPath $AttestationOutputPath) {
                throw 'archive_validation_admin_state_conflict'
            }
            Move-Item -LiteralPath $temporary -Destination $AttestationOutputPath
        }
        else {
            Move-Item -LiteralPath $temporary -Destination $AttestationOutputPath -Force
        }
    }
    finally {
        if (Test-Path -LiteralPath $temporary -PathType Leaf) {
            [IO.File]::Delete($temporary)
        }
    }
    if (Test-ReparsePath $AttestationOutputPath) {
        throw 'archive_attestation_output_unavailable'
    }
    $attestationAcl = Get-Acl -LiteralPath $AttestationOutputPath -ErrorAction Stop
    try {
        $attestationOwnerSid = ([Security.Principal.NTAccount]$attestationAcl.Owner).Translate(
            [Security.Principal.SecurityIdentifier]
        ).Value
    }
    catch {
        throw 'archive_attestation_output_unavailable'
    }
    if (
        @($ExpectedAdminSid, 'S-1-5-18', 'S-1-5-32-544') -cnotcontains $attestationOwnerSid -or
        -not (Test-NonAdminWriteDenied $attestationAcl $ExpectedAdminSid)
    ) {
        throw 'archive_attestation_output_unavailable'
    }

    if ($ValidationAttestationOnly) {
        if (
            [string]::IsNullOrWhiteSpace($validationRunId) -or
            (Test-Path -LiteralPath $HostSessionStatePath)
        ) {
            throw 'archive_validation_attestation_contract_invalid'
        }
        Write-PublicStatus -Ok $true -State 'host_validation_attestation_ready'
        exit 0
    }

    $hostSessionDirectory = Split-Path -Parent $HostSessionStatePath
    if (
        -not [IO.Path]::GetFullPath($hostSessionDirectory).TrimEnd('\').Equals(
            [IO.Path]::GetFullPath((Split-Path -Parent $AttestationKeyPath)).TrimEnd('\'),
            [StringComparison]::OrdinalIgnoreCase
        ) -or
        (Test-ReparsePath $hostSessionDirectory) -or
        (
            (Test-Path -LiteralPath $HostSessionStatePath) -and
            (Test-ReparsePath $HostSessionStatePath)
        )
    ) {
        throw 'archive_host_session_state_unavailable'
    }
    $hostSessionDirectoryAcl = Get-Acl -LiteralPath $hostSessionDirectory -ErrorAction Stop
    if (
        -not $hostSessionDirectoryAcl.AreAccessRulesProtected -or
        -not (Test-NonAdminWriteDenied $hostSessionDirectoryAcl $ExpectedAdminSid)
    ) {
        throw 'archive_host_session_state_unavailable'
    }
    $hostSessionExpiresAt = $issuedAt + 300
    $hostSessionKey = [IO.File]::ReadAllBytes($AttestationKeyPath)
    try {
        Set-HostSessionMarker `
            -State active `
            -SigningKey $hostSessionKey `
            -Nonce ([string]$attestation.bootstrapNonce) `
            -BootId ([string]$attestation.bootId) `
            -ExpiresAt $hostSessionExpiresAt
    }
    finally {
        [Array]::Clear($hostSessionKey, 0, $hostSessionKey.Length)
    }
    if (Test-ReparsePath $HostSessionStatePath) {
        throw 'archive_host_session_state_unavailable'
    }
    $hostSessionAcl = Get-Acl -LiteralPath $HostSessionStatePath -ErrorAction Stop
    try {
        $hostSessionOwnerSid = ([Security.Principal.NTAccount]$hostSessionAcl.Owner).Translate(
            [Security.Principal.SecurityIdentifier]
        ).Value
    }
    catch {
        throw 'archive_host_session_state_unavailable'
    }
    if (
        @($ExpectedAdminSid, 'S-1-5-18', 'S-1-5-32-544') -cnotcontains $hostSessionOwnerSid -or
        -not (Test-NonAdminWriteDenied $hostSessionAcl $ExpectedAdminSid)
    ) {
        throw 'archive_host_session_state_unavailable'
    }
    Start-HostSessionWatcher `
        -Nonce ([string]$attestation.bootstrapNonce) `
        -BootId ([string]$attestation.bootId) `
        -ExpiresAt $hostSessionExpiresAt

    if ($OpenControlPage) {
        $bootstrapUrl = (
            $ControlPageUrl + '#archive-bootstrap=' +
            [string]$attestation.bootstrapNonce
        )
        Start-Process -FilePath $bootstrapUrl -WindowStyle Normal | Out-Null
    }
    Write-PublicStatus -Ok $true -State 'host_attestation_ready'
    exit 0
}
catch {
    Write-PublicStatus -Ok $false -State 'storage_preflight_failed'
    exit 1
}
