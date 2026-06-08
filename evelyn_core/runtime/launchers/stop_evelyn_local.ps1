param(
    [int]$DelayMs = 500,
    [switch]$DryRun
)

$ErrorActionPreference = 'Continue'

$projectRoot = Resolve-Path (Join-Path $PSScriptRoot '..\..\..')
$stopMarker = Join-Path $projectRoot '.evelyn_stop_requested'
$targetPorts = @(8799, 8880, 8891, 9820, 9821, 9822)
$evelynCommandFragments = @(
    'C:\Evelyn',
    'C:/Evelyn',
    '/mnt/c/Evelyn',
    '\\Evelyn\\main.py',
    'start_local.bat',
    'start_main_llm.bat',
    'start_router_llm.bat',
    'start_sub_llm.bat',
    'run_main_llm.sh',
    'run_router_llm.sh',
    'run_sub_llm.sh',
    'start_tts.ps1',
    'start_tts.bat',
    'start_vision.ps1',
    'start_vision.bat',
    'evelyn_core.vision_service',
    'evelyn_core.control_page_server'
)
$protectedFragments = @(
    '\.openclaw\',
    '/.openclaw/',
    'openclaw',
    'codex-home',
    '\acpx\',
    '/acpx/',
    'embedded app-server',
    'app-server',
    'stop_evelyn_stack.ps1',
    'stop_evelyn_local.ps1',
    'stop_local.bat'
)
$wslKillPatterns = @(
    '/mnt/c/Evelyn/evelyn_core/runtime/launchers/[r]un_main_llm.sh',
    '/mnt/c/Evelyn/evelyn_core/runtime/launchers/[r]un_router_llm.sh',
    '/mnt/c/Evelyn/evelyn_core/runtime/launchers/[r]un_sub_llm.sh',
    '[e]velyn_core.vision_service'
)
$protectedPids = @{}
$stopToolFragments = @(
    '\.openclaw\',
    '/.openclaw/',
    'openclaw',
    'codex-home',
    '\acpx\',
    '/acpx/',
    'embedded app-server',
    'app-server',
    'stop_evelyn_stack.ps1',
    'stop_evelyn_local.ps1',
    'stop_local.bat'
)

$currentPidForProtection = [int]$PID
for ($depth = 0; $depth -lt 8; $depth++) {
    if ($currentPidForProtection -le 0 -or $currentPidForProtection -eq 4) {
        break
    }
    $currentProcForProtection = Get-CimInstance Win32_Process -Filter ("ProcessId = " + $currentPidForProtection) -ErrorAction SilentlyContinue
    if ($null -eq $currentProcForProtection) {
        break
    }
    $commandLineForProtection = [string]$currentProcForProtection.CommandLine
    $protectThisProcess = ($depth -eq 0)
    if (-not $protectThisProcess) {
        foreach ($fragment in $stopToolFragments) {
            if ($commandLineForProtection -like ('*' + $fragment + '*')) {
                $protectThisProcess = $true
                break
            }
        }
    }
    if (-not $protectThisProcess) {
        break
    }
    $protectedPids[$currentPidForProtection] = $true
    $currentPidForProtection = [int]$currentProcForProtection.ParentProcessId
}

function Test-ContainsAnyFragment {
    param(
        [string]$Value,
        [string[]]$Fragments
    )

    if ([string]::IsNullOrWhiteSpace($Value)) {
        return $false
    }
    foreach ($fragment in $Fragments) {
        if ($Value -like ('*' + $fragment + '*')) {
            return $true
        }
    }
    return $false
}

function Test-ProtectedCommandLine {
    param([string]$CommandLine)
    return (Test-ContainsAnyFragment -Value $CommandLine -Fragments $protectedFragments)
}

function Test-EvelynCommandLine {
    param([string]$CommandLine)
    if (Test-ProtectedCommandLine -CommandLine $CommandLine) {
        return $false
    }
    return (Test-ContainsAnyFragment -Value $CommandLine -Fragments $evelynCommandFragments)
}

function Test-KnownPortOwner {
    param(
        [int]$Port,
        [string]$CommandLine
    )

    if (Test-ProtectedCommandLine -CommandLine $CommandLine) {
        return $false
    }
    if (Test-EvelynCommandLine -CommandLine $CommandLine) {
        return $true
    }
    if ($Port -eq 8880 -and $CommandLine -like '*omnivoice*') {
        return $true
    }
    if ($Port -eq 8891 -and $CommandLine -like '*evelyn_core.vision_service*') {
        return $true
    }
    return $false
}

function New-ProcessIndex {
    $table = @{}
    $children = @{}
    foreach ($row in @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)) {
        $procId = [int]$row.ProcessId
        $parentPid = [int]$row.ParentProcessId
        $table[$procId] = $row
        if (-not $children.ContainsKey($parentPid)) {
            $children[$parentPid] = New-Object 'System.Collections.Generic.List[int]'
        }
        $children[$parentPid].Add($procId)
    }
    return @{
        Table = $table
        Children = $children
    }
}

function Add-TargetPid {
    param(
        [int]$ProcessId,
        [string]$Reason,
        [hashtable]$ProcessTable,
        [hashtable]$ChildrenByParent,
        [hashtable]$Targets,
        [hashtable]$Visited
    )

    if ($ProcessId -le 0 -or $ProcessId -eq 4 -or $protectedPids.ContainsKey($ProcessId)) {
        return
    }
    if (-not $ProcessTable.ContainsKey($ProcessId)) {
        return
    }
    $proc = $ProcessTable[$ProcessId]
    $commandLine = [string]$proc.CommandLine
    if (Test-ProtectedCommandLine -CommandLine $commandLine) {
        return
    }
    if (-not $Targets.ContainsKey($ProcessId)) {
        $Targets[$ProcessId] = New-Object 'System.Collections.Generic.List[string]'
    }
    $Targets[$ProcessId].Add($Reason)
    if ($Visited.ContainsKey($ProcessId)) {
        return
    }
    $Visited[$ProcessId] = $true
    if ($ChildrenByParent.ContainsKey($ProcessId)) {
        foreach ($childPid in $ChildrenByParent[$ProcessId]) {
            Add-TargetPid -ProcessId ([int]$childPid) -Reason "child-of:$ProcessId" -ProcessTable $ProcessTable -ChildrenByParent $ChildrenByParent -Targets $Targets -Visited $Visited
        }
    }
}

function Add-EvelynAncestors {
    param(
        [int]$ProcessId,
        [hashtable]$ProcessTable,
        [hashtable]$ChildrenByParent,
        [hashtable]$Targets,
        [hashtable]$Visited
    )

    $currentPid = $ProcessId
    for ($depth = 0; $depth -lt 5; $depth++) {
        if (-not $ProcessTable.ContainsKey($currentPid)) {
            break
        }
        $parentPid = [int]$ProcessTable[$currentPid].ParentProcessId
        if ($parentPid -le 0 -or $parentPid -eq 4 -or $protectedPids.ContainsKey($parentPid)) {
            break
        }
        if (-not $ProcessTable.ContainsKey($parentPid)) {
            break
        }
        $parentCommandLine = [string]$ProcessTable[$parentPid].CommandLine
        if (-not (Test-EvelynCommandLine -CommandLine $parentCommandLine)) {
            break
        }
        Add-TargetPid -ProcessId $parentPid -Reason "evelyn-ancestor-of:$currentPid" -ProcessTable $ProcessTable -ChildrenByParent $ChildrenByParent -Targets $Targets -Visited $Visited
        $currentPid = $parentPid
    }
}

function Collect-Targets {
    $index = New-ProcessIndex
    $targets = @{}
    $visited = @{}

    foreach ($row in $index.Table.Values) {
        $commandLine = [string]$row.CommandLine
        if (Test-EvelynCommandLine -CommandLine $commandLine) {
            Add-TargetPid -ProcessId ([int]$row.ProcessId) -Reason 'command-line:evelyn' -ProcessTable $index.Table -ChildrenByParent $index.Children -Targets $targets -Visited $visited
            Add-EvelynAncestors -ProcessId ([int]$row.ProcessId) -ProcessTable $index.Table -ChildrenByParent $index.Children -Targets $targets -Visited $visited
        }
    }

    foreach ($port in $targetPorts) {
        foreach ($conn in @(Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue)) {
            $ownerPid = [int]$conn.OwningProcess
            if (-not $index.Table.ContainsKey($ownerPid)) {
                continue
            }
            $commandLine = [string]$index.Table[$ownerPid].CommandLine
            if (Test-KnownPortOwner -Port $port -CommandLine $commandLine) {
                Add-TargetPid -ProcessId $ownerPid -Reason "listen-port:$port" -ProcessTable $index.Table -ChildrenByParent $index.Children -Targets $targets -Visited $visited
                Add-EvelynAncestors -ProcessId $ownerPid -ProcessTable $index.Table -ChildrenByParent $index.Children -Targets $targets -Visited $visited
            } else {
                Write-Host ("[stop_evelyn_local] skip port {0} owner pid={1}; command line did not prove Evelyn ownership" -f $port, $ownerPid)
            }
        }
    }

    return @{
        Index = $index
        Targets = $targets
    }
}

function Get-TargetRows {
    param(
        [hashtable]$ProcessTable,
        [hashtable]$Targets
    )

    $rows = foreach ($entry in $Targets.GetEnumerator()) {
        $procId = [int]$entry.Key
        if (-not $ProcessTable.ContainsKey($procId)) {
            continue
        }
        $proc = $ProcessTable[$procId]
        [PSCustomObject]@{
            ProcessId = $procId
            ParentProcessId = [int]$proc.ParentProcessId
            Name = [string]$proc.Name
            Reasons = (($entry.Value | Select-Object -Unique) -join '; ')
            CommandLine = [string]$proc.CommandLine
        }
    }

    $targetSet = @{}
    foreach ($row in @($rows)) {
        $targetSet[[int]$row.ProcessId] = $true
    }
    foreach ($row in @($rows)) {
        $depth = 0
        $parentPid = [int]$row.ParentProcessId
        while ($parentPid -gt 0 -and $targetSet.ContainsKey($parentPid) -and $ProcessTable.ContainsKey($parentPid)) {
            $depth += 1
            $parentPid = [int]$ProcessTable[$parentPid].ParentProcessId
        }
        $row | Add-Member -NotePropertyName TargetAncestorDepth -NotePropertyValue $depth -Force -PassThru
    }
}

function Invoke-EvelynWslKill {
    param([switch]$DryRun)

    foreach ($pattern in $wslKillPatterns) {
        if ($DryRun) {
            Write-Host ("[stop_evelyn_local] dry-run: wsl pkill -f {0}" -f $pattern)
            continue
        }
        try {
            & wsl.exe bash -lc ("pkill -f " + "'" + $pattern.Replace("'", "'\''") + "'") 2>$null | Out-Null
        } catch {
        }
    }
}

if ($DelayMs -gt 0) {
    Start-Sleep -Milliseconds $DelayMs
}

if (-not $DryRun) {
    try {
        Set-Content -LiteralPath $stopMarker -Value ("local stop requested at " + (Get-Date).ToString('s')) -Encoding UTF8 -Force
    } catch {
    }
}

$collected = Collect-Targets
$rows = @(Get-TargetRows -ProcessTable $collected.Index.Table -Targets $collected.Targets)

if ($DryRun) {
    Write-Host ("[stop_evelyn_local] dry-run: {0} Windows process(es) would be stopped" -f $rows.Count)
    foreach ($row in @($rows | Sort-Object @{ Expression = 'TargetAncestorDepth'; Descending = $true }, ProcessId)) {
        Write-Host ("[stop_evelyn_local] dry-run pid={0} name={1} reasons={2}" -f $row.ProcessId, $row.Name, $row.Reasons)
        if (-not [string]::IsNullOrWhiteSpace($row.CommandLine)) {
            Write-Host ("  " + $row.CommandLine)
        }
    }
} else {
    foreach ($row in @($rows | Sort-Object @{ Expression = 'TargetAncestorDepth'; Descending = $true }, ProcessId)) {
        try {
            Stop-Process -Id $row.ProcessId -Force -ErrorAction Stop
            Write-Host ("[stop_evelyn_local] stopped pid={0} name={1} reasons={2}" -f $row.ProcessId, $row.Name, $row.Reasons)
        } catch {
            Write-Host ("[stop_evelyn_local] failed pid={0} name={1}: {2}" -f $row.ProcessId, $row.Name, $_.Exception.Message)
        }
    }
}

Invoke-EvelynWslKill -DryRun:$DryRun

Start-Sleep -Milliseconds 500
foreach ($port in $targetPorts) {
    $listeners = @(Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue)
    if ($listeners.Count -eq 0) {
        Write-Host ("[stop_evelyn_local] port {0}: NO_LISTENERS" -f $port)
    } else {
        $owners = (($listeners | Select-Object -ExpandProperty OwningProcess -Unique) -join ',')
        Write-Host ("[stop_evelyn_local] port {0}: still listening pid(s) {1}" -f $port, $owners)
    }
}
