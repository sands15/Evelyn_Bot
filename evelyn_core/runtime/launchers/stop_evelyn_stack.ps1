param(
    [int]$DelayMs = 3000
)

$ErrorActionPreference = 'Continue'

$projectRoot = Resolve-Path (Join-Path $PSScriptRoot '..\..\..')
$stopMarker = Join-Path $projectRoot '.evelyn_stop_requested'
try {
    Set-Content -LiteralPath $stopMarker -Value ("stop requested at " + (Get-Date).ToString('s')) -Encoding UTF8 -Force
} catch {
}

$targetPorts = @(3000, 8765, 8787, 8798, 8799, 8880, 8912, 9820, 9821, 9822)
$targetCommandFragments = @(
    'start_background_stack.ps1',
    'supervise_service.ps1',
    'start_main_llm.bat',
    'start_router_llm.bat',
    'start_sub_llm.bat',
    'run_main_llm.sh',
    'run_router_llm.sh',
    'run_sub_llm.sh',
    'start_tts.ps1',
    'start_tts.bat',
    'omnivoice_server.cli',
    'start_control_page.ps1',
    'start_bot.ps1',
    'start_bot.bat',
    '\\Evelyn\\main.py',
    'start_voyager_service.ps1',
    'start_voyager_service.bat',
    'start_voyager.bat',
    'start_codex_gateway.ps1',
    'start_codex_gateway.bat',
    'evelyn_core.voyager_service',
    'evelyn_core.codex_gateway_server',
    'evelyn_core.control_page_server',
    'upstream_voyager_runner.py',
    'voyager\\env\\mineflayer\\index.js',
    'http.server 8912'
)

$protectedPids = @{}
$protectedPids[[int]$PID] = $true
$ancestorPassthroughNames = @('powershell.exe', 'pwsh.exe', 'cmd.exe', 'py.exe', 'python.exe', 'wsl.exe', 'wslhost.exe', 'node.exe')

function Get-ListenOwnerPids {
    param([int[]]$Ports)

    $ids = New-Object 'System.Collections.Generic.HashSet[int]'
    foreach ($port in $Ports) {
        foreach ($row in @(Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue)) {
            if ($null -ne $row -and $row.OwningProcess) {
                $ids.Add([int]$row.OwningProcess) | Out-Null
            }
        }
    }
    return @($ids)
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

function Test-RelevantProcess {
    param(
        [int]$ProcessId,
        [hashtable]$ProcessTable,
        [string[]]$CommandFragments
    )

    if (-not $ProcessTable.ContainsKey($ProcessId)) {
        return $false
    }
    $proc = $ProcessTable[$ProcessId]
    $commandLine = [string]$proc.CommandLine
    foreach ($fragment in $CommandFragments) {
        if ($commandLine -like ('*' + $fragment + '*')) {
            return $true
        }
    }
    $name = ([string]$proc.Name).ToLowerInvariant()
    if ($ancestorPassthroughNames -contains $name) {
        return $true
    }
    return $false
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

    if ($ProcessId -le 0 -or $ProcessId -eq 4) {
        return
    }
    if ($protectedPids.ContainsKey($ProcessId)) {
        return
    }
    if (-not $ProcessTable.ContainsKey($ProcessId)) {
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

function Add-TargetAncestors {
    param(
        [int]$ProcessId,
        [hashtable]$ProcessTable,
        [hashtable]$ChildrenByParent,
        [hashtable]$Targets,
        [hashtable]$Visited,
        [string[]]$CommandFragments
    )

    $currentPid = $ProcessId
    for ($depth = 0; $depth -lt 6; $depth++) {
        if (-not $ProcessTable.ContainsKey($currentPid)) {
            break
        }
        $parentPid = [int]$ProcessTable[$currentPid].ParentProcessId
        if ($parentPid -le 0 -or $parentPid -eq 4) {
            break
        }
        if ($protectedPids.ContainsKey($parentPid)) {
            break
        }
        if (-not (Test-RelevantProcess -ProcessId $parentPid -ProcessTable $ProcessTable -CommandFragments $CommandFragments)) {
            break
        }
        Add-TargetPid -ProcessId $parentPid -Reason "ancestor-of:$currentPid" -ProcessTable $ProcessTable -ChildrenByParent $ChildrenByParent -Targets $Targets -Visited $Visited
        $currentPid = $parentPid
    }
}

function Collect-TargetProcesses {
    param(
        [int[]]$Ports,
        [string[]]$CommandFragments
    )

    $index = New-ProcessIndex
    $targets = @{}
    $visited = @{}

    foreach ($ownerPid in @(Get-ListenOwnerPids -Ports $Ports)) {
        Add-TargetPid -ProcessId ([int]$ownerPid) -Reason "listen-port" -ProcessTable $index.Table -ChildrenByParent $index.Children -Targets $targets -Visited $visited
        Add-TargetAncestors -ProcessId ([int]$ownerPid) -ProcessTable $index.Table -ChildrenByParent $index.Children -Targets $targets -Visited $visited -CommandFragments $CommandFragments
    }

    foreach ($row in $index.Table.Values) {
        $commandLine = [string]$row.CommandLine
        if ([string]::IsNullOrWhiteSpace($commandLine)) {
            continue
        }
        foreach ($fragment in $CommandFragments) {
            if ($commandLine -like ('*' + $fragment + '*')) {
                Add-TargetPid -ProcessId ([int]$row.ProcessId) -Reason ("command:" + $fragment) -ProcessTable $index.Table -ChildrenByParent $index.Children -Targets $targets -Visited $visited
                Add-TargetAncestors -ProcessId ([int]$row.ProcessId) -ProcessTable $index.Table -ChildrenByParent $index.Children -Targets $targets -Visited $visited -CommandFragments $CommandFragments
                break
            }
        }
    }

    return @{
        Index = $index
        Targets = $targets
    }
}

function Stop-TargetProcesses {
    param(
        [string]$Label,
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
    $rankedRows = foreach ($row in @($rows)) {
        $depth = 0
        $parentPid = [int]$row.ParentProcessId
        while ($parentPid -gt 0 -and $targetSet.ContainsKey($parentPid) -and $ProcessTable.ContainsKey($parentPid)) {
            $depth += 1
            $parentPid = [int]$ProcessTable[$parentPid].ParentProcessId
        }
        $row | Add-Member -NotePropertyName TargetAncestorDepth -NotePropertyValue $depth -Force -PassThru
    }

    foreach ($row in @($rankedRows | Sort-Object TargetAncestorDepth, ProcessId)) {
        try {
            Stop-Process -Id $row.ProcessId -Force -ErrorAction Stop
        } catch {
        }
    }

    Write-Host ("[stop_evelyn_stack] {0}: targeted {1} process(es)" -f $Label, @($rows).Count)
}

function Invoke-WslShutdown {
    try {
        & wsl.exe --shutdown | Out-Null
    } catch {
    }
}

if ($DelayMs -gt 0) {
    Start-Sleep -Milliseconds $DelayMs
}

$firstPass = Collect-TargetProcesses -Ports $targetPorts -CommandFragments $targetCommandFragments
Stop-TargetProcesses -Label 'pass-1' -ProcessTable $firstPass.Index.Table -Targets $firstPass.Targets
Invoke-WslShutdown
Start-Sleep -Milliseconds 750

$secondPass = Collect-TargetProcesses -Ports $targetPorts -CommandFragments $targetCommandFragments
Stop-TargetProcesses -Label 'pass-2' -ProcessTable $secondPass.Index.Table -Targets $secondPass.Targets
Invoke-WslShutdown
