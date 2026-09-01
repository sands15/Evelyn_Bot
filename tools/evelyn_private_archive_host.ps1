Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$launcher = Join-Path `
    (Split-Path -Parent $PSScriptRoot) `
    'scripts\Start-EvelynConversationArchiveAdmin.ps1'
& $launcher @args
exit $LASTEXITCODE
