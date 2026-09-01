from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import unittest
from pathlib import Path


REPO_ROOT = next(
    path for path in Path(__file__).resolve().parents if (path / "main.py").exists()
)
LAUNCHER = REPO_ROOT / "tools" / "run_discord_feedback_live_validation.ps1"
ENTRYPOINT = REPO_ROOT / "docker" / "discord_token_stdin_entrypoint.py"
COMMAND_GUARD = REPO_ROOT / "docker" / "discord_command_registry_guard.py"
DOCKERIGNORE = REPO_ROOT / ".dockerignore"
DISCORD_DOCKERFILE = REPO_ROOT / "docker" / "Dockerfile.discord-bot"


class DiscordFeedbackLiveValidationLauncherTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = LAUNCHER.read_text(encoding="utf-8")
        cls.entrypoint = ENTRYPOINT.read_text(encoding="utf-8")
        cls.command_guard = COMMAND_GUARD.read_text(encoding="utf-8")
        cls.dockerignore = DOCKERIGNORE.read_text(encoding="utf-8")
        cls.discord_dockerfile = DISCORD_DOCKERFILE.read_text(encoding="utf-8")

    def test_builds_exact_dirty_snapshot_without_weakening_production_gate(self) -> None:
        source = self.source
        self.assertTrue(source.startswith("#Requires -Version 7.2\n"))
        self.assertIn("docker/Dockerfile.bot-api", source)
        self.assertIn("docker/Dockerfile.control-page", source)
        self.assertIn("docker/Dockerfile.discord-bot", source)
        self.assertIn("$placeholderRevision = '0' * 64", source)
        self.assertIn("Get-ImageSourceDigest", source)
        self.assertIn("current_source_snapshot_raced", source)
        self.assertIn("promoted_source_manifest_mismatch", source)
        self.assertIn("EVELYN_IMAGE_SOURCE_REVISION=${EVELYN_SOURCE_REVISION}", source)
        self.assertIn("ai.evelyn.source-digest", source)
        self.assertIn("Export-ExactSourceSnapshot", source)
        self.assertIn("--pull=false", source)
        self.assertIn("--no-cache", source)

        self.assertNotIn("source_revision.ps1", source)
        self.assertNotIn("Resolve-EvelynSourceRevision", source)
        self.assertNotRegex(source, r"(?i)git\s+(?:commit|stash|reset|checkout|switch)")
        self.assertNotIn("docker compose down", source.lower())
        self.assertNotIn("prune", source.lower())

    def test_dpapi_secret_is_stdin_only_and_compose_never_creates_discord(self) -> None:
        source = self.source
        self.assertIn("tools\\discord_capture_credential.psm1", source)
        self.assertIn("Read-EvelynDiscordTokenCache", source)
        self.assertIn("Assert-EvelynDiscordTokenBytes", source)
        self.assertIn("[byte[]]$tokenBytes", source)
        self.assertIn("$process.StandardInput.BaseStream", source)
        self.assertIn("$stream.Write($TokenBytes", source)
        self.assertIn("$stream.WriteByte(10)", source)
        self.assertGreaterEqual(source.count("$stream.Write($TokenBytes"), 2)
        self.assertIn("[Array]::Clear($tokenBytes", source)
        self.assertIn("'start', '--attach', '--interactive', $discordContainerId", source)
        self.assertIn("$null = $startInfo.Environment.Remove('DISCORD_BOT_TOKEN')", source)
        self.assertIn("$discordInterpolationPlaceholder = 'validation-placeholder'", source)
        self.assertIn("DISCORD_BOT_TOKEN = $discordInterpolationPlaceholder", source)
        self.assertNotRegex(
            source,
            r"'--env'\s*,\s*['\"]DISCORD_BOT_TOKEN(?:=|['\"])",
        )
        self.assertNotIn("--profile', 'discord", source)
        self.assertNotRegex(source, r"['\"]up['\"].{0,120}discord_bot")

        self.assertIn("'create', '--interactive'", source)
        self.assertIn("--log-driver', 'none'", source)
        self.assertIn("--read-only", source)
        self.assertIn("--cap-drop', 'ALL'", source)
        self.assertIn("'/app/docker/discord_token_stdin_entrypoint.py'", source)
        self.assertIn("discord_container_secret_contract_invalid", source)
        self.assertIn("command_guard_container_contract_invalid", source)
        self.assertIn("Where-Object { $_ -like 'DISCORD_BOT_TOKEN=*' }", source)

        self.assertIn("!docker/**", self.dockerignore)
        self.assertIn("COPY . /app", self.discord_dockerfile)
        self.assertTrue(ENTRYPOINT.is_file())
        self.assertTrue(COMMAND_GUARD.is_file())
        self.assertNotIn('os.environ[TOKEN_ENV] =', self.entrypoint)
        self.assertIn('if "DISCORD_BOT_TOKEN" in os.environ:', self.command_guard)

    def test_current_source_and_runtime_writes_are_isolated_from_project_state(self) -> None:
        source = self.source
        for variable in (
            "$scratchRuntimeRoot",
            "$scratchBotMemoryRoot",
            "$scratchBotProfilesRoot",
            "$scratchGuildSettingsRoot",
            "$scratchLogsRoot",
        ):
            self.assertIn(variable, source)
        self.assertIn("'--artifacts-root', $scratchRuntimeRoot", source)
        self.assertIn(
            "$startInfo.Environment['EVELYN_RUNTIME_ARTIFACTS_DIR'] = $scratchRuntimeRoot",
            source,
        )
        for target in ("external", "assets", "docs"):
            self.assertIn(f'${{source{target.title()}}}:/app/{target}:ro', source)
            self.assertIn(
                f"source=$(Join-Path $sourceRoot '{target}'),target=/app/{target},readonly",
                source,
            )
            self.assertNotIn(
                f"source=$(Join-Path $projectRoot '{target}'),target=/app/{target}",
                source,
            )
        for target in (
            "bot_memory",
            "bot_profiles",
            "guild_settings",
            "runtime_artifacts",
            "logs",
        ):
            self.assertNotIn(f"./{target}:/app/{target}", source)
        self.assertGreaterEqual(source.count("/app/runtime_artifacts"), 4)
        self.assertIn("Join-Path $scratchRuntimeRoot 'voice_input_lease\\owner.json'", source)
        self.assertIn("Assert-ComposeBind", source)
        self.assertIn("Assert-ContainerBind", source)
        self.assertIn("compose_runtime_isolation_invalid", source)
        self.assertIn("container_runtime_isolation_invalid", source)
        self.assertIn("public_result_artifact_isolation_failed", source)
        self.assertIn("sourceDigest = $sourceDigest", source)

    def test_command_registry_cleanup_and_operator_receipt_are_fail_closed(self) -> None:
        source = self.source
        self.assertIn("'/app/docker/discord_command_registry_guard.py'", source)
        self.assertIn("Wait-CommandGuardState -ExpectedState 'baseline_ready'", source)
        self.assertIn("Wait-CommandGuardState -ExpectedState 'published_ready'", source)
        self.assertIn("$marker.state -ceq 'restored'", source)
        self.assertIn("Request-CommandGuardCleanup", source)
        self.assertIn("command_registry_cleanup_failed", source)
        self.assertNotIn("Start-Sleep -Seconds 10", source)
        self.assertIn("[Console]::In.ReadLineAsync()", source)
        self.assertIn("$receipt -ceq 'PASS'", source)
        self.assertIn("$receipt -ceq 'FAIL'", source)
        self.assertIn("operatorConfirmed = $operatorConfirmed", source)
        self.assertIn("'window_closed_unverified'", source)
        self.assertNotIn("Read-Host", source)
        self.assertIn("'stop', '--timeout', '30', $discordContainerId", source)
        self.assertNotIn("'stop', '--time', '30', $discordContainerId", source)
        self.assertIn("$stopped.State.Running -ne $false", source)

        stop_discord = source.index("try { Stop-Discord }", source.index("} finally {", source.index("$runFailure")))
        restore_commands = source.index("try { Request-CommandGuardCleanup }", stop_discord)
        remove_resources = source.index("try { Remove-OwnedDockerResources }", restore_commands)
        self.assertLess(stop_discord, restore_commands)
        self.assertLess(restore_commands, remove_resources)

        guard = self.command_guard
        self.assertIn("guard_baseline_managed_commands_present", guard)
        self.assertIn("guard_registry_foreign_drift", guard)
        self.assertIn("delete_guild_command", guard)
        self.assertIn("OWNERSHIP_SCHEMA", guard)
        self.assertIn("self._read_ownership()", guard)
        self.assertIn("command_shape(command) not in expected", guard)
        self.assertIn("canonical(final_guild) != canonical(self.baseline_guild)", guard)
        self.assertNotIn("tokenPath", guard)
        self.assertNotIn("registrySnapshot", guard)
        self.assertIn("ownershipPath = '/run/evelyn-command-guard/ownership.json'", source)
        self.assertIn("runId = $runId", source)
        self.assertIn(
            "EVELYN_CONVERSATION_ARCHIVE_COMMAND_OWNERSHIP_LEDGER=/run/evelyn-command-guard/ownership.json",
            source,
        )
        self.assertIn("EVELYN_CONVERSATION_ARCHIVE_COMMAND_RUN_ID=$runId", source)
        self.assertGreaterEqual(
            source.count("target=/run/evelyn-command-guard"), 2
        )
        clear_run_artifacts = source[
            source.index("function Clear-OwnedRunArtifacts") : source.index(
                "if (-not $RunLive)"
            )
        ]
        self.assertLess(
            clear_run_artifacts.index("if ($cleanupFailures.Count -ne 0) { return }"),
            clear_run_artifacts.index("Get-ChildItem"),
        )

    def test_validation_admin_identity_is_stdin_only_and_sessionless(self) -> None:
        source = self.source
        identity_start = source.index("$identityPayload = [ordered]@{")
        attestation_ready = source.index(
            "Assert-ValidationAttestationReady", identity_start
        )
        invocation = source[identity_start:attestation_ready]
        self.assertIn("-InputBytes $identityBytes", invocation)
        self.assertIn("'-ValidationAttestationOnly'", invocation)
        self.assertNotIn("'-ExpectedAdminSid'", invocation)
        self.assertNotIn("'-ExpectedAdminAccount'", invocation)
        self.assertNotIn("'-RegisteredDiscordUserId'", invocation)
        self.assertNotIn("-ExpectedAdminSid', $ExpectedAdminSid", source)
        self.assertIn("validation_admin_state_conflict", source)
        self.assertIn("Get-ValidationAdminWatcherCount", source)
        self.assertIn("Remove-ValidationAttestation", source)
        self.assertIn("validationAttestationHash", source)
        self.assertIn("bootstrapNonce -cne $validationAttestationNonce", source)
        self.assertIn("$scratchHostSessionPlaceholder", source)
        self.assertNotIn("-File', $provisioner, '-ExpectedAdminSid'", source)

    def test_uses_archive_overlay_full_startup_dependencies_and_fresh_mic_off(self) -> None:
        source = self.source
        self.assertIn("docker-compose.conversation-archive.yml", source)
        self.assertIn("EVELYN_CONVERSATION_ARCHIVE_COMMAND_GUILD_ID", source)
        self.assertIn("Initialize-EvelynConversationArchiveTest.ps1", source)
        self.assertIn("Start-EvelynConversationArchiveAdmin.ps1", source)
        self.assertIn("Assert-ComposeContract -Archive", source)
        self.assertIn("'--no-build', '--wait'", source)
        for service in (
            "main_llm",
            "main_llm_gateway",
            "router_llm",
            "sub_llm",
            "tts",
            "stt",
        ):
            self.assertIn(f"'{service}'", source)
        self.assertIn("TTS_WARMUP", source.upper())
        self.assertIn("MAIN_LLM_EPOCH_FILE=/main-llm-epoch/epoch", source)
        self.assertIn("MAIN_LLM_ADMISSION_GATEWAY_URL", source)
        self.assertIn("MAIN_LLM_PROMPT_ASSETS_EMBEDDED=true", source)
        self.assertIn("MAIN_LLM_REQUIRE_EXACT_PROMPT_ABI=true", source)

        preflight_up = source.index(
            "'up', '-d', '--no-build', '--no-deps', '--wait', '--wait-timeout', '120', 'bot_api', 'control_page'"
        )
        first_supervisor = source.index("$script:hostSupervisor = Start-HostSupervisor", preflight_up)
        first_mic_off = source.index("Wait-FreshPhysicalMicOff", first_supervisor)
        first_supervisor_stop = source.index("Stop-HostSupervisor", first_mic_off)
        full_dependencies = source.index("'main_llm', 'main_llm_gateway', 'router_llm', 'sub_llm', 'tts', 'stt'", first_supervisor_stop)
        attestation = source.index("'-ValidationAttestationOnly'", full_dependencies)
        final_bot = source.index("'bot_api'", attestation)
        manual_discord = source.index("New-DiscordContainer -Image", final_bot)
        ordering = (
            preflight_up,
            first_supervisor,
            first_mic_off,
            first_supervisor_stop,
            full_dependencies,
            attestation,
            final_bot,
            manual_discord,
        )
        self.assertEqual(tuple(sorted(ordering)), ordering)

        self.assertIn("consent.consent.state -ceq 'inactive'", source)
        self.assertIn("micControlPendingRevision -eq 0", source)
        self.assertIn("captureStopped -eq $true", source)
        self.assertIn("discord_voice_lease_release_unverified", source)
        self.assertGreaterEqual(source.count("Wait-FreshPhysicalMicOff"), 3)
        self.assertIn("Restore-DockerInitialState", source)
        self.assertIn("Get-DockerDesktopOwnerProcessSnapshot", source)
        self.assertIn("Get-DockerDesktopWslSnapshot", source)
        self.assertIn("Initialize-DockerContext", source)
        self.assertIn("docker_context_override_forbidden", source)
        self.assertIn("docker_context_not_local_desktop", source)
        self.assertIn("@('--context', $dockerContextName) + $Arguments", source)
        self.assertGreaterEqual(source.count("'--context', $dockerContextName"), 3)
        self.assertIn("Group-Object -Property ProcessName", source)
        self.assertIn("Test-DockerDesktopFullyStopped", source)
        self.assertIn("docker_initial_state_unknown", source)
        self.assertIn("Capture-DockerHostSnapshot", source)
        self.assertIn("Get-ContainerSnapshot", source)
        self.assertIn("Get-ProtectedImageSnapshot", source)
        self.assertIn("Test-ProductionContainersStopped", source)
        self.assertIn("Ensure-DockerForCleanup", source)
        self.assertIn("Assert-DockerHostSnapshotUnchanged", source)
        self.assertIn("non_owned_docker_state_drift", source)
        self.assertIn("ai.evelyn.owner", source)
        self.assertIn("ai.evelyn.run-id", source)
        self.assertIn("Remove-OwnedDockerResources", source)
        self.assertIn("docker_owned_resource_cleanup_unverified", source)
        self.assertIn("Clear-OwnedRunArtifacts", source)
        self.assertIn("run_artifact_cleanup_unverified", source)
        docker_start = source.index("Start-DockerIfNeeded", source.index("try {"))
        host_snapshot = source.index("Capture-DockerHostSnapshot", docker_start)
        image_build = source.index("Build-CurrentSourceImages", host_snapshot)
        self.assertLess(docker_start, host_snapshot)
        self.assertLess(host_snapshot, image_build)
        cleanup_start = source.index("try { Ensure-DockerForCleanup }", image_build)
        cleanup_stop = source.index("try { Stop-Discord }", cleanup_start)
        self.assertLess(cleanup_start, cleanup_stop)
        owned_zero = source.index(
            "try { Remove-OwnedDockerResources }", cleanup_stop
        )
        non_owned_snapshot = source.index(
            "try { Assert-DockerHostSnapshotUnchanged }", owned_zero
        )
        docker_restore = source.index(
            "try { Restore-DockerInitialState }", non_owned_snapshot
        )
        self.assertLess(owned_zero, non_owned_snapshot)
        self.assertLess(non_owned_snapshot, docker_restore)
        cleanup = source[
            source.index("function Remove-OwnedDockerResources") :
            source.index("function Clear-OwnedRunArtifacts")
        ]
        self.assertNotIn("-AllowFailure", cleanup)

    @unittest.skipUnless(
        os.name == "nt" and shutil.which("pwsh"),
        "PowerShell 7 is required",
    )
    def test_docker_initial_state_contract_fails_closed_without_calling_docker(self) -> None:
        environment = os.environ.copy()
        environment["EVELYN_LAUNCHER_UNDER_TEST"] = str(LAUNCHER)
        command = r"""
$tokens = $null
$errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile(
    $env:EVELYN_LAUNCHER_UNDER_TEST,
    [ref]$tokens,
    [ref]$errors
)
$function = $ast.Find({
    param($node)
    $node -is [Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -ceq 'Get-DockerInitialState'
}, $true)
. ([scriptblock]::Create($function.Extent.Text))
function Test-DockerReady { return $script:ready }
function Get-DockerDesktopOwnerProcessSnapshot { return $script:processes }
function Get-DockerDesktopWslSnapshot { return $script:wsl }
function Invoke-Docker {
    return [pscustomobject]@{ ExitCode = 0; Stdout = $script:status }
}
$script:ready = $true
$script:processes = 'Docker Desktop'
$script:wsl = 'docker-desktop'
$script:status = 'running'
$on = Get-DockerInitialState
$script:wsl = ''
$readyUnknown = ''
try { $null = Get-DockerInitialState } catch { $readyUnknown = $_.Exception.Message }
$script:ready = $false
$script:processes = ''
$script:wsl = ''
$script:status = 'stopped'
$off = Get-DockerInitialState
$script:processes = 'com.docker.backend'
$unknown = ''
try { $null = Get-DockerInitialState } catch { $unknown = $_.Exception.Message }
[pscustomobject]@{
    parseErrors = @($errors).Count
    on = $on.EngineRunning
    off = $off.EngineRunning
    readyUnknown = $readyUnknown
    unknown = $unknown
} | ConvertTo-Json -Compress
"""
        completed = subprocess.run(
            ["pwsh", "-NoProfile", "-Command", command],
            cwd=REPO_ROOT,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(
            json.loads(completed.stdout),
            {
                "parseErrors": 0,
                "on": True,
                "off": False,
                "readyUnknown": "docker_initial_state_unknown",
                "unknown": "docker_initial_state_unknown",
            },
        )

    @unittest.skipUnless(
        os.name == "nt" and shutil.which("pwsh"),
        "PowerShell 7 is required",
    )
    def test_docker_context_is_locked_to_local_desktop_without_calling_docker(self) -> None:
        environment = os.environ.copy()
        environment["EVELYN_LAUNCHER_UNDER_TEST"] = str(LAUNCHER)
        environment.pop("DOCKER_HOST", None)
        environment.pop("DOCKER_CONTEXT", None)
        command = r"""
$tokens = $null
$errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile(
    $env:EVELYN_LAUNCHER_UNDER_TEST,
    [ref]$tokens,
    [ref]$errors
)
$function = $ast.Find({
    param($node)
    $node -is [Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -ceq 'Initialize-DockerContext'
}, $true)
. ([scriptblock]::Create($function.Extent.Text))
$script:dockerCommand = [pscustomobject]@{ Source = 'docker.exe' }
$script:dockerContextName = ''
$script:endpoint = 'npipe:////./pipe/dockerDesktopLinuxEngine'
function Invoke-External {
    param([string]$FilePath, [string[]]$Arguments, [int]$TimeoutSec)
    if ($Arguments[1] -ceq 'show') {
        return [pscustomobject]@{ ExitCode = 0; Stdout = 'desktop-linux' }
    }
    return [pscustomobject]@{ ExitCode = 0; Stdout = $script:endpoint }
}
Initialize-DockerContext
$locked = $script:dockerContextName
$script:endpoint = 'tcp://remote.example.invalid:2376'
$remote = ''
try { Initialize-DockerContext } catch { $remote = $_.Exception.Message }
$env:DOCKER_HOST = 'tcp://remote.example.invalid:2376'
$override = ''
try { Initialize-DockerContext } catch { $override = $_.Exception.Message }
[pscustomobject]@{
    parseErrors = @($errors).Count
    locked = $locked
    remote = $remote
    override = $override
} | ConvertTo-Json -Compress
"""
        completed = subprocess.run(
            ["pwsh", "-NoProfile", "-Command", command],
            cwd=REPO_ROOT,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(
            json.loads(completed.stdout),
            {
                "parseErrors": 0,
                "locked": "desktop-linux",
                "remote": "docker_context_not_local_desktop",
                "override": "docker_context_override_forbidden",
            },
        )

    @unittest.skipUnless(
        os.name == "nt" and shutil.which("pwsh"),
        "PowerShell 7 is required",
    )
    def test_docker_restore_and_host_drift_contracts_without_calling_docker(self) -> None:
        environment = os.environ.copy()
        environment["EVELYN_LAUNCHER_UNDER_TEST"] = str(LAUNCHER)
        command = r"""
$tokens = $null
$errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile(
    $env:EVELYN_LAUNCHER_UNDER_TEST,
    [ref]$tokens,
    [ref]$errors
)
foreach ($name in @(
    'Ensure-DockerForCleanup',
    'Restore-DockerInitialState',
    'Test-ProductionContainersStopped',
    'Assert-DockerHostSnapshotUnchanged'
)) {
    $function = $ast.Find({
        param($node)
        $node -is [Management.Automation.Language.FunctionDefinitionAst] -and
            $node.Name -ceq $name
    }, $true)
    . ([scriptblock]::Create($function.Extent.Text))
}
function Test-DockerReady { return $script:ready }
function Test-DockerDesktopFullyStopped { return (-not $script:ready) }
function Start-DockerDesktop {
    $script:startCalls += 1
    $script:ready = $true
    $script:processes = $script:startProcesses
    $script:wsl = $script:startWsl
}
function Stop-DockerDesktop {
    $script:stopCalls += 1
    $script:ready = $false
    $script:processes = ''
    $script:wsl = ''
}
function Get-DockerDesktopOwnerProcessSnapshot { return $script:processes }
function Get-DockerDesktopWslSnapshot { return $script:wsl }
function Get-ContainerSnapshot { return $script:containers }
function Get-ProtectedImageSnapshot { return $script:images }
function Invoke-Docker {
    return [pscustomobject]@{ ExitCode = 0; Stdout = $script:dockerOutput }
}
$script:startCalls = 0
$script:stopCalls = 0
$script:ready = $true
$script:processes = 'Docker Desktop'
$script:wsl = 'docker-desktop'
$script:startProcesses = 'Docker Desktop'
$script:startWsl = 'docker-desktop'
$initialDockerState = [pscustomobject]@{
    EngineRunning = $false; DesktopProcesses = ''; DockerWsl = ''
}
$initialDockerRunning = $false
Restore-DockerInitialState
$offRestored = (-not $script:ready -and $script:stopCalls -eq 1)

$script:ready = $false
$script:processes = ''
$script:wsl = ''
$initialDockerState = [pscustomobject]@{
    EngineRunning = $true
    DesktopProcesses = 'Docker Desktop'
    DockerWsl = 'docker-desktop'
}
$initialDockerRunning = $true
Ensure-DockerForCleanup
Restore-DockerInitialState
$onRestored = ($script:ready -and $script:startCalls -eq 1)

$script:dockerOutput = 'running-production-container'
$productionFailure = ''
try { Test-ProductionContainersStopped } catch { $productionFailure = $_.Exception.Message }
$script:dockerOutput = ''
$dockerHostSnapshotCaptured = $true
$baselineContainers = 'baseline'
$protectedImageSnapshot = 'protected'
$script:containers = 'drifted'
$script:images = 'protected'
$driftFailure = ''
try { Assert-DockerHostSnapshotUnchanged } catch { $driftFailure = $_.Exception.Message }
[pscustomobject]@{
    parseErrors = @($errors).Count
    offRestored = $offRestored
    onRestored = $onRestored
    productionFailure = $productionFailure
    driftFailure = $driftFailure
} | ConvertTo-Json -Compress
"""
        completed = subprocess.run(
            ["pwsh", "-NoProfile", "-Command", command],
            cwd=REPO_ROOT,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(
            json.loads(completed.stdout),
            {
                "parseErrors": 0,
                "offRestored": True,
                "onRestored": True,
                "productionFailure": "production_container_running",
                "driftFailure": "non_owned_docker_state_drift",
            },
        )

    @unittest.skipUnless(
        os.name == "nt" and shutil.which("pwsh"),
        "PowerShell 7 is required",
    )
    def test_no_live_switch_is_side_effect_free_and_parseable(self) -> None:
        completed = subprocess.run(
            ["pwsh", "-NoProfile", "-File", str(LAUNCHER)],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["state"], "live_confirmation_required")
        self.assertTrue(payload["contentFree"])
        self.assertEqual(payload["sourceDigest"], "")
        self.assertFalse(payload["operatorConfirmed"])


if __name__ == "__main__":
    unittest.main()
