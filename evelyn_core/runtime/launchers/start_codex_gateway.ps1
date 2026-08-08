$ErrorActionPreference = 'Stop'
$host.UI.RawUI.WindowTitle = 'Codex-Gateway | Docker isolated runtime'

& (Join-Path $PSScriptRoot 'start_docker_compose_services.ps1') `
    -Profiles codex-gateway `
    -Services codex_gateway
