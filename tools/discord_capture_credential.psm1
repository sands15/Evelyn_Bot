Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$script:tokenFileName = 'discord-bot-token.dpapi'
$script:temporaryFileName = '.discord-bot-token.dpapi.part'
$script:maxTokenBytes = 512
$script:maxCiphertextBytes = 4096
$script:entropy = [Text.Encoding]::UTF8.GetBytes(
    'evelyn.discord-capture-credential.v1'
)
# CurrentUser DPAPI and these ACLs isolate other OS principals and avoid
# plaintext-at-rest. A malicious process already running as this same Windows
# user is outside this boundary because it can invoke CurrentUser DPAPI too.

function Get-EvelynDiscordCredentialPaths {
    param(
        [Parameter(Mandatory = $true)][string]$TrustedRoot,
        [Parameter(Mandatory = $true)][string]$CredentialRoot
    )

    $trusted = [IO.Path]::GetFullPath($TrustedRoot).TrimEnd(
        [IO.Path]::DirectorySeparatorChar,
        [IO.Path]::AltDirectorySeparatorChar
    )
    $root = [IO.Path]::GetFullPath($CredentialRoot).TrimEnd(
        [IO.Path]::DirectorySeparatorChar,
        [IO.Path]::AltDirectorySeparatorChar
    )
    if (-not $root.StartsWith(
        $trusted + [IO.Path]::DirectorySeparatorChar,
        [StringComparison]::OrdinalIgnoreCase
    )) {
        throw 'discord_token_cache_unsafe'
    }
    return [pscustomobject]@{
        TrustedRoot = $trusted
        Root = $root
        Token = Join-Path $root $script:tokenFileName
        Temporary = Join-Path $root $script:temporaryFileName
    }
}

function Assert-EvelynDiscordCredentialPath {
    param([Parameter(Mandatory = $true)]$Paths)

    $relative = [IO.Path]::GetRelativePath($Paths.TrustedRoot, $Paths.Root)
    if ([IO.Path]::IsPathRooted($relative) -or $relative.StartsWith('..')) {
        throw 'discord_token_cache_unsafe'
    }
    $current = $Paths.TrustedRoot
    foreach ($part in $relative.Split(
        @([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar),
        [StringSplitOptions]::RemoveEmptyEntries
    )) {
        if (Test-Path -LiteralPath $current) {
            $item = Get-Item -Force -LiteralPath $current
            if (
                -not $item.PSIsContainer -or
                ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)
            ) {
                throw 'discord_token_cache_unsafe'
            }
        }
        $current = Join-Path $current $part
    }
    if (Test-Path -LiteralPath $current) {
        $item = Get-Item -Force -LiteralPath $current
        if (
            -not $item.PSIsContainer -or
            ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)
        ) {
            throw 'discord_token_cache_unsafe'
        }
    }
}

function Get-EvelynDiscordCredentialSids {
    $current = [Security.Principal.WindowsIdentity]::GetCurrent().User
    $system = [Security.Principal.SecurityIdentifier]::new('S-1-5-18')
    return [pscustomobject]@{ Current = $current; System = $system }
}

function Set-EvelynPrivateDirectoryAcl {
    param([Parameter(Mandatory = $true)][string]$Path)

    $sids = Get-EvelynDiscordCredentialSids
    $inheritance = (
        [Security.AccessControl.InheritanceFlags]::ContainerInherit -bor
        [Security.AccessControl.InheritanceFlags]::ObjectInherit
    )
    $acl = [Security.AccessControl.DirectorySecurity]::new()
    $acl.SetOwner($sids.Current)
    $acl.SetAccessRuleProtection($true, $false)
    foreach ($sid in @($sids.Current, $sids.System)) {
        $rule = [Security.AccessControl.FileSystemAccessRule]::new(
            $sid,
            [Security.AccessControl.FileSystemRights]::FullControl,
            $inheritance,
            [Security.AccessControl.PropagationFlags]::None,
            [Security.AccessControl.AccessControlType]::Allow
        )
        $null = $acl.AddAccessRule($rule)
    }
    Set-Acl -LiteralPath $Path -AclObject $acl
}

function Set-EvelynPrivateFileAcl {
    param([Parameter(Mandatory = $true)][string]$Path)

    $sids = Get-EvelynDiscordCredentialSids
    $file = [IO.FileInfo]::new($Path)
    $sections = (
        [Security.AccessControl.AccessControlSections]::Access -bor
        [Security.AccessControl.AccessControlSections]::Owner -bor
        [Security.AccessControl.AccessControlSections]::Group
    )
    $acl = [IO.FileSystemAclExtensions]::GetAccessControl($file, $sections)
    $acl.SetOwner($sids.Current)
    $acl.SetAccessRuleProtection($true, $false)
    foreach ($existingRule in @($acl.GetAccessRules(
        $true,
        $true,
        [Security.Principal.SecurityIdentifier]
    ))) {
        $acl.RemoveAccessRuleSpecific($existingRule)
    }
    foreach ($sid in @($sids.Current, $sids.System)) {
        $rule = [Security.AccessControl.FileSystemAccessRule]::new(
            $sid,
            [Security.AccessControl.FileSystemRights]::FullControl,
            [Security.AccessControl.AccessControlType]::Allow
        )
        $null = $acl.AddAccessRule($rule)
    }
    [IO.FileSystemAclExtensions]::SetAccessControl($file, $acl)
}

function Assert-EvelynPrivateAcl {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][ValidateSet('Directory', 'File')]
        [string]$Kind
    )

    $sids = Get-EvelynDiscordCredentialSids
    $acl = Get-Acl -LiteralPath $Path
    $owner = $acl.GetOwner([Security.Principal.SecurityIdentifier])
    if (
        $owner -ne $sids.Current -or
        -not $acl.AreAccessRulesProtected
    ) {
        throw 'discord_token_cache_unsafe'
    }
    $rules = @($acl.GetAccessRules(
        $true,
        $true,
        [Security.Principal.SecurityIdentifier]
    ))
    $seen = @{}
    foreach ($rule in $rules) {
        $sid = [string]$rule.IdentityReference.Value
        if (
            $rule.AccessControlType -ne
                [Security.AccessControl.AccessControlType]::Allow -or
            $sid -notin @([string]$sids.Current.Value, [string]$sids.System.Value) -or
            ($rule.FileSystemRights -band
                [Security.AccessControl.FileSystemRights]::FullControl) -ne
                [Security.AccessControl.FileSystemRights]::FullControl
        ) {
            throw 'discord_token_cache_unsafe'
        }
        $seen[$sid] = $true
    }
    if (
        $rules.Count -ne 2 -or
        -not $seen.ContainsKey([string]$sids.Current.Value) -or
        -not $seen.ContainsKey([string]$sids.System.Value)
    ) {
        throw 'discord_token_cache_unsafe'
    }
    if ($Kind -ceq 'Directory') {
        $required = (
            [Security.AccessControl.InheritanceFlags]::ContainerInherit -bor
            [Security.AccessControl.InheritanceFlags]::ObjectInherit
        )
        if (@($rules | Where-Object {
            ($_.InheritanceFlags -band $required) -ne $required
        }).Count) {
            throw 'discord_token_cache_unsafe'
        }
    }
}

function Initialize-EvelynDiscordCredentialStore {
    param(
        [Parameter(Mandatory = $true)][string]$TrustedRoot,
        [Parameter(Mandatory = $true)][string]$CredentialRoot
    )

    $paths = Get-EvelynDiscordCredentialPaths `
        -TrustedRoot $TrustedRoot `
        -CredentialRoot $CredentialRoot
    Assert-EvelynDiscordCredentialPath -Paths $paths
    if (-not (Test-Path -LiteralPath $paths.Root)) {
        $null = New-Item -ItemType Directory -Path $paths.Root -Force
        Assert-EvelynDiscordCredentialPath -Paths $paths
        Set-EvelynPrivateDirectoryAcl -Path $paths.Root
    }
    Assert-EvelynPrivateAcl -Path $paths.Root -Kind Directory
    return $paths
}

function Assert-EvelynPrivateTokenFile {
    param([Parameter(Mandatory = $true)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) {
        return $false
    }
    $item = Get-Item -Force -LiteralPath $Path
    if (
        $item.PSIsContainer -or
        ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)
    ) {
        throw 'discord_token_cache_unsafe'
    }
    Assert-EvelynPrivateAcl -Path $Path -Kind File
    return $true
}

function Assert-EvelynDiscordTokenBytes {
    param(
        [Parameter(Mandatory = $true)]
        [AllowEmptyCollection()]
        [byte[]]$TokenBytes
    )

    if ($TokenBytes.Length -le 0 -or $TokenBytes.Length -gt $script:maxTokenBytes) {
        throw 'discord_token_invalid'
    }
    $chars = $null
    try {
        $utf8 = [Text.UTF8Encoding]::new($false, $true)
        $chars = $utf8.GetChars($TokenBytes)
        if ($chars.Length -le 0) {
            throw 'discord_token_invalid'
        }
        foreach ($character in $chars) {
            if (
                [char]::IsWhiteSpace($character) -or
                [char]::IsControl($character)
            ) {
                throw 'discord_token_invalid'
            }
        }
    } catch {
        if ([string]$_.Exception.Message -ceq 'discord_token_invalid') {
            throw
        }
        throw 'discord_token_invalid'
    } finally {
        if ($null -ne $chars) {
            [Array]::Clear($chars, 0, $chars.Length)
        }
    }
}

function Read-EvelynDiscordTokenCache {
    param(
        [Parameter(Mandatory = $true)][string]$TrustedRoot,
        [Parameter(Mandatory = $true)][string]$CredentialRoot
    )

    $ciphertext = $null
    $plaintext = $null
    $success = $false
    try {
        $paths = Initialize-EvelynDiscordCredentialStore `
            -TrustedRoot $TrustedRoot `
            -CredentialRoot $CredentialRoot
        if (-not (Assert-EvelynPrivateTokenFile -Path $paths.Token)) {
            return $null
        }
        $length = (Get-Item -Force -LiteralPath $paths.Token).Length
        if ($length -le 0 -or $length -gt $script:maxCiphertextBytes) {
            throw 'discord_token_cache_invalid'
        }
        $ciphertext = [IO.File]::ReadAllBytes($paths.Token)
        $plaintext = [Security.Cryptography.ProtectedData]::Unprotect(
            $ciphertext,
            $script:entropy,
            [Security.Cryptography.DataProtectionScope]::CurrentUser
        )
        Assert-EvelynDiscordTokenBytes -TokenBytes $plaintext
        $success = $true
        return ,$plaintext
    } catch {
        if (
            [string]$_.Exception.Message -in @(
                'discord_token_cache_unsafe',
                'discord_token_cache_invalid'
            )
        ) {
            throw
        }
        throw 'discord_token_cache_invalid'
    } finally {
        if ($null -ne $ciphertext) {
            [Array]::Clear($ciphertext, 0, $ciphertext.Length)
        }
        if (-not $success -and $null -ne $plaintext) {
            [Array]::Clear($plaintext, 0, $plaintext.Length)
        }
    }
}

function Write-EvelynDiscordTokenCache {
    param(
        [Parameter(Mandatory = $true)][string]$TrustedRoot,
        [Parameter(Mandatory = $true)][string]$CredentialRoot,
        [Parameter(Mandatory = $true)][byte[]]$TokenBytes
    )

    $ciphertext = $null
    $stream = $null
    $temporaryCreated = $false
    try {
        Assert-EvelynDiscordTokenBytes -TokenBytes $TokenBytes
        $paths = Initialize-EvelynDiscordCredentialStore `
            -TrustedRoot $TrustedRoot `
            -CredentialRoot $CredentialRoot
        if (Test-Path -LiteralPath $paths.Temporary) {
            $null = Assert-EvelynPrivateTokenFile -Path $paths.Temporary
            [IO.File]::Delete($paths.Temporary)
        }
        if (Test-Path -LiteralPath $paths.Token) {
            $null = Assert-EvelynPrivateTokenFile -Path $paths.Token
        }
        $ciphertext = [Security.Cryptography.ProtectedData]::Protect(
            $TokenBytes,
            $script:entropy,
            [Security.Cryptography.DataProtectionScope]::CurrentUser
        )
        if (
            $ciphertext.Length -le 0 -or
            $ciphertext.Length -gt $script:maxCiphertextBytes
        ) {
            throw 'discord_token_cache_write_failed'
        }
        $stream = [IO.FileStream]::new(
            $paths.Temporary,
            [IO.FileMode]::CreateNew,
            [IO.FileAccess]::Write,
            [IO.FileShare]::None,
            4096,
            [IO.FileOptions]::WriteThrough
        )
        $temporaryCreated = $true
        $stream.Write($ciphertext, 0, $ciphertext.Length)
        $stream.Flush($true)
        $stream.Dispose()
        $stream = $null
        Set-EvelynPrivateFileAcl -Path $paths.Temporary
        Assert-EvelynPrivateAcl -Path $paths.Temporary -Kind File
        [IO.File]::Move($paths.Temporary, $paths.Token, $true)
        $temporaryCreated = $false
        Set-EvelynPrivateFileAcl -Path $paths.Token
        $null = Assert-EvelynPrivateTokenFile -Path $paths.Token
    } catch {
        if (
            [string]$_.Exception.Message -in @(
                'discord_token_invalid',
                'discord_token_cache_unsafe',
                'discord_token_cache_write_failed'
            )
        ) {
            throw
        }
        throw 'discord_token_cache_write_failed'
    } finally {
        if ($null -ne $stream) {
            $stream.Dispose()
        }
        if ($temporaryCreated -and $null -ne $paths) {
            try {
                if (Test-Path -LiteralPath $paths.Temporary) {
                    $null = Assert-EvelynPrivateTokenFile -Path $paths.Temporary
                    [IO.File]::Delete($paths.Temporary)
                }
            } catch {
            }
        }
        if ($null -ne $ciphertext) {
            [Array]::Clear($ciphertext, 0, $ciphertext.Length)
        }
    }
}

function Remove-EvelynDiscordTokenCache {
    param(
        [Parameter(Mandatory = $true)][string]$TrustedRoot,
        [Parameter(Mandatory = $true)][string]$CredentialRoot
    )

    $paths = Get-EvelynDiscordCredentialPaths `
        -TrustedRoot $TrustedRoot `
        -CredentialRoot $CredentialRoot
    Assert-EvelynDiscordCredentialPath -Paths $paths
    if (-not (Test-Path -LiteralPath $paths.Root)) {
        return $false
    }
    Assert-EvelynPrivateAcl -Path $paths.Root -Kind Directory
    $removed = $false
    foreach ($path in @($paths.Token, $paths.Temporary)) {
        if (Assert-EvelynPrivateTokenFile -Path $path) {
            [IO.File]::Delete($path)
            if ($path -ceq $paths.Token) {
                $removed = $true
            }
        }
    }
    return $removed
}

Export-ModuleMember -Function @(
    'Assert-EvelynDiscordTokenBytes',
    'Read-EvelynDiscordTokenCache',
    'Write-EvelynDiscordTokenCache',
    'Remove-EvelynDiscordTokenCache'
)
