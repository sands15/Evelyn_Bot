param(
    [string]$ProjectRoot = (Join-Path $PSScriptRoot '..\..\..'),
    [ValidateSet('bot_api', 'control_page', 'vision')]
    [string[]]$Services = @('bot_api', 'control_page', 'vision')
)

$ErrorActionPreference = 'Stop'

$resolvedProjectRoot = [System.IO.Path]::GetFullPath(
    [string](Resolve-Path -LiteralPath $ProjectRoot -ErrorAction Stop)
).TrimEnd('\')
$imageDefinitions = @{
    bot_api = @{
        Dockerfile = 'docker\Dockerfile.bot-api'
        Image = 'evelyn-fast-control-bot_api'
    }
    control_page = @{
        Dockerfile = 'docker\Dockerfile.control-page'
        Image = 'evelyn-fast-control-control_page'
    }
    vision = @{
        Dockerfile = 'docker\Dockerfile.vision'
        Image = 'evelyn-fast-control-vision'
    }
}

if (-not ('EvelynSubstNativeMethods' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using System.Text;

public static class EvelynSubstNativeMethods
{
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    public static extern uint QueryDosDevice(
        string lpDeviceName,
        StringBuilder lpTargetPath,
        int ucchMax
    );
}
'@
}

function Get-SubstTarget {
    param([string]$Drive)

    $buffer = [System.Text.StringBuilder]::new(32768)
    $length = [EvelynSubstNativeMethods]::QueryDosDevice(
        $Drive,
        $buffer,
        $buffer.Capacity
    )
    if ($length -eq 0) {
        return $null
    }
    $target = ([string]$buffer.ToString()).Split([char]0)[0]
    if ($target.StartsWith('\??\', [System.StringComparison]::Ordinal)) {
        return $target.Substring(4)
    }
    return $target
}

function Invoke-DockerBuild {
    param(
        [string]$Dockerfile,
        [string]$Image
    )

    $arguments = @(
        'build',
        '--file', $Dockerfile,
        '--tag', $Image,
        '.'
    )
    $previousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $output = & docker.exe @arguments 2>&1
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
    if ($output) {
        $output | ForEach-Object { Write-Host $_ }
    }
    if ($exitCode -ne 0) {
        throw "docker $($arguments -join ' ') failed with exit code $exitCode"
    }
}

$buildRoot = $resolvedProjectRoot
$mappedDrive = $null
$requiresAsciiAlias = [bool]($resolvedProjectRoot -match '[^\x00-\x7F]')

try {
    if ($requiresAsciiAlias) {
        $existingFileSystemDrives = @(
            Get-PSDrive -PSProvider FileSystem -ErrorAction SilentlyContinue |
                ForEach-Object { [string]$_.Name }
        )
        foreach ($letter in @('Z', 'Y', 'X', 'W', 'V', 'U', 'T')) {
            $candidate = "${letter}:"
            if (
                $existingFileSystemDrives -contains $letter -or
                -not [string]::IsNullOrWhiteSpace((Get-SubstTarget -Drive $candidate))
            ) {
                continue
            }

            & subst.exe $candidate $resolvedProjectRoot
            if ($LASTEXITCODE -ne 0) {
                continue
            }
            $mappedDrive = $candidate
            $mappedTarget = Get-SubstTarget -Drive $candidate
            if ([string]::IsNullOrWhiteSpace($mappedTarget)) {
                throw "Temporary build drive $candidate could not be queried after creation."
            }
            $mappedTarget = [System.IO.Path]::GetFullPath($mappedTarget).TrimEnd('\')
            if (-not $mappedTarget.Equals(
                $resolvedProjectRoot,
                [System.StringComparison]::OrdinalIgnoreCase
            )) {
                throw "Temporary build drive $candidate resolved to an unexpected target."
            }
            $buildRoot = "$candidate\"
            Write-Host "[Evelyn] Using temporary ASCII Docker build root $buildRoot"
            break
        }
        if (-not $mappedDrive) {
            throw "No unused drive letter is available for the temporary Docker build root."
        }
    }

    Push-Location -LiteralPath $buildRoot
    try {
        foreach ($service in $Services) {
            $definition = $imageDefinitions[$service]
            $dockerfile = [string]$definition.Dockerfile
            if (-not (Test-Path -LiteralPath $dockerfile -PathType Leaf)) {
                throw "Dockerfile not found for allowlisted service ${service}: $dockerfile"
            }
            Write-Host "[Evelyn] Building allowlisted image $service."
            Invoke-DockerBuild -Dockerfile $dockerfile -Image ([string]$definition.Image)
        }
    } finally {
        Pop-Location
    }
} finally {
    if ($mappedDrive) {
        $currentTarget = Get-SubstTarget -Drive $mappedDrive
        if ([string]::IsNullOrWhiteSpace($currentTarget)) {
            throw "Temporary build drive $mappedDrive disappeared before cleanup."
        }
        $currentTarget = [System.IO.Path]::GetFullPath($currentTarget).TrimEnd('\')
        if (-not $currentTarget.Equals(
            $resolvedProjectRoot,
            [System.StringComparison]::OrdinalIgnoreCase
        )) {
            throw "Refusing to remove $mappedDrive because its target changed."
        }
        & subst.exe $mappedDrive '/D'
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to remove temporary build drive $mappedDrive."
        }
        Write-Host "[Evelyn] Removed temporary Docker build root $mappedDrive"
    }
}
