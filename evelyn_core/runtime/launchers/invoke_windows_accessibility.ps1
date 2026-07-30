param(
    [ValidateRange(1, 160)]
    [int]$MaxElements = 120
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

function Clean-AccessibilityText {
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

function Write-AccessibilityResult {
    param(
        [bool]$Ok,
        [string]$ErrorCode,
        [bool]$Available,
        [string]$WindowTitle,
        [string]$WindowClass,
        [bool]$Truncated,
        [object[]]$Elements
    )

    [PSCustomObject]@{
        schema = 'windows_accessibility.result.v1'
        ok = $Ok
        errorCode = $ErrorCode
        capturedAt = [double]([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()) / 1000.0
        available = $Available
        windowTitle = $WindowTitle
        windowClass = $WindowClass
        truncated = $Truncated
        elements = @($Elements)
    } | ConvertTo-Json -Depth 6 -Compress
}

try {
    Add-Type -AssemblyName UIAutomationClient
    Add-Type -AssemblyName UIAutomationTypes
    if (-not ('EvelynAccessibilityNativeMethods' -as [type])) {
        Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using System.Text;

public static class EvelynAccessibilityNativeMethods
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

    $windowHandle = [EvelynAccessibilityNativeMethods]::GetForegroundWindow()
    if ($windowHandle -eq [IntPtr]::Zero) {
        Write-AccessibilityResult -Ok $true -ErrorCode '' -Available $false `
            -WindowTitle '' -WindowClass '' -Truncated $false -Elements @()
        exit 0
    }

    $root = [System.Windows.Automation.AutomationElement]::FromHandle(
        $windowHandle
    )
    if ($null -eq $root) {
        Write-AccessibilityResult -Ok $true -ErrorCode '' -Available $false `
            -WindowTitle '' -WindowClass '' -Truncated $false -Elements @()
        exit 0
    }

    $windowTitle = Clean-AccessibilityText -Value $root.Current.Name -MaxChars 240
    $classBuffer = [System.Text.StringBuilder]::new(256)
    [void][EvelynAccessibilityNativeMethods]::GetClassName(
        $windowHandle,
        $classBuffer,
        $classBuffer.Capacity
    )
    $windowClass = Clean-AccessibilityText -Value $classBuffer.ToString() -MaxChars 80

    $allowedTypes = @{
        Window = $true
        TitleBar = $true
        Button = $true
        MenuBar = $true
        Menu = $true
        MenuItem = $true
        ToolBar = $true
        Tab = $true
        TabItem = $true
        Text = $true
        Hyperlink = $true
        CheckBox = $true
        RadioButton = $true
        ComboBox = $true
        List = $true
        ListItem = $true
        Tree = $true
        TreeItem = $true
        DataGrid = $true
        DataItem = $true
        Header = $true
        HeaderItem = $true
        StatusBar = $true
    }
    $walker = [System.Windows.Automation.TreeWalker]::ControlViewWalker
    $queue = [System.Collections.Generic.Queue[object]]::new()
    $queue.Enqueue([PSCustomObject]@{ Element = $root; Depth = 0 })
    $seen = @{}
    $elements = [System.Collections.Generic.List[object]]::new()
    $visited = 0
    $truncated = $false

    while ($queue.Count -gt 0 -and $visited -lt 600) {
        $entry = $queue.Dequeue()
        $element = $entry.Element
        $depth = [int]$entry.Depth
        $visited += 1

        try {
            $current = $element.Current
            $runtimeId = @($element.GetRuntimeId()) -join '.'
            if (-not $runtimeId) {
                $runtimeId = "$depth|$($current.ControlType.ProgrammaticName)|$($current.AutomationId)|$($current.Name)"
            }
            if (-not $seen.ContainsKey($runtimeId)) {
                $seen[$runtimeId] = $true
                $controlType = Clean-AccessibilityText `
                    -Value ([string]$current.ControlType.ProgrammaticName).Replace('ControlType.', '') `
                    -MaxChars 40
                $name = Clean-AccessibilityText -Value $current.Name -MaxChars 180
                $automationId = Clean-AccessibilityText `
                    -Value $current.AutomationId -MaxChars 120
                $visible = -not [bool]$current.IsOffscreen
                if (
                    $visible -and
                    $allowedTypes.ContainsKey($controlType) -and
                    ($name -or $automationId -or $controlType -eq 'Window')
                ) {
                    $bounds = $current.BoundingRectangle
                    $elements.Add([PSCustomObject]@{
                        runtimeId = $runtimeId
                        name = $name
                        automationId = $automationId
                        controlType = $controlType
                        isEnabled = [bool]$current.IsEnabled
                        bounds = [PSCustomObject]@{
                            x = [math]::Round([double]$bounds.X, 1)
                            y = [math]::Round([double]$bounds.Y, 1)
                            width = [math]::Round([double]$bounds.Width, 1)
                            height = [math]::Round([double]$bounds.Height, 1)
                        }
                    })
                    if ($elements.Count -ge $MaxElements) {
                        $truncated = $queue.Count -gt 0
                        break
                    }
                }
            }
        } catch {
            # An element can disappear while the foreground app updates.
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
    if ($visited -ge 600 -and $queue.Count -gt 0) {
        $truncated = $true
    }

    Write-AccessibilityResult -Ok $true -ErrorCode '' `
        -Available ([bool]($windowTitle -or $windowClass -or $elements.Count)) `
        -WindowTitle $windowTitle -WindowClass $windowClass `
        -Truncated $truncated -Elements $elements.ToArray()
} catch {
    Write-AccessibilityResult -Ok $false `
        -ErrorCode 'windows_accessibility_failed' -Available $false `
        -WindowTitle '' -WindowClass '' -Truncated $false -Elements @()
    exit 1
}
