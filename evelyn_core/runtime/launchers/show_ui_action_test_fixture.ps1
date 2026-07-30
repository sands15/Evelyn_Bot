param(
    [string]$ProjectRoot = (Join-Path $PSScriptRoot '..\..\..')
)

$ErrorActionPreference = 'Stop'

if (-not $IsWindows -and $PSVersionTable.PSEdition -eq 'Core') {
    throw 'The UI action test fixture requires Windows.'
}
if (
    [System.Threading.Thread]::CurrentThread.GetApartmentState() -ne
    [System.Threading.ApartmentState]::STA
) {
    throw (
        'The UI action test fixture requires an STA shell. Run: ' +
        'powershell.exe -NoProfile -STA -File ' +
        '.\evelyn_core\runtime\launchers\show_ui_action_test_fixture.ps1'
    )
}

$resolvedProjectRoot = [System.IO.Path]::GetFullPath(
    [string](Resolve-Path -LiteralPath $ProjectRoot -ErrorAction Stop)
).TrimEnd('\')
$artifactRoot = Join-Path $resolvedProjectRoot (
    'runtime_artifacts\ui_action_fixture'
)
$statusPath = Join-Path $artifactRoot 'status.json'
New-Item -ItemType Directory -Force -Path $artifactRoot | Out-Null

function Write-UiActionFixtureStatus {
    param(
        [ValidateSet('ready', 'invoked', 'closed')]
        [string]$State,
        [bool]$ButtonEnabled
    )

    $payload = [ordered]@{
        schema = 'ui_action.fixture-status.v1'
        state = $State
        updatedAt = (
            [double][DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() /
            1000.0
        )
        buttonEnabled = $ButtonEnabled
        expectedPostcondition = 'target_disabled'
        reversible = $true
        storesTargetText = $false
    }
    $temporaryPath = Join-Path $artifactRoot (
        '.status-' + [Guid]::NewGuid().ToString('N') + '.tmp'
    )
    try {
        $json = $payload | ConvertTo-Json -Depth 3
        [System.IO.File]::WriteAllText(
            $temporaryPath,
            $json,
            [System.Text.UTF8Encoding]::new($false)
        )
        Move-Item -LiteralPath $temporaryPath -Destination $statusPath -Force
    } finally {
        if (Test-Path -LiteralPath $temporaryPath -PathType Leaf) {
            Remove-Item -LiteralPath $temporaryPath -Force
        }
    }
}

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()

$form = [System.Windows.Forms.Form]::new()
$form.Text = 'Evelyn UI Action Test Fixture'
$form.Name = 'evelynUiActionTestFixture'
$form.AccessibleName = 'Evelyn UI Action Test Fixture'
$form.StartPosition = (
    [System.Windows.Forms.FormStartPosition]::CenterScreen
)
$form.ClientSize = [System.Drawing.Size]::new(520, 235)
$form.MinimumSize = [System.Drawing.Size]::new(520, 274)

$heading = [System.Windows.Forms.Label]::new()
$heading.Text = 'Safe UIA Invoke Fixture'
$heading.Font = [System.Drawing.Font]::new(
    'Segoe UI',
    15,
    [System.Drawing.FontStyle]::Bold
)
$heading.AutoSize = $true
$heading.Location = [System.Drawing.Point]::new(24, 22)

$instructions = [System.Windows.Forms.Label]::new()
$instructions.Text = (
    'This window exposes one reversible target Button. ' +
    'A verified invoke disables it; Reset enables it again.'
)
$instructions.AutoSize = $false
$instructions.Size = [System.Drawing.Size]::new(465, 44)
$instructions.Location = [System.Drawing.Point]::new(27, 62)

$stateLabel = [System.Windows.Forms.Label]::new()
$stateLabel.Text = 'State: ready'
$stateLabel.AutoSize = $true
$stateLabel.Location = [System.Drawing.Point]::new(27, 112)

$invokeButton = [System.Windows.Forms.Button]::new()
$invokeButton.Name = 'evelynSafeInvokeButton'
$invokeButton.AccessibleName = 'Evelyn Safe Invoke Test'
$invokeButton.Text = 'Evelyn Safe Invoke Test'
$invokeButton.Size = [System.Drawing.Size]::new(270, 42)
$invokeButton.Location = [System.Drawing.Point]::new(27, 148)

$resetLink = [System.Windows.Forms.LinkLabel]::new()
$resetLink.Name = 'evelynSafeInvokeReset'
$resetLink.AccessibleName = 'Reset Safe Invoke Test'
$resetLink.Text = 'Reset manually'
$resetLink.AutoSize = $true
$resetLink.Location = [System.Drawing.Point]::new(316, 161)

$invokeButton.Add_Click({
    $invokeButton.Enabled = $false
    $stateLabel.Text = 'State: invoked and disabled'
    Write-UiActionFixtureStatus -State 'invoked' -ButtonEnabled $false
})
$resetLink.Add_LinkClicked({
    $invokeButton.Enabled = $true
    $stateLabel.Text = 'State: ready'
    Write-UiActionFixtureStatus -State 'ready' -ButtonEnabled $true
})
$form.Add_Shown({
    Write-UiActionFixtureStatus -State 'ready' -ButtonEnabled $true
    [void]$form.Activate()
})
$form.Add_FormClosed({
    Write-UiActionFixtureStatus -State 'closed' -ButtonEnabled $false
})

$form.Controls.AddRange(
    @(
        $heading,
        $instructions,
        $stateLabel,
        $invokeButton,
        $resetLink
    )
)
[void][System.Windows.Forms.Application]::Run($form)
