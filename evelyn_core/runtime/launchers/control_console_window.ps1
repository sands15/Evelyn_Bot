param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('list', 'show')]
    [string]$Action,
    [string]$Key
)

$ErrorActionPreference = 'Stop'

$windowSpecs = @(
    @{ key = 'main-llm'; title = 'Main-LLM'; port = 9820; aliases = @('main-llm', 'main_llm', 'main') }
    @{ key = 'router-llm'; title = 'Router-LLM'; port = 9822; aliases = @('router-llm', 'router_llm', 'router') }
    @{ key = 'sub-llm'; title = 'Sub-LLM'; port = 9821; aliases = @('sub-llm', 'sub_llm', 'sub') }
    @{ key = 'tts'; title = 'TTS'; port = 8880; aliases = @('tts', 'voice') }
    @{ key = 'control-page'; title = 'Control-Page'; port = 8799; aliases = @('control-page', 'control_page', 'page', 'docs') }
    @{ key = 'bot'; title = 'Bot'; port = 8798; aliases = @('bot', 'evelyn') }
)

$windowSpecByKey = @{}
$windowAliasMap = @{}
foreach ($spec in $windowSpecs) {
    $windowSpecByKey[$spec.key] = $spec
    $windowAliasMap[$spec.key] = $spec.key
    foreach ($alias in $spec.aliases) {
        $windowAliasMap[$alias] = $spec.key
    }
}

Add-Type @"
using System;
using System.Runtime.InteropServices;
using System.Text;

public static class EvelynWindowInterop {
    public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);

    [DllImport("user32.dll")]
    public static extern bool EnumWindows(EnumWindowsProc lpEnumFunc, IntPtr lParam);

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    public static extern int GetWindowText(IntPtr hWnd, StringBuilder lpString, int nMaxCount);

    [DllImport("user32.dll")]
    public static extern bool ShowWindowAsync(IntPtr hWnd, int nCmdShow);

    [DllImport("user32.dll")]
    public static extern bool SetForegroundWindow(IntPtr hWnd);
}
"@

function Normalize-WindowKey {
    param([string]$Value)
    if ([string]::IsNullOrWhiteSpace($Value)) {
        return $null
    }
    $normalized = $Value.Trim().ToLowerInvariant().Replace(' ', '-')
    if ($windowAliasMap.ContainsKey($normalized)) {
        return $windowAliasMap[$normalized]
    }
    return $null
}

function Get-WindowHandleByTitle {
    param([string]$Title)

    $script:matched = [IntPtr]::Zero
    $callback = [EvelynWindowInterop+EnumWindowsProc]{
        param([IntPtr]$hWnd, [IntPtr]$lParam)
        $builder = New-Object System.Text.StringBuilder 512
        [void][EvelynWindowInterop]::GetWindowText($hWnd, $builder, $builder.Capacity)
        if ($builder.ToString() -eq $Title) {
            $script:matched = $hWnd
            return $false
        }
        return $true
    }
    [void][EvelynWindowInterop]::EnumWindows($callback, [IntPtr]::Zero)
    return $script:matched
}

function Test-PortConnect {
    param(
        [string]$HostName = '127.0.0.1',
        [int]$Port,
        [int]$TimeoutMs = 1000
    )

    $client = $null
    try {
        $client = [System.Net.Sockets.TcpClient]::new()
        $iar = $client.BeginConnect($HostName, $Port, $null, $null)
        if ($iar.AsyncWaitHandle.WaitOne($TimeoutMs)) {
            $client.EndConnect($iar)
            return $true
        }
    } catch {
    } finally {
        if ($client) {
            $client.Close()
        }
    }
    return $false
}

if ($Action -eq 'list') {
    $rows = foreach ($spec in $windowSpecs) {
        $hWnd = Get-WindowHandleByTitle -Title $spec.title
        [ordered]@{
            key = $spec.key
            title = $spec.title
            port = $spec.port
            running = (Test-PortConnect -HostName '127.0.0.1' -Port $spec.port)
            windowFound = ($hWnd -ne [IntPtr]::Zero)
        }
    }
    [ordered]@{
        ok = $true
        windows = $rows
    } | ConvertTo-Json -Depth 4 -Compress
    exit 0
}

$resolvedKey = Normalize-WindowKey -Value $Key
if (-not $resolvedKey) {
    [ordered]@{
        ok = $false
        error = 'unknown_window_key'
        requested = $Key
    } | ConvertTo-Json -Depth 4 -Compress
    exit 1
}

$spec = $windowSpecByKey[$resolvedKey]
$handle = Get-WindowHandleByTitle -Title $spec.title
if ($handle -eq [IntPtr]::Zero) {
    [ordered]@{
        ok = $false
        error = 'window_not_found'
        key = $spec.key
        title = $spec.title
        running = (Test-PortConnect -HostName '127.0.0.1' -Port $spec.port)
    } | ConvertTo-Json -Depth 4 -Compress
    exit 1
}

[void][EvelynWindowInterop]::ShowWindowAsync($handle, 9)
Start-Sleep -Milliseconds 120
[void][EvelynWindowInterop]::ShowWindowAsync($handle, 5)
[void][EvelynWindowInterop]::SetForegroundWindow($handle)

[ordered]@{
    ok = $true
    key = $spec.key
    title = $spec.title
} | ConvertTo-Json -Depth 4 -Compress
