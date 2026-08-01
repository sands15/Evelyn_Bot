Set-StrictMode -Version Latest

function Resolve-EvelynSourceRevision {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ProjectRoot,
        [string]$RequestedRevision = ''
    )

    $revision = ([string]$RequestedRevision).Trim().ToLowerInvariant()
    if ($revision) {
        if ($revision -notmatch '^(?:[0-9a-f]{40}|[0-9a-f]{64})$') {
            throw 'EVELYN_SOURCE_REVISION must be an exact 40- or 64-character hexadecimal revision.'
        }
    }

    $gitCommand = Get-Command git.exe -ErrorAction SilentlyContinue
    if (-not $gitCommand) {
        $gitCommand = Get-Command git -ErrorAction SilentlyContinue
    }
    if (-not $gitCommand) {
        if ($revision) {
            return $revision
        }
        throw 'Cannot prove the runtime source revision because git is unavailable. Set EVELYN_SOURCE_REVISION explicitly.'
    }

    $statusOutput = @(& $gitCommand.Source -C $ProjectRoot status --porcelain --untracked-files=all 2>&1)
    if ($LASTEXITCODE -ne 0) {
        throw 'Cannot inspect the Evelyn source tree before runtime launch.'
    }
    if ($statusOutput.Count -gt 0) {
        throw 'Refusing to launch revision-gated containers from a dirty source tree. Commit or stash source changes first.'
    }

    $headOutput = @(& $gitCommand.Source -C $ProjectRoot rev-parse HEAD 2>&1)
    if ($LASTEXITCODE -ne 0 -or $headOutput.Count -ne 1) {
        throw 'Cannot resolve the Evelyn source revision before runtime launch.'
    }
    $headRevision = ([string]$headOutput[0]).Trim().ToLowerInvariant()
    if ($headRevision -notmatch '^(?:[0-9a-f]{40}|[0-9a-f]{64})$') {
        throw 'The resolved Evelyn source revision is not an exact Git/SHA revision.'
    }
    if ($revision -and $revision -ne $headRevision) {
        throw 'EVELYN_SOURCE_REVISION does not match the checked-out Evelyn source revision.'
    }
    return $headRevision
}

function Initialize-EvelynSourceRevision {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ProjectRoot
    )

    $revision = Resolve-EvelynSourceRevision `
        -ProjectRoot $ProjectRoot `
        -RequestedRevision ([string]$env:EVELYN_SOURCE_REVISION)
    $env:EVELYN_SOURCE_REVISION = $revision
    return $revision
}
