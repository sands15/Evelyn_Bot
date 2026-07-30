param(
    [ValidateSet('invoke')]
    [string]$Action,

    [ValidatePattern('^[0-9a-f]{20}$')]
    [string]$ElementId,

    [ValidatePattern('^[0-9a-f]{64}$')]
    [string]$ExpectedWindowDigest
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

function Clean-UiActionText {
    param(
        [object]$Value,
        [int]$MaxChars
    )

    $text = [Regex]::Replace([string]$Value, '[\x00-\x1f\x7f]+', ' ').Trim()
    if ($text.Length -gt $MaxChars) {
        return $text.Substring(0, $MaxChars)
    }
    return $text
}

function Get-UiActionSha256 {
    param([string]$Value)

    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($Value)
        $hash = $sha.ComputeHash($bytes)
        return (-join ($hash | ForEach-Object { $_.ToString('x2') }))
    } finally {
        $sha.Dispose()
    }
}

function Write-UiActionResult {
    param(
        [bool]$Ok,
        [string]$ErrorCode,
        [bool]$Executed,
        [string]$WindowDigest
    )

    [PSCustomObject]@{
        schema = 'windows_ui_action.result.v1'
        ok = $Ok
        errorCode = $ErrorCode
        completedAt = [double]([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()) / 1000.0
        executed = $Executed
        action = $Action
        elementId = $ElementId
        windowDigest = $WindowDigest
    } | ConvertTo-Json -Depth 3 -Compress
}

try {
    Add-Type -AssemblyName UIAutomationClient
    Add-Type -AssemblyName UIAutomationTypes
    if (-not ('EvelynUiActionNativeMethods' -as [type])) {
        Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using System.Text;

public static class EvelynUiActionNativeMethods
{
    [DllImport("user32.dll")]
    public static extern IntPtr GetForegroundWindow();

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    public static extern int GetClassName(
        IntPtr hWnd,
        StringBuilder lpClassName,
        int nMaxCount
    );
}
'@
    }

    $windowHandle = [EvelynUiActionNativeMethods]::GetForegroundWindow()
    if ($windowHandle -eq [IntPtr]::Zero) {
        Write-UiActionResult -Ok $false -ErrorCode 'ui_action_foreground_missing' `
            -Executed $false -WindowDigest $ExpectedWindowDigest
        exit 2
    }
    $root = [System.Windows.Automation.AutomationElement]::FromHandle(
        $windowHandle
    )
    if ($null -eq $root) {
        Write-UiActionResult -Ok $false -ErrorCode 'ui_action_foreground_missing' `
            -Executed $false -WindowDigest $ExpectedWindowDigest
        exit 2
    }
    $windowTitle = Clean-UiActionText -Value $root.Current.Name -MaxChars 240
    $classBuffer = [System.Text.StringBuilder]::new(256)
    [void][EvelynUiActionNativeMethods]::GetClassName(
        $windowHandle,
        $classBuffer,
        $classBuffer.Capacity
    )
    $windowClass = Clean-UiActionText -Value $classBuffer.ToString() -MaxChars 80
    $windowDigest = Get-UiActionSha256 -Value (
        $windowTitle + [char]0x1f + $windowClass
    )
    if ($windowDigest -ne $ExpectedWindowDigest) {
        Write-UiActionResult -Ok $false `
            -ErrorCode 'ui_action_foreground_changed_since_preview' `
            -Executed $false -WindowDigest $ExpectedWindowDigest
        exit 3
    }

    $walker = [System.Windows.Automation.TreeWalker]::ControlViewWalker
    $queue = [System.Collections.Generic.Queue[object]]::new()
    $queue.Enqueue([PSCustomObject]@{ Element = $root; Depth = 0 })
    $visited = 0
    $matches = [System.Collections.Generic.List[object]]::new()
    while ($queue.Count -gt 0 -and $visited -lt 600) {
        $entry = $queue.Dequeue()
        $element = $entry.Element
        $depth = [int]$entry.Depth
        $visited += 1
        try {
            $current = $element.Current
            $controlType = Clean-UiActionText `
                -Value ([string]$current.ControlType.ProgrammaticName).Replace('ControlType.', '') `
                -MaxChars 40
            if ($controlType -eq 'Button' -and -not [bool]$current.IsOffscreen) {
                $runtimeId = @($element.GetRuntimeId()) -join '.'
                if (-not $runtimeId) {
                    $runtimeId = "$depth|$($current.ControlType.ProgrammaticName)|$($current.AutomationId)|$($current.Name)"
                }
                $runtimeId = Clean-UiActionText -Value $runtimeId -MaxChars 160
                $name = Clean-UiActionText -Value $current.Name -MaxChars 180
                $automationId = Clean-UiActionText `
                    -Value $current.AutomationId -MaxChars 120
                $material = $windowTitle + [char]0x1f + $windowClass +
                    [char]0x1f + $runtimeId + [char]0x1f + $controlType +
                    [char]0x1f + $automationId + [char]0x1f + $name
                $candidateId = (Get-UiActionSha256 -Value $material).Substring(0, 20)
                if ($candidateId -eq $ElementId) {
                    $matches.Add($element)
                }
            }
        } catch {
            # Dynamic UIA elements can disappear while walking.
        }
        if ($depth -ge 8) {
            continue
        }
        try {
            $child = $walker.GetFirstChild($element)
            while ($null -ne $child) {
                $queue.Enqueue(
                    [PSCustomObject]@{
                        Element = $child
                        Depth = $depth + 1
                    }
                )
                $child = $walker.GetNextSibling($child)
            }
        } catch {
            # Continue with already-enqueued elements.
        }
    }
    if ($matches.Count -eq 0) {
        Write-UiActionResult -Ok $false -ErrorCode 'ui_action_target_missing' `
            -Executed $false -WindowDigest $windowDigest
        exit 4
    }
    if ($matches.Count -ne 1) {
        Write-UiActionResult -Ok $false -ErrorCode 'ui_action_target_ambiguous' `
            -Executed $false -WindowDigest $windowDigest
        exit 5
    }
    $target = $matches[0]
    if (-not [bool]$target.Current.IsEnabled) {
        Write-UiActionResult -Ok $false -ErrorCode 'ui_action_target_disabled' `
            -Executed $false -WindowDigest $windowDigest
        exit 6
    }
    [object]$pattern = $null
    if (-not $target.TryGetCurrentPattern(
        [System.Windows.Automation.InvokePattern]::Pattern,
        [ref]$pattern
    )) {
        Write-UiActionResult -Ok $false `
            -ErrorCode 'ui_action_invoke_pattern_unavailable' `
            -Executed $false -WindowDigest $windowDigest
        exit 7
    }
    ([System.Windows.Automation.InvokePattern]$pattern).Invoke()
    Write-UiActionResult -Ok $true -ErrorCode '' -Executed $true `
        -WindowDigest $windowDigest
} catch {
    Write-UiActionResult -Ok $false -ErrorCode 'windows_ui_action_failed' `
        -Executed $false -WindowDigest $ExpectedWindowDigest
    exit 1
}
