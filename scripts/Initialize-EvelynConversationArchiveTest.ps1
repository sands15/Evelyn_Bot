#Requires -Version 7.0

[CmdletBinding()]
param(
    [ValidatePattern('^S-\d(?:-\d+)+$')]
    [string]$ExpectedAdminSid = '',

    [string]$ExpectedAdminAccount = '',

    [switch]$ElevatedChild,

    [switch]$LibraryOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$script:ArchiveOwnerMarkerName = '.evelyn-test-archive-owner.json'
$script:ArchiveOwnerMarkerSchema = 'conversation_archive.test-provision-owner.v1'
$script:ArchiveOwnerMarkerPurpose = 'conversation_archive.test-provision'
$script:ArchiveKeyNames = @(
    'auth.key',
    'ingest.key',
    'user-view.key',
    'proxy.key',
    'minecraft.key'
)
$script:ArchiveTlsCertName = 'control-page-cert.pem'
$script:ArchiveTlsKeyName = 'control-page-key.pem'

function Write-EvelynArchiveProvisionStatus {
    param(
        [bool]$Ok,
        [string]$State,
        [int]$Created = 0,
        [int]$Reused = 0
    )
    [pscustomobject]@{
        schema = 'conversation_archive.test-provision-status.v1'
        ok = $Ok
        state = $State
        created = $Created
        reused = $Reused
    } | ConvertTo-Json -Compress
}

function Test-EvelynArchiveElevatedAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    return $principal.IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator
    )
}

function Quote-EvelynArchiveNativeArgument {
    param([string]$Value)
    return '"' + $Value.Replace('"', '\"') + '"'
}

function Start-EvelynArchiveElevatedProvisioner {
    $arguments = @(
        '-NoProfile',
        '-ExecutionPolicy', 'Bypass',
        '-File', (Quote-EvelynArchiveNativeArgument $PSCommandPath),
        '-ExpectedAdminSid', (Quote-EvelynArchiveNativeArgument $ExpectedAdminSid),
        '-ExpectedAdminAccount', (Quote-EvelynArchiveNativeArgument $ExpectedAdminAccount),
        '-ElevatedChild'
    )
    $process = Start-Process `
        -FilePath (Join-Path $PSHOME 'pwsh.exe') `
        -ArgumentList $arguments `
        -Verb RunAs `
        -WindowStyle Hidden `
        -Wait `
        -PassThru
    exit $process.ExitCode
}

function Test-EvelynArchiveReparsePath {
    param([Parameter(Mandatory = $true)][string]$LiteralPath)

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

function Assert-EvelynArchivePathAncestors {
    param([Parameter(Mandatory = $true)][string]$LiteralPath)

    $fullPath = [IO.Path]::GetFullPath($LiteralPath)
    $root = [IO.Path]::GetPathRoot($fullPath)
    if ([string]::IsNullOrWhiteSpace($root)) {
        throw 'archive_provision_path_invalid'
    }
    $relative = $fullPath.Substring($root.Length)
    $current = $root.TrimEnd('\')
    foreach ($part in $relative.Split(
        @(
            [IO.Path]::DirectorySeparatorChar,
            [IO.Path]::AltDirectorySeparatorChar
        ),
        [StringSplitOptions]::RemoveEmptyEntries
    )) {
        $current = Join-Path $current $part
        if (-not (Test-Path -LiteralPath $current)) {
            continue
        }
        $item = Get-Item -LiteralPath $current -Force
        if (
            -not $item.PSIsContainer -or
            ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0
        ) {
            throw 'archive_provision_path_invalid'
        }
    }
}

function Get-EvelynArchiveAllowedSids {
    param([Parameter(Mandatory = $true)][string]$AdminSid)

    return @(
        [Security.Principal.SecurityIdentifier]::new($AdminSid),
        [Security.Principal.SecurityIdentifier]::new('S-1-5-18')
    )
}

function Set-EvelynArchivePrivateDirectoryAcl {
    param(
        [Parameter(Mandatory = $true)][string]$LiteralPath,
        [Parameter(Mandatory = $true)][string]$AdminSid
    )

    $sids = Get-EvelynArchiveAllowedSids -AdminSid $AdminSid
    $inheritance = (
        [Security.AccessControl.InheritanceFlags]::ContainerInherit -bor
        [Security.AccessControl.InheritanceFlags]::ObjectInherit
    )
    $acl = [Security.AccessControl.DirectorySecurity]::new()
    $acl.SetOwner($sids[0])
    $acl.SetAccessRuleProtection($true, $false)
    foreach ($sid in $sids) {
        $rule = [Security.AccessControl.FileSystemAccessRule]::new(
            $sid,
            [Security.AccessControl.FileSystemRights]::FullControl,
            $inheritance,
            [Security.AccessControl.PropagationFlags]::None,
            [Security.AccessControl.AccessControlType]::Allow
        )
        $null = $acl.AddAccessRule($rule)
    }
    [IO.FileSystemAclExtensions]::SetAccessControl(
        [IO.DirectoryInfo]::new($LiteralPath),
        $acl
    )
}

function Set-EvelynArchivePrivateFileAcl {
    param(
        [Parameter(Mandatory = $true)][string]$LiteralPath,
        [Parameter(Mandatory = $true)][string]$AdminSid
    )

    $sids = Get-EvelynArchiveAllowedSids -AdminSid $AdminSid
    $acl = [Security.AccessControl.FileSecurity]::new()
    $acl.SetOwner($sids[0])
    $acl.SetAccessRuleProtection($true, $false)
    foreach ($sid in $sids) {
        $rule = [Security.AccessControl.FileSystemAccessRule]::new(
            $sid,
            [Security.AccessControl.FileSystemRights]::FullControl,
            [Security.AccessControl.AccessControlType]::Allow
        )
        $null = $acl.AddAccessRule($rule)
    }
    [IO.FileSystemAclExtensions]::SetAccessControl(
        [IO.FileInfo]::new($LiteralPath),
        $acl
    )
}

function Assert-EvelynArchivePrivateAcl {
    param(
        [Parameter(Mandatory = $true)][string]$LiteralPath,
        [Parameter(Mandatory = $true)][string]$AdminSid,
        [Parameter(Mandatory = $true)]
        [ValidateSet('Directory', 'File')]
        [string]$Kind
    )

    if (-not (Test-Path -LiteralPath $LiteralPath)) {
        throw 'archive_provision_acl_invalid'
    }
    if (Test-EvelynArchiveReparsePath -LiteralPath $LiteralPath) {
        throw 'archive_provision_acl_invalid'
    }
    $item = Get-Item -LiteralPath $LiteralPath -Force
    if (
        ($Kind -ceq 'Directory' -and -not $item.PSIsContainer) -or
        ($Kind -ceq 'File' -and $item.PSIsContainer)
    ) {
        throw 'archive_provision_acl_invalid'
    }
    $acl = Get-Acl -LiteralPath $LiteralPath -ErrorAction Stop
    try {
        $ownerSid = $acl.GetOwner(
            [Security.Principal.SecurityIdentifier]
        ).Value
    }
    catch {
        throw 'archive_provision_acl_invalid'
    }
    if ($ownerSid -cne $AdminSid -or -not $acl.AreAccessRulesProtected) {
        throw 'archive_provision_acl_invalid'
    }
    $expectedSids = @($AdminSid, 'S-1-5-18')
    $seen = @{}
    $rules = @($acl.GetAccessRules(
        $true,
        $true,
        [Security.Principal.SecurityIdentifier]
    ))
    foreach ($rule in $rules) {
        $sid = [string]$rule.IdentityReference.Value
        if (
            $rule.AccessControlType -ne
                [Security.AccessControl.AccessControlType]::Allow -or
            $rule.IsInherited -or
            $expectedSids -cnotcontains $sid -or
            ($rule.FileSystemRights -band
                [Security.AccessControl.FileSystemRights]::FullControl) -ne
                [Security.AccessControl.FileSystemRights]::FullControl
        ) {
            throw 'archive_provision_acl_invalid'
        }
        if ($Kind -ceq 'Directory') {
            $required = (
                [Security.AccessControl.InheritanceFlags]::ContainerInherit -bor
                [Security.AccessControl.InheritanceFlags]::ObjectInherit
            )
            if (($rule.InheritanceFlags -band $required) -ne $required) {
                throw 'archive_provision_acl_invalid'
            }
            if (
                $rule.PropagationFlags -ne
                    [Security.AccessControl.PropagationFlags]::None
            ) {
                throw 'archive_provision_acl_invalid'
            }
        }
        elseif (
            $rule.InheritanceFlags -ne
                [Security.AccessControl.InheritanceFlags]::None -or
            $rule.PropagationFlags -ne
                [Security.AccessControl.PropagationFlags]::None
        ) {
            throw 'archive_provision_acl_invalid'
        }
        $seen[$sid] = $true
    }
    if (
        $rules.Count -ne 2 -or
        -not $seen.ContainsKey($AdminSid) -or
        -not $seen.ContainsKey('S-1-5-18')
    ) {
        throw 'archive_provision_acl_invalid'
    }
}

function ConvertTo-EvelynArchivePem {
    param(
        [Parameter(Mandatory = $true)][string]$Label,
        [Parameter(Mandatory = $true)][byte[]]$Bytes
    )

    $base64 = [Convert]::ToBase64String($Bytes)
    $builder = [Text.StringBuilder]::new()
    $null = $builder.Append('-----BEGIN ').Append($Label).Append("-----`n")
    for ($offset = 0; $offset -lt $base64.Length; $offset += 64) {
        $length = [Math]::Min(64, $base64.Length - $offset)
        $null = $builder.Append($base64, $offset, $length).Append("`n")
    }
    $null = $builder.Append('-----END ').Append($Label).Append("-----`n")
    return $builder.ToString()
}

function New-EvelynArchiveLoopbackTlsMaterial {
    $rsa = [Security.Cryptography.RSA]::Create(3072)
    $certificate = $null
    $certificateBytes = $null
    $privateKeyBytes = $null
    try {
        $request = [Security.Cryptography.X509Certificates.CertificateRequest]::new(
            'CN=Evelyn Local Conversation Archive',
            $rsa,
            [Security.Cryptography.HashAlgorithmName]::SHA256,
            [Security.Cryptography.RSASignaturePadding]::Pkcs1
        )
        $san = [Security.Cryptography.X509Certificates.SubjectAlternativeNameBuilder]::new()
        $san.AddIpAddress([Net.IPAddress]::Parse('127.0.0.1'))
        $san.AddIpAddress([Net.IPAddress]::Parse('::1'))
        $request.CertificateExtensions.Add($san.Build())
        $request.CertificateExtensions.Add(
            [Security.Cryptography.X509Certificates.X509BasicConstraintsExtension]::new(
                $false,
                $false,
                0,
                $true
            )
        )
        $usage = (
            [Security.Cryptography.X509Certificates.X509KeyUsageFlags]::DigitalSignature -bor
            [Security.Cryptography.X509Certificates.X509KeyUsageFlags]::KeyEncipherment
        )
        $request.CertificateExtensions.Add(
            [Security.Cryptography.X509Certificates.X509KeyUsageExtension]::new(
                $usage,
                $true
            )
        )
        $enhancedUsage = [Security.Cryptography.OidCollection]::new()
        $null = $enhancedUsage.Add(
            [Security.Cryptography.Oid]::new('1.3.6.1.5.5.7.3.1')
        )
        $request.CertificateExtensions.Add(
            [Security.Cryptography.X509Certificates.X509EnhancedKeyUsageExtension]::new(
                $enhancedUsage,
                $true
            )
        )
        $now = [DateTimeOffset]::UtcNow
        $certificate = $request.CreateSelfSigned(
            $now.AddMinutes(-5),
            $now.AddDays(397)
        )
        $certificateBytes = $certificate.Export(
            [Security.Cryptography.X509Certificates.X509ContentType]::Cert
        )
        $privateKeyBytes = $rsa.ExportPkcs8PrivateKey()
        return [pscustomobject]@{
            Certificate = ConvertTo-EvelynArchivePem `
                -Label 'CERTIFICATE' `
                -Bytes $certificateBytes
            PrivateKey = ConvertTo-EvelynArchivePem `
                -Label 'PRIVATE KEY' `
                -Bytes $privateKeyBytes
        }
    }
    finally {
        if ($null -ne $certificateBytes) {
            [Array]::Clear($certificateBytes, 0, $certificateBytes.Length)
        }
        if ($null -ne $privateKeyBytes) {
            [Array]::Clear($privateKeyBytes, 0, $privateKeyBytes.Length)
        }
        if ($null -ne $certificate) {
            $certificate.Dispose()
        }
        $rsa.Dispose()
    }
}

function Write-EvelynArchiveNewPrivateFile {
    param(
        [Parameter(Mandatory = $true)][string]$LiteralPath,
        [Parameter(Mandatory = $true)][byte[]]$Bytes,
        [Parameter(Mandatory = $true)][string]$AdminSid
    )

    if (Test-Path -LiteralPath $LiteralPath) {
        throw 'archive_provision_file_exists'
    }
    $directory = Split-Path -Parent $LiteralPath
    Assert-EvelynArchivePrivateAcl `
        -LiteralPath $directory `
        -AdminSid $AdminSid `
        -Kind Directory
    $temporary = Join-Path $directory (
        '.' + [IO.Path]::GetFileName($LiteralPath) + '.' + $PID + '.' +
        [Guid]::NewGuid().ToString('N') + '.tmp'
    )
    $stream = $null
    try {
        $stream = [IO.File]::Open(
            $temporary,
            [IO.FileMode]::CreateNew,
            [IO.FileAccess]::Write,
            [IO.FileShare]::None
        )
        $stream.Write($Bytes, 0, $Bytes.Length)
        $stream.Flush($true)
        $stream.Dispose()
        $stream = $null
        Set-EvelynArchivePrivateFileAcl `
            -LiteralPath $temporary `
            -AdminSid $AdminSid
        Assert-EvelynArchivePrivateAcl `
            -LiteralPath $temporary `
            -AdminSid $AdminSid `
            -Kind File
        [IO.File]::Move($temporary, $LiteralPath)
        Assert-EvelynArchivePrivateAcl `
            -LiteralPath $LiteralPath `
            -AdminSid $AdminSid `
            -Kind File
    }
    finally {
        if ($null -ne $stream) {
            $stream.Dispose()
        }
        if (Test-Path -LiteralPath $temporary) {
            [IO.File]::Delete($temporary)
        }
    }
}

function New-EvelynArchiveOwnerMarkerBytes {
    param(
        [Parameter(Mandatory = $true)][string]$ProvisionId,
        [Parameter(Mandatory = $true)]
        [ValidateSet('primary', 'replica', 'anchor', 'secrets')]
        [string]$Role
    )

    $marker = [ordered]@{
        schema = $script:ArchiveOwnerMarkerSchema
        purpose = $script:ArchiveOwnerMarkerPurpose
        installId = $ProvisionId
        role = $Role
    }
    return [Text.UTF8Encoding]::new($false).GetBytes(
        ($marker | ConvertTo-Json -Compress)
    )
}

function Read-EvelynArchiveOwnerMarker {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$AdminSid,
        [Parameter(Mandatory = $true)]
        [ValidateSet('primary', 'replica', 'anchor', 'secrets')]
        [string]$Role
    )

    Assert-EvelynArchivePrivateAcl `
        -LiteralPath $Root `
        -AdminSid $AdminSid `
        -Kind Directory
    $markerPath = Join-Path $Root $script:ArchiveOwnerMarkerName
    Assert-EvelynArchivePrivateAcl `
        -LiteralPath $markerPath `
        -AdminSid $AdminSid `
        -Kind File
    try {
        $item = Get-Item -LiteralPath $markerPath -Force
        if ($item.Length -gt 4096) {
            throw 'archive_provision_marker_invalid'
        }
        $marker = ConvertFrom-Json `
            -InputObject ([IO.File]::ReadAllText($markerPath)) `
            -AsHashtable
    }
    catch {
        throw 'archive_provision_marker_invalid'
    }
    if (
        -not ($marker -is [Collections.IDictionary]) -or
        $marker.Count -ne 4 -or
        $marker.schema -cne $script:ArchiveOwnerMarkerSchema -or
        $marker.purpose -cne $script:ArchiveOwnerMarkerPurpose -or
        $marker.installId -notmatch '^[0-9a-f]{32}$' -or
        $marker.role -cne $Role
    ) {
        throw 'archive_provision_marker_invalid'
    }
    return [string]$marker.installId
}

function New-EvelynArchiveSecretMaterial {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$AdminSid
    )

    $seen = [Collections.Generic.HashSet[string]]::new(
        [StringComparer]::Ordinal
    )
    foreach ($name in $script:ArchiveKeyNames) {
        $bytes = [byte[]]::new(32)
        try {
            [Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
            $encoded = [Convert]::ToBase64String($bytes)
            if (-not $seen.Add($encoded)) {
                throw 'archive_provision_key_collision'
            }
            Write-EvelynArchiveNewPrivateFile `
                -LiteralPath (Join-Path $Root $name) `
                -Bytes $bytes `
                -AdminSid $AdminSid
        }
        finally {
            [Array]::Clear($bytes, 0, $bytes.Length)
        }
    }
    $tls = New-EvelynArchiveLoopbackTlsMaterial
    $certificateBytes = [Text.UTF8Encoding]::new($false).GetBytes(
        $tls.Certificate
    )
    $privateKeyBytes = [Text.UTF8Encoding]::new($false).GetBytes(
        $tls.PrivateKey
    )
    try {
        Write-EvelynArchiveNewPrivateFile `
            -LiteralPath (Join-Path $Root $script:ArchiveTlsCertName) `
            -Bytes $certificateBytes `
            -AdminSid $AdminSid
        Write-EvelynArchiveNewPrivateFile `
            -LiteralPath (Join-Path $Root $script:ArchiveTlsKeyName) `
            -Bytes $privateKeyBytes `
            -AdminSid $AdminSid
    }
    finally {
        [Array]::Clear($certificateBytes, 0, $certificateBytes.Length)
        [Array]::Clear($privateKeyBytes, 0, $privateKeyBytes.Length)
    }
}

function Assert-EvelynArchiveSecretMaterial {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$AdminSid
    )

    $seen = [Collections.Generic.HashSet[string]]::new(
        [StringComparer]::Ordinal
    )
    foreach ($name in $script:ArchiveKeyNames) {
        $path = Join-Path $Root $name
        Assert-EvelynArchivePrivateAcl `
            -LiteralPath $path `
            -AdminSid $AdminSid `
            -Kind File
        $bytes = [IO.File]::ReadAllBytes($path)
        try {
            if (
                $bytes.Length -ne 32 -or
                -not $seen.Add([Convert]::ToBase64String($bytes))
            ) {
                throw 'archive_provision_key_invalid'
            }
        }
        finally {
            [Array]::Clear($bytes, 0, $bytes.Length)
        }
    }
    $certificatePath = Join-Path $Root $script:ArchiveTlsCertName
    $privateKeyPath = Join-Path $Root $script:ArchiveTlsKeyName
    foreach ($path in @($certificatePath, $privateKeyPath)) {
        Assert-EvelynArchivePrivateAcl `
            -LiteralPath $path `
            -AdminSid $AdminSid `
            -Kind File
        $item = Get-Item -LiteralPath $path -Force
        if ($item.Length -lt 64 -or $item.Length -gt 16384) {
            throw 'archive_provision_tls_invalid'
        }
    }
    $certificate = $null
    try {
        $certificate = [Security.Cryptography.X509Certificates.X509Certificate2]::CreateFromPemFile(
            $certificatePath,
            $privateKeyPath
        )
        if (
            -not $certificate.HasPrivateKey -or
            [Convert]::ToBase64String($certificate.SubjectName.RawData) -cne
                [Convert]::ToBase64String($certificate.IssuerName.RawData) -or
            $certificate.NotBefore.ToUniversalTime() -gt [DateTime]::UtcNow -or
            $certificate.NotAfter.ToUniversalTime() -le [DateTime]::UtcNow.AddDays(1) -or
            -not $certificate.MatchesHostname('127.0.0.1', $true, $false) -or
            -not $certificate.MatchesHostname('::1', $true, $false)
        ) {
            throw 'archive_provision_tls_invalid'
        }
        $publicKey = [Security.Cryptography.X509Certificates.RSACertificateExtensions]::GetRSAPublicKey(
            $certificate
        )
        try {
            if ($null -eq $publicKey -or $publicKey.KeySize -lt 3072) {
                throw 'archive_provision_tls_invalid'
            }
        }
        finally {
            if ($null -ne $publicKey) {
                $publicKey.Dispose()
            }
        }
        $enhancedUsage = @($certificate.Extensions | Where-Object {
            $_.Oid.Value -ceq '2.5.29.37'
        })
        if ($enhancedUsage.Count -ne 1) {
            throw 'archive_provision_tls_invalid'
        }
        $enhancedUsageExtension = [Security.Cryptography.X509Certificates.X509EnhancedKeyUsageExtension]$enhancedUsage[0]
        $serverAuth = @(
            $enhancedUsageExtension.EnhancedKeyUsages | Where-Object {
                $_.Value -ceq '1.3.6.1.5.5.7.3.1'
            }
        )
        if ($serverAuth.Count -ne 1) {
            throw 'archive_provision_tls_invalid'
        }
        $basicConstraints = @($certificate.Extensions | Where-Object {
            $_.Oid.Value -ceq '2.5.29.19'
        })
        if ($basicConstraints.Count -ne 1) {
            throw 'archive_provision_tls_invalid'
        }
        $basicConstraintsExtension = [Security.Cryptography.X509Certificates.X509BasicConstraintsExtension]$basicConstraints[0]
        if ($basicConstraintsExtension.CertificateAuthority) {
            throw 'archive_provision_tls_invalid'
        }
    }
    catch {
        if ($_.Exception.Message -eq 'archive_provision_tls_invalid') {
            throw
        }
        throw 'archive_provision_tls_invalid'
    }
    finally {
        if ($null -ne $certificate) {
            $certificate.Dispose()
        }
    }
}

function New-EvelynArchiveProvisionDirectory {
    param(
        [Parameter(Mandatory = $true)][string]$Target,
        [Parameter(Mandatory = $true)][string]$AdminSid,
        [Parameter(Mandatory = $true)][string]$ProvisionId,
        [Parameter(Mandatory = $true)]
        [ValidateSet('primary', 'replica', 'anchor', 'secrets')]
        [string]$Role
    )

    if (Test-Path -LiteralPath $Target) {
        throw 'archive_provision_target_exists'
    }
    Assert-EvelynArchivePathAncestors -LiteralPath $Target
    $parent = Split-Path -Parent $Target
    if (-not (Test-Path -LiteralPath $parent -PathType Container)) {
        throw 'archive_provision_parent_unavailable'
    }
    $staging = Join-Path $parent (
        '.' + [IO.Path]::GetFileName($Target) + '.' + $PID + '.' +
        [Guid]::NewGuid().ToString('N') + '.tmp'
    )
    $moved = $false
    try {
        $null = [IO.Directory]::CreateDirectory($staging)
        Set-EvelynArchivePrivateDirectoryAcl `
            -LiteralPath $staging `
            -AdminSid $AdminSid
        Assert-EvelynArchivePrivateAcl `
            -LiteralPath $staging `
            -AdminSid $AdminSid `
            -Kind Directory
        $markerBytes = New-EvelynArchiveOwnerMarkerBytes `
            -ProvisionId $ProvisionId `
            -Role $Role
        try {
            Write-EvelynArchiveNewPrivateFile `
                -LiteralPath (Join-Path $staging $script:ArchiveOwnerMarkerName) `
                -Bytes $markerBytes `
                -AdminSid $AdminSid
        }
        finally {
            [Array]::Clear($markerBytes, 0, $markerBytes.Length)
        }
        if ($Role -ceq 'secrets') {
            New-EvelynArchiveSecretMaterial `
                -Root $staging `
                -AdminSid $AdminSid
            Assert-EvelynArchiveSecretMaterial `
                -Root $staging `
                -AdminSid $AdminSid
        }
        [IO.Directory]::Move($staging, $Target)
        $moved = $true
        $actualId = Read-EvelynArchiveOwnerMarker `
            -Root $Target `
            -AdminSid $AdminSid `
            -Role $Role
        if ($actualId -cne $ProvisionId) {
            throw 'archive_provision_marker_invalid'
        }
        if ($Role -ceq 'secrets') {
            Assert-EvelynArchiveSecretMaterial `
                -Root $Target `
                -AdminSid $AdminSid
        }
    }
    catch {
        if ($moved -and (Test-Path -LiteralPath $Target -PathType Container)) {
            if (Test-EvelynArchiveReparsePath -LiteralPath $Target) {
                throw 'archive_provision_rollback_unsafe'
            }
            [IO.Directory]::Delete($Target, $true)
        }
        throw
    }
    finally {
        if (Test-Path -LiteralPath $staging -PathType Container) {
            if (Test-EvelynArchiveReparsePath -LiteralPath $staging) {
                throw 'archive_provision_rollback_unsafe'
            }
            [IO.Directory]::Delete($staging, $true)
        }
    }
}

function New-EvelynArchivePrivateParent {
    param(
        [Parameter(Mandatory = $true)][string]$Target,
        [Parameter(Mandatory = $true)][string]$AdminSid
    )

    if (Test-Path -LiteralPath $Target) {
        Assert-EvelynArchivePathAncestors -LiteralPath $Target
        return $false
    }
    Assert-EvelynArchivePathAncestors -LiteralPath $Target
    $parent = Split-Path -Parent $Target
    if (-not (Test-Path -LiteralPath $parent -PathType Container)) {
        throw 'archive_provision_parent_unavailable'
    }
    $created = $false
    try {
        $null = [IO.Directory]::CreateDirectory($Target)
        $created = $true
        Set-EvelynArchivePrivateDirectoryAcl `
            -LiteralPath $Target `
            -AdminSid $AdminSid
        Assert-EvelynArchivePrivateAcl `
            -LiteralPath $Target `
            -AdminSid $AdminSid `
            -Kind Directory
        return $true
    }
    catch {
        if (
            $created -and
            (Test-Path -LiteralPath $Target -PathType Container) -and
            @([IO.Directory]::EnumerateFileSystemEntries($Target)).Count -eq 0
        ) {
            if (Test-EvelynArchiveReparsePath -LiteralPath $Target) {
                throw 'archive_provision_rollback_unsafe'
            }
            [IO.Directory]::Delete($Target, $false)
        }
        throw
    }
}

function Undo-EvelynArchiveProvisionCreation {
    param(
        [Parameter(Mandatory = $true)]
        [AllowEmptyCollection()]
        [Collections.Generic.List[string]]$CreatedTargets,
        [Parameter(Mandatory = $true)]
        [AllowEmptyCollection()]
        [Collections.Generic.List[string]]$CreatedParents
    )

    for ($index = $CreatedTargets.Count - 1; $index -ge 0; $index--) {
        $target = $CreatedTargets[$index]
        if (-not (Test-Path -LiteralPath $target)) {
            continue
        }
        if (Test-EvelynArchiveReparsePath -LiteralPath $target) {
            throw 'archive_provision_rollback_unsafe'
        }
        [IO.Directory]::Delete($target, $true)
    }
    for ($index = $CreatedParents.Count - 1; $index -ge 0; $index--) {
        $parent = $CreatedParents[$index]
        if (-not (Test-Path -LiteralPath $parent)) {
            continue
        }
        if (Test-EvelynArchiveReparsePath -LiteralPath $parent) {
            throw 'archive_provision_rollback_unsafe'
        }
        if (@([IO.Directory]::EnumerateFileSystemEntries($parent)).Count -eq 0) {
            [IO.Directory]::Delete($parent, $false)
        }
    }
}

function Initialize-EvelynArchiveTestProvision {
    param(
        [Parameter(Mandatory = $true)]
        [Collections.IDictionary]$Roots,
        [Parameter(Mandatory = $true)][string]$AdminSid
    )

    $expectedRoles = @('primary', 'replica', 'anchor', 'secrets')
    if (
        $Roots.Count -ne $expectedRoles.Count -or
        @($expectedRoles | Where-Object { -not $Roots.Contains($_) }).Count
    ) {
        throw 'archive_provision_roots_invalid'
    }
    $normalizedRoots = @($expectedRoles | ForEach-Object {
        [IO.Path]::GetFullPath([string]$Roots[$_]).TrimEnd('\').ToUpperInvariant()
    })
    if (@($normalizedRoots | Select-Object -Unique).Count -ne $expectedRoles.Count) {
        throw 'archive_provision_roots_invalid'
    }
    foreach ($role in $expectedRoles) {
        Assert-EvelynArchivePathAncestors -LiteralPath ([string]$Roots[$role])
    }
    $existingIds = [Collections.Generic.HashSet[string]]::new(
        [StringComparer]::Ordinal
    )
    $existingCount = 0
    foreach ($role in $expectedRoles) {
        $root = [string]$Roots[$role]
        if (-not (Test-Path -LiteralPath $root)) {
            continue
        }
        $existingCount++
        $null = $existingIds.Add((Read-EvelynArchiveOwnerMarker `
            -Root $root `
            -AdminSid $AdminSid `
            -Role $role
        ))
        if ($role -ceq 'secrets') {
            Assert-EvelynArchiveSecretMaterial `
                -Root $root `
                -AdminSid $AdminSid
        }
    }
    if ($existingIds.Count -gt 1) {
        throw 'archive_provision_install_id_mismatch'
    }
    $provisionId = if ($existingIds.Count -eq 1) {
        [string]@($existingIds)[0]
    }
    else {
        [Guid]::NewGuid().ToString('N')
    }
    $createdTargets = [Collections.Generic.List[string]]::new()
    $createdParents = [Collections.Generic.List[string]]::new()
    try {
        $parents = [Collections.Generic.List[string]]::new()
        foreach ($role in $expectedRoles) {
            $parent = Split-Path -Parent ([string]$Roots[$role])
            if (-not $parents.Contains($parent)) {
                $parents.Add($parent)
            }
        }
        foreach ($parent in $parents) {
            if (New-EvelynArchivePrivateParent `
                -Target $parent `
                -AdminSid $AdminSid
            ) {
                $createdParents.Add($parent)
            }
        }
        foreach ($role in $expectedRoles) {
            $root = [string]$Roots[$role]
            if (Test-Path -LiteralPath $root) {
                continue
            }
            New-EvelynArchiveProvisionDirectory `
                -Target $root `
                -AdminSid $AdminSid `
                -ProvisionId $provisionId `
                -Role $role
            $createdTargets.Add($root)
        }
        foreach ($role in $expectedRoles) {
            $root = [string]$Roots[$role]
            $actualId = Read-EvelynArchiveOwnerMarker `
                -Root $root `
                -AdminSid $AdminSid `
                -Role $role
            if ($actualId -cne $provisionId) {
                throw 'archive_provision_install_id_mismatch'
            }
            if ($role -ceq 'secrets') {
                Assert-EvelynArchiveSecretMaterial `
                    -Root $root `
                    -AdminSid $AdminSid
            }
        }
        return [pscustomobject]@{
            InstallId = $provisionId
            Created = $createdTargets.Count
            Reused = $existingCount
        }
    }
    catch {
        Undo-EvelynArchiveProvisionCreation `
            -CreatedTargets $createdTargets `
            -CreatedParents $createdParents
        throw
    }
}

function Assert-EvelynArchiveFixedRoots {
    param([Parameter(Mandatory = $true)][Collections.IDictionary]$Roots)

    $expected = [ordered]@{
        primary = 'C:\ProgramData\Evelyn\private-audit'
        replica = 'D:\EvelynBackup\private-audit'
        anchor = 'C:\ProgramData\Evelyn\private-audit-anchor'
        secrets = 'C:\ProgramData\Evelyn\private-audit-secrets'
    }
    foreach ($role in $expected.Keys) {
        if (
            -not [IO.Path]::GetFullPath([string]$Roots[$role]).TrimEnd('\').Equals(
                [IO.Path]::GetFullPath([string]$expected[$role]).TrimEnd('\'),
                [StringComparison]::OrdinalIgnoreCase
            )
        ) {
            throw 'archive_provision_fixed_path_required'
        }
    }
}

function Assert-EvelynArchiveVolumesReady {
    $evidence = @{}
    foreach ($driveLetter in @('C', 'D')) {
        $volume = Get-Volume -DriveLetter $driveLetter -ErrorAction Stop
        $partition = Get-Partition -DriveLetter $driveLetter -ErrorAction Stop
        $disk = Get-Disk -Number $partition.DiskNumber -ErrorAction Stop
        $bitLocker = Get-BitLockerVolume `
            -MountPoint "$driveLetter`:" `
            -ErrorAction Stop
        if (
            [string]::IsNullOrWhiteSpace([string]$volume.UniqueId) -or
            [string]::IsNullOrWhiteSpace([string]$disk.UniqueId) -or
            [string]$volume.DriveType -cne 'Fixed' -or
            [string]$volume.FileSystem -cne 'NTFS' -or
            [string]$volume.HealthStatus -cne 'Healthy' -or
            [string]$bitLocker.ProtectionStatus -cne 'On' -or
            [string]$bitLocker.VolumeStatus -cne 'FullyEncrypted' -or
            [string]$bitLocker.LockStatus -cne 'Unlocked'
        ) {
            throw 'archive_provision_volume_preflight_failed'
        }
        $evidence[$driveLetter] = [pscustomobject]@{
            VolumeId = [string]$volume.UniqueId
            DiskId = [string]$disk.UniqueId
        }
    }
    if (
        $evidence.C.VolumeId.Equals(
            $evidence.D.VolumeId,
            [StringComparison]::OrdinalIgnoreCase
        ) -or
        $evidence.C.DiskId.Equals(
            $evidence.D.DiskId,
            [StringComparison]::OrdinalIgnoreCase
        )
    ) {
        throw 'archive_provision_volume_preflight_failed'
    }
}

function Assert-EvelynArchiveServicesStopped {
    $listenerCommand = Get-Command Get-NetTCPConnection -ErrorAction SilentlyContinue
    if ($null -ne $listenerCommand) {
        $listeners = @(Get-NetTCPConnection `
            -State Listen `
            -ErrorAction SilentlyContinue | Where-Object {
                $_.LocalPort -in @(8798, 8800)
            })
        if ($listeners.Count) {
            throw 'archive_provision_services_running'
        }
    }
    if ($null -eq (Get-Command docker -ErrorAction SilentlyContinue)) {
        return
    }
    foreach ($container in @(
        'evelyn-bot-api',
        'evelyn-control-page',
        'evelyn-discord-bot'
    )) {
        $state = & docker inspect `
            --format '{{json .State.Running}}' `
            $container 2>$null
        if ($LASTEXITCODE -eq 0 -and [string]$state.Trim() -ceq 'true') {
            throw 'archive_provision_services_running'
        }
    }
}

if ($LibraryOnly) {
    return
}

$roots = [ordered]@{
    primary = 'C:\ProgramData\Evelyn\private-audit'
    replica = 'D:\EvelynBackup\private-audit'
    anchor = 'C:\ProgramData\Evelyn\private-audit-anchor'
    secrets = 'C:\ProgramData\Evelyn\private-audit-secrets'
}

try {
    if (
        [string]::IsNullOrWhiteSpace($ExpectedAdminSid) -or
        [string]::IsNullOrWhiteSpace($ExpectedAdminAccount) -or
        $ExpectedAdminAccount.Length -gt 256 -or
        $ExpectedAdminAccount.IndexOfAny(
            [char[]]@('"', "`0", "`r", "`n")
        ) -ge 0
    ) {
        throw 'archive_provision_identity_unconfigured'
    }
    Assert-EvelynArchiveFixedRoots -Roots $roots
    if (-not (Test-EvelynArchiveElevatedAdministrator)) {
        if ($ElevatedChild) {
            throw 'archive_provision_elevation_failed'
        }
        Start-EvelynArchiveElevatedProvisioner
    }
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    if (
        $identity.User.Value -cne $ExpectedAdminSid -or
        -not $identity.Name.Equals(
            $ExpectedAdminAccount,
            [StringComparison]::OrdinalIgnoreCase
        )
    ) {
        throw 'archive_provision_identity_mismatch'
    }
    Assert-EvelynArchiveServicesStopped
    Assert-EvelynArchiveVolumesReady
    $result = Initialize-EvelynArchiveTestProvision `
        -Roots $roots `
        -AdminSid $ExpectedAdminSid
    Write-EvelynArchiveProvisionStatus `
        -Ok $true `
        -State 'test_provision_ready' `
        -Created $result.Created `
        -Reused $result.Reused
    exit 0
}
catch {
    Write-EvelynArchiveProvisionStatus `
        -Ok $false `
        -State 'test_provision_failed'
    exit 1
}
