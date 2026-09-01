Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$provisioner = Join-Path `
    (Split-Path -Parent $PSScriptRoot) `
    'scripts\Initialize-EvelynConversationArchiveTest.ps1'
$pwsh = (Get-Command pwsh.exe -ErrorAction Stop).Source
& $pwsh `
    -NoProfile `
    -ExecutionPolicy Bypass `
    -File $provisioner `
    @args
exit $LASTEXITCODE
