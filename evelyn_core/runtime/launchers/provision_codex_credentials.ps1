param(
    [string]$SourceAuthFile = '',
    [string]$SourceConfigFile = '',
    [string]$Destination = '',
    [switch]$IncludeConfig
)

$ErrorActionPreference = 'Stop'

$projectRoot = if ($env:EVELYN_PROJECT_ROOT) {
    [string](Resolve-Path -LiteralPath $env:EVELYN_PROJECT_ROOT)
} else {
    [string](Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..\..'))
}
$allowedRoot = [System.IO.Path]::GetFullPath(
    (Join-Path $projectRoot 'runtime_artifacts\secrets')
).TrimEnd('\', '/')
$destinationPath = if ($Destination) {
    [System.IO.Path]::GetFullPath($Destination)
} else {
    Join-Path $allowedRoot 'codex_device_home'
}
$destinationPrefix = $allowedRoot + [System.IO.Path]::DirectorySeparatorChar
if (
    -not $destinationPath.Equals(
        $allowedRoot,
        [System.StringComparison]::OrdinalIgnoreCase
    ) -and
    -not $destinationPath.StartsWith(
        $destinationPrefix,
        [System.StringComparison]::OrdinalIgnoreCase
    )
) {
    throw 'Codex credential destination must stay under runtime_artifacts\secrets.'
}

$authSource = if ($SourceAuthFile) {
    [System.IO.Path]::GetFullPath($SourceAuthFile)
} elseif ($env:EVELYN_CODEX_AUTH_FILE) {
    [System.IO.Path]::GetFullPath($env:EVELYN_CODEX_AUTH_FILE)
} else {
    Join-Path $env:USERPROFILE '.codex\auth.json'
}
if (-not (Test-Path -LiteralPath $authSource -PathType Leaf)) {
    throw 'Codex auth.json source is missing.'
}
if ((Get-Item -LiteralPath $authSource).Length -gt 1MB) {
    throw 'Codex auth.json exceeds the 1 MiB safety limit.'
}
try {
    Get-Content -LiteralPath $authSource -Raw -Encoding UTF8 |
        ConvertFrom-Json |
        Out-Null
} catch {
    throw 'Codex auth.json is not valid JSON.'
}

$configSource = ''
if ($IncludeConfig) {
    $configSource = if ($SourceConfigFile) {
        [System.IO.Path]::GetFullPath($SourceConfigFile)
    } elseif ($env:EVELYN_CODEX_CONFIG_FILE) {
        [System.IO.Path]::GetFullPath($env:EVELYN_CODEX_CONFIG_FILE)
    } else {
        Join-Path $env:USERPROFILE '.codex\config.toml'
    }
    if (-not (Test-Path -LiteralPath $configSource -PathType Leaf)) {
        throw 'Codex config.toml source is missing.'
    }
    if ((Get-Item -LiteralPath $configSource).Length -gt 1MB) {
        throw 'Codex config.toml exceeds the 1 MiB safety limit.'
    }
}

New-Item -ItemType Directory -Force -Path $destinationPath | Out-Null
Copy-Item -LiteralPath $authSource -Destination (
    Join-Path $destinationPath 'auth.json'
) -Force
if ($IncludeConfig) {
    Copy-Item -LiteralPath $configSource -Destination (
        Join-Path $destinationPath 'config.toml'
    ) -Force
} else {
    $staleConfig = Join-Path $destinationPath 'config.toml'
    if (Test-Path -LiteralPath $staleConfig -PathType Leaf) {
        Remove-Item -LiteralPath $staleConfig -Force
    }
}

$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
$directoryAcl = New-Object System.Security.AccessControl.DirectorySecurity
$directoryAcl.SetAccessRuleProtection($true, $false)
$directoryRule = New-Object System.Security.AccessControl.FileSystemAccessRule(
    $identity.User,
    'FullControl',
    'ContainerInherit,ObjectInherit',
    'None',
    'Allow'
)
$directoryAcl.AddAccessRule($directoryRule)
Set-Acl -LiteralPath $destinationPath -AclObject $directoryAcl

Get-ChildItem -LiteralPath $destinationPath -File |
    Where-Object { $_.Name -in @('auth.json', 'config.toml') } |
    ForEach-Object {
        $fileAcl = New-Object System.Security.AccessControl.FileSecurity
        $fileAcl.SetAccessRuleProtection($true, $false)
        $fileRule = New-Object System.Security.AccessControl.FileSystemAccessRule(
            $identity.User,
            'FullControl',
            'Allow'
        )
        $fileAcl.AddAccessRule($fileRule)
        Set-Acl -LiteralPath $_.FullName -AclObject $fileAcl
    }

Write-Output '[Evelyn] Dedicated Codex credentials are provisioned.'
Write-Output "[Evelyn] Directory: $destinationPath"
Write-Output (
    '[Evelyn] config.toml copied: ' +
    $(if ($IncludeConfig) { 'yes' } else { 'no' })
)
