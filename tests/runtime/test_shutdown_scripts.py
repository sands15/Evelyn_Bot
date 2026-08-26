from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
LAUNCHERS = REPO_ROOT / "evelyn_core" / "runtime" / "launchers"


class ShutdownScriptContractTests(unittest.TestCase):
    def read_script(self, name: str) -> str:
        return (LAUNCHERS / name).read_text(encoding="utf-8")

    def test_local_stop_script_has_safe_contract(self) -> None:
        script = self.read_script("stop_evelyn_local.ps1")

        self.assertIn("[switch]$DryRun", script)
        self.assertIn("stop_evelyn_local", script)
        self.assertIn("command line did not prove Evelyn ownership", script)
        self.assertIn("openclaw", script.lower())
        self.assertIn("codex-home", script.lower())
        self.assertIn("stop_evelyn_stack.ps1", script)
        self.assertIn("stop_local.bat", script)
        self.assertNotIn("wsl.exe --shutdown", script)
        self.assertNotIn("taskkill", script.lower())
        self.assertIn("$env:DISCORD_BOT_TOKEN = 'local-only-disabled'", script)
        self.assertIn("Remove-Item Env:DISCORD_BOT_TOKEN", script)

    def test_local_stop_requests_supervisor_owned_shutdown_before_fallback(self) -> None:
        script = self.read_script("stop_evelyn_local.ps1")

        request_index = script.index(
            "Set-Content -LiteralPath $supervisorStopRequest"
        )
        wait_index = script.index("evelyn_core\\.host_supervisor", request_index)
        compose_index = script.index("Invoke-EvelynDockerComposeStop", wait_index)
        fallback_index = script.index("$collected = Collect-Targets", compose_index)
        self.assertLess(request_index, wait_index)
        self.assertLess(wait_index, compose_index)
        self.assertLess(compose_index, fallback_index)

    def test_stack_stop_script_has_safe_contract(self) -> None:
        script = self.read_script("stop_evelyn_stack.ps1")

        self.assertIn("[switch]$DryRun", script)
        self.assertIn("stop_evelyn_stack", script)
        self.assertIn("command line did not prove Evelyn ownership", script)
        self.assertIn("openclaw", script.lower())
        self.assertIn("codex-home", script.lower())
        self.assertIn("stop_evelyn_local.ps1", script)
        self.assertIn("pkill -f", script)
        self.assertNotIn("wsl.exe --shutdown", script)
        self.assertNotIn("taskkill", script.lower())

    def test_dry_run_does_not_write_stop_marker(self) -> None:
        for script_name in ("stop_evelyn_local.ps1", "stop_evelyn_stack.ps1"):
            with self.subTest(script=script_name):
                script = self.read_script(script_name)
                marker_write = script.index("Set-Content -LiteralPath $stopMarker")
                dry_run_guard = script.rindex("if (-not $DryRun)", 0, marker_write)

                self.assertGreater(marker_write, dry_run_guard)

    def test_stop_scripts_protect_each_other_and_openclaw(self) -> None:
        for script_name in ("stop_evelyn_local.ps1", "stop_evelyn_stack.ps1"):
            with self.subTest(script=script_name):
                script = self.read_script(script_name).lower()

                self.assertIn("stop_evelyn_local.ps1", script)
                self.assertIn("stop_evelyn_stack.ps1", script)
                self.assertIn("stop_local.bat", script)
                self.assertIn("openclaw", script)
                self.assertIn("codex-home", script)

    def test_stop_scripts_do_not_target_process_names_blindly(self) -> None:
        for script_name in ("stop_evelyn_local.ps1", "stop_evelyn_stack.ps1"):
            with self.subTest(script=script_name):
                script = self.read_script(script_name).lower()

                self.assertNotIn("ancestorpassthroughnames", script)
                self.assertNotIn("get-process", script)
                self.assertNotIn("where-object", script)
                self.assertNotIn("shutdown.exe", script)

    def test_local_stop_targets_only_local_runtime_ports(self) -> None:
        script = self.read_script("stop_evelyn_local.ps1")

        self.assertIn("$targetPorts = @(8798, 8799, 8880, 8891, 9820, 9821, 9822)", script)
        self.assertNotIn("$targetPorts = @(3000", script)
        self.assertNotIn("8787", script)
        self.assertNotIn("8912", script)

    def test_local_launcher_keeps_control_page_and_bot_api_ports_separate(self) -> None:
        script = self.read_script("start_local_background.ps1")

        self.assertIn("$controlPagePublicPort = if ($env:CONTROL_PAGE_PUBLIC_PORT)", script)
        self.assertIn("$botApiPort = if ($env:CONTROL_PAGE_BOT_API_PORT)", script)
        self.assertIn("docker-compose.fast-control.yml", script)
        self.assertIn("'bot_api'", script)
        self.assertIn("'control_page'", script)
        self.assertIn("LOCAL_BRIDGE_BOT_API_BASE = 'http://127.0.0.1:$botApiPort'", script)
        self.assertIn(
            'Wait-HttpReady -Url "http://127.0.0.1:$botApiPort/health" '
            "-Label 'Docker Bot API' -Contract 'bot_api'",
            script,
        )
        self.assertIn(
            'Wait-HttpReady -Url "http://127.0.0.1:$controlPagePublicPort/health" '
            "-Label 'Docker Control Page' -Contract 'control_page'",
            script,
        )
        self.assertNotIn("function Start-LocalControlService", script)

    def test_local_launcher_defers_minecraft_services_until_explicit_start(self) -> None:
        script = self.read_script("start_local_background.ps1")
        docker_core = script[
            script.index("function Start-DockerCore") :
            script.index("function Test-HostSupervisorRunning")
        ]
        voyager_start = (
            REPO_ROOT / "evelyn_core" / "start_voyager.bat"
        ).read_text(encoding="utf-8")

        self.assertNotIn("'--profile', 'voyager'", docker_core)
        self.assertIn("'minecraft_llm'", docker_core)
        self.assertNotIn("'codex_gateway'", docker_core)
        self.assertNotIn("'voyager'", docker_core)
        self.assertIn("Minecraft world service is deferred", docker_core)
        self.assertIn("-Profiles voyager", voyager_start)
        self.assertIn(
            "-Services router_llm,minecraft_llm,voyager",
            voyager_start,
        )

    def test_local_launcher_starts_supervisor_before_reporting_ready(self) -> None:
        script = self.read_script("start_local_background.ps1")

        start_index = script.index("Start-DockerCore")
        bot_wait_index = script.index(
            'Wait-HttpReady -Url "http://127.0.0.1:$botApiPort/health" '
            "-Label 'Docker Bot API' -Contract 'bot_api'"
        )
        page_wait_index = script.index(
            'Wait-HttpReady -Url "http://127.0.0.1:$controlPagePublicPort/health" '
            "-Label 'Docker Control Page' -Contract 'control_page'"
        )
        supervisor_index = script.index("Start-HostSupervisor", page_wait_index)
        ready_index = script.index('Write-Host "[Evelyn] Docker local core is ready. Control page: $controlPageUrl"')

        self.assertLess(start_index, bot_wait_index)
        self.assertLess(bot_wait_index, page_wait_index)
        self.assertLess(page_wait_index, supervisor_index)
        self.assertLess(supervisor_index, ready_index)
        self.assertNotIn("Start-LocalIoBridge", script)

    def test_local_launcher_uses_supervisor_as_bridge_parent(self) -> None:
        script = self.read_script("start_local_background.ps1")

        self.assertIn("function Start-HostSupervisor", script)
        self.assertIn("function Resolve-HostPython", script)
        self.assertIn("function Wait-HostSupervisorReady", script)
        self.assertIn(".venv-host\\Scripts\\python.exe", script)
        self.assertIn("EVELYN_HOST_PYTHON", script)
        self.assertIn("bootstrap_host_runtime.ps1", script)
        self.assertIn("consecutiveFreshHeartbeats", script)
        self.assertIn("evelyn_core.host_supervisor", script)
        self.assertIn("$supervisorLog", script)
        self.assertIn("-WindowStyle $windowStyle", script)
        self.assertNotIn("py -3.11 -m evelyn_core.host_supervisor", script)

    def test_local_launcher_clears_rotation_stop_request_before_restart(self) -> None:
        script = self.read_script("start_local_background.ps1")
        rotation = script[
            script.index("function Stop-PreviousHostSupervisorGeneration") :
            script.index("function Start-HostSupervisor")
        ]

        self.assertIn(
            "Remove-Item -LiteralPath $supervisorStopRequest -Force "
            "-ErrorAction SilentlyContinue",
            rotation,
        )
        self.assertLess(
            rotation.index("Set-Content -LiteralPath $supervisorStopRequest"),
            rotation.index("Remove-Item -LiteralPath $supervisorStopRequest"),
        )

    def test_local_launcher_requires_two_fresh_supervisor_and_bridge_heartbeats(self) -> None:
        script = self.read_script("start_local_background.ps1")
        readiness = script[
            script.index("function Wait-HostSupervisorReady") :
            script.index("function Assert-TtsProfileReady")
        ]

        self.assertIn(
            "$localBridgeStatus = Join-Path $projectRoot "
            "'runtime_artifacts\\local_bridge\\status.json'",
            script,
        )
        self.assertIn("Get-Content -Raw -LiteralPath $supervisorStatus", readiness)
        self.assertIn("Get-Content -Raw -LiteralPath $localBridgeStatus", readiness)
        self.assertIn("'host_supervisor.status.v1'", readiness)
        self.assertIn("'local_io_bridge.status.v1'", readiness)
        self.assertIn("$lastSupervisorHeartbeat = 0.0", readiness)
        self.assertIn("$lastBridgeHeartbeat = 0.0", readiness)
        self.assertIn(
            "$supervisorHeartbeat -gt $lastSupervisorHeartbeat",
            readiness,
        )
        self.assertIn("$bridgeHeartbeat -gt $lastBridgeHeartbeat", readiness)
        self.assertIn("$consecutiveFreshHeartbeats -ge 2", readiness)
        self.assertIn("$supervisor.localBridge.ownershipReady -eq $true", readiness)
        self.assertIn(
            "$supervisor.localBridge.birthIdentityRecorded -eq $true",
            readiness,
        )
        self.assertIn("[int]$bridge.pid -eq [int]$supervisor.localBridge.pid", readiness)
        self.assertGreaterEqual(
            readiness.count("$consecutiveFreshHeartbeats = 0"),
            3,
        )
        self.assertIn("($bridge.ready -is [bool])", readiness)
        self.assertIn("$bridge.ready -eq $true", readiness)

    def test_local_launcher_requires_capture_ready_when_mic_is_enabled(self) -> None:
        script = self.read_script("start_local_background.ps1")
        readiness = script[
            script.index("function Wait-HostSupervisorReady") :
            script.index("function Assert-TtsProfileReady")
        ]

        self.assertIn("($bridge.micEnabled -is [bool])", readiness)
        self.assertIn("($bridge.mic.enabled -is [bool])", readiness)
        self.assertIn("$bridge.micEnabled -eq $bridge.mic.enabled", readiness)
        self.assertIn("$bridge.micEnabled -eq $false -or", readiness)
        self.assertIn("($bridge.mic.captureReady -is [bool])", readiness)
        self.assertIn("$bridge.mic.captureReady -eq $true", readiness)
        self.assertIn("$captureReady", readiness)

    def test_host_runtime_bootstrap_is_locked_and_keeps_torch_optional(self) -> None:
        bootstrap = self.read_script("bootstrap_host_runtime.ps1")
        lock = (REPO_ROOT / "requirements.host.lock").read_text(encoding="utf-8")

        self.assertIn("requirements.host.lock", bootstrap)
        self.assertIn(".venv-host", bootstrap)
        self.assertIn("Python311", bootstrap)
        self.assertIn("import aiohttp, numpy, sounddevice", bootstrap)
        self.assertIn("from PIL import ImageGrab", bootstrap)
        self.assertIn("aiohttp==3.14.1", lock)
        self.assertIn("numpy==2.4.6", lock)
        self.assertIn("Pillow==12.3.0", lock)
        self.assertIn("tzdata==2026.3", lock)
        self.assertIn("sounddevice==0.5.5", lock)
        self.assertIn("soxr==1.1.0", lock)
        self.assertNotIn("torch==", lock)

    def test_local_launcher_waits_for_vision_before_host_bridge(self) -> None:
        script = self.read_script("start_local_background.ps1")

        self.assertIn("function Wait-HttpReady", script)
        self.assertIn("START_MODEL_WAIT_TIMEOUT_SEC", script)
        self.assertIn("[bool]$health.ok -and [bool]$health.ready", script)
        self.assertIn("[bool]$health.ok -and [bool]$health.models.smol.loaded", script)
        self.assertIn("$health.ready -eq $true", script)
        self.assertIn("$health.model_loaded -eq $true", script)
        self.assertIn("[string]$health.status -eq 'healthy'", script)
        self.assertIn("[string]$health.model_id -eq 'k2-fsa/OmniVoice'", script)
        self.assertIn(
            "[string]$health.model_revision -eq 'c5fdb5ccb189668d56333f77ba2629f4cd7535f4'",
            script,
        )
        self.assertIn("[switch]$ModelStartup", script)
        self.assertIn(
            "Wait-Port -HostName '127.0.0.1' -Port 8880 -Label 'OmniVoice-TTS' -ModelStartup",
            script,
        )
        self.assertIn(
            "Wait-HttpReady -Url 'http://127.0.0.1:8880/health' -Label 'OmniVoice-TTS' -Contract 'omnivoice'",
            script,
        )
        self.assertIn(
            "Wait-HttpReady -Url 'http://127.0.0.1:8892/health' -Label 'STT'",
            script,
        )
        self.assertIn(
            "[ValidateSet('ready', 'vision', 'omnivoice', 'bot_api', 'control_page')]",
            script,
        )
        self.assertIn("[string]$health.role -ceq 'fast-control-bot-api'", script)
        self.assertIn("[string]$health.role -ceq 'control-page'", script)
        self.assertIn("$health.botProxyReady -eq $true", script)
        self.assertIn(
            "[string]$health.sourceIdentity.imageSourceRevision -ceq $sourceRevision",
            script,
        )
        self.assertIn(
            "[string]$health.botSourceIdentity.imageSourceRevision -ceq $sourceRevision",
            script,
        )
        vision_wait = script.index(
            "Wait-HttpReady -Url 'http://127.0.0.1:8891/health' -Label 'Vision' -Contract 'vision'"
        )
        bot_wait = script.index(
            'Wait-HttpReady -Url "http://127.0.0.1:$botApiPort/health" '
            "-Label 'Docker Bot API' -Contract 'bot_api'"
        )
        control_wait = script.index(
            'Wait-HttpReady -Url "http://127.0.0.1:$controlPagePublicPort/health" '
            "-Label 'Docker Control Page' -Contract 'control_page'"
        )
        supervisor_start = script.index("Start-HostSupervisor", control_wait)
        self.assertLess(vision_wait, bot_wait)
        self.assertLess(bot_wait, control_wait)
        self.assertLess(control_wait, supervisor_start)
        self.assertNotIn(
            "Wait-Port -HostName '127.0.0.1' -Port $botApiPort",
            script,
        )
        self.assertNotIn(
            "Wait-Port -HostName '127.0.0.1' -Port $controlPagePublicPort",
            script,
        )
        self.assertIn("VISION_SERVICE_URL = 'http://127.0.0.1:8891'", script)
        self.assertIn("from PIL import ImageGrab", script)

    def test_local_launcher_fails_early_for_missing_tts_profile(self) -> None:
        script = self.read_script("start_local_background.ps1")

        self.assertIn("function Assert-TtsProfileReady", script)
        self.assertIn("ref_audio.wav", script)
        self.assertIn("meta.json", script)
        self.assertIn("$metadata.ref_text", script)
        profile_check = script.rindex("Assert-TtsProfileReady")
        supervisor_rotation = script.rindex(
            "Stop-PreviousHostSupervisorGeneration"
        )
        docker_start = script.rindex("Start-DockerCore")
        self.assertLess(profile_check, supervisor_rotation)
        self.assertLess(supervisor_rotation, docker_start)
        self.assertLess(
            profile_check,
            script.index("Wait-Port -HostName '127.0.0.1' -Port 9820"),
        )

    def test_local_launcher_exports_host_paths_before_compose(self) -> None:
        script = self.read_script("start_local_background.ps1")

        self.assertIn("$env:EVELYN_HOST_PROJECT_ROOT = $projectRoot", script)
        self.assertIn("$env:EVELYN_OMNIVOICE_PROFILES_DIR = $ttsProfilesRoot", script)
        self.assertIn("$env:DISCORD_BOT_TOKEN = 'local-only-disabled'", script)
        self.assertLess(
            script.index("$env:EVELYN_HOST_PROJECT_ROOT = $projectRoot"),
            script.rindex("Start-DockerCore"),
        )

    def test_local_launcher_scopes_runtime_channel_tokens_to_authorized_children(self) -> None:
        script = self.read_script("start_local_background.ps1")
        docker_helper = script[
            script.index("function Invoke-DockerCommandWithRuntimeChannelTokens") :
            script.index("function Test-DockerContainerRunning")
        ]
        supervisor = script[
            script.index("function Start-HostSupervisor") :
            script.index("function Open-ControlPage")
        ]
        browser_start = script.index("function Open-ControlPage")
        browser = script[
            browser_start :
            script.index("\ntry {\n    # Keep channel credentials", browser_start)
        ]
        main = script[script.rindex("try {") :]

        self.assertNotIn(
            "$env:LOCAL_BRIDGE_STATUS_AUTH_TOKEN = New-SecureRuntimeToken",
            script,
        )
        self.assertNotIn(
            "$env:EVELYN_INTERNAL_CONTROL_TOKEN = New-SecureRuntimeToken",
            script,
        )
        self.assertNotIn(
            "$env:EVELYN_VOICE_CAPTURE_HOST_AUTH_TOKEN = New-SecureRuntimeToken",
            script,
        )
        self.assertNotIn(
            "$env:EVELYN_VOICE_INPUT_LEASE_TOKEN = New-SecureRuntimeToken",
            script,
        )
        self.assertNotIn(
            "$env:EVELYN_WORKSPACE_SANDBOX_AUTH_TOKEN = New-SecureRuntimeToken",
            script,
        )
        self.assertIn(
            "$voiceCaptureHostAuthToken = New-SecureRuntimeToken",
            script,
        )
        self.assertNotIn(
            "$previousVoiceCaptureHostAuthToken.Trim()",
            script,
        )
        self.assertLess(
            script.index(
                "[Environment]::SetEnvironmentVariable(\n"
                "    'LOCAL_BRIDGE_STATUS_AUTH_TOKEN',\n"
                "    $null,"
            ),
            script.index("Initialize-EvelynSourceRevision"),
        )
        self.assertLess(
            script.index(
                "[Environment]::SetEnvironmentVariable(\n"
                "    'EVELYN_INTERNAL_CONTROL_TOKEN',\n"
                "    $null,"
            ),
            script.index("Initialize-EvelynSourceRevision"),
        )
        self.assertLess(
            script.index(
                "[Environment]::SetEnvironmentVariable(\n"
                "    'EVELYN_WORKSPACE_SANDBOX_AUTH_TOKEN',\n"
                "    $null,"
            ),
            script.index("Initialize-EvelynSourceRevision"),
        )
        self.assertLess(
            script.index(
                "[Environment]::SetEnvironmentVariable(\n"
                "    'EVELYN_VOICE_CAPTURE_HOST_AUTH_TOKEN',\n"
                "    $null,"
            ),
            script.index("Initialize-EvelynSourceRevision"),
        )
        self.assertLess(
            script.index(
                "[Environment]::SetEnvironmentVariable(\n"
                "    'EVELYN_VOICE_INPUT_LEASE_TOKEN',\n"
                "    $null,"
            ),
            script.index("Initialize-EvelynSourceRevision"),
        )
        self.assertIn("-Value $localBridgeStatusAuthToken", docker_helper)
        self.assertIn("-Value $internalControlToken", docker_helper)
        self.assertIn("-Value $voiceCaptureHostAuthToken", docker_helper)
        self.assertIn("-Value $voiceInputLeaseToken", docker_helper)
        self.assertIn("-Value $workspaceSandboxAuthToken", docker_helper)
        self.assertIn("Invoke-DockerCommandWithRuntimeChannelTokens", script)
        self.assertIn("-Value $localBridgeStatusAuthToken", supervisor)
        self.assertIn("-Name 'EVELYN_INTERNAL_CONTROL_TOKEN'", supervisor)
        self.assertIn(
            "-Name 'EVELYN_VOICE_CAPTURE_HOST_AUTH_TOKEN'",
            supervisor,
        )
        self.assertIn(
            "-Name 'EVELYN_VOICE_INPUT_LEASE_TOKEN'",
            supervisor,
        )
        self.assertIn(
            "-Name 'EVELYN_WORKSPACE_SANDBOX_AUTH_TOKEN'",
            supervisor,
        )
        self.assertIn("-Value $null", supervisor)
        self.assertNotIn("LOCAL_BRIDGE_STATUS_AUTH_TOKEN", browser)
        self.assertNotIn("EVELYN_INTERNAL_CONTROL_TOKEN", browser)
        self.assertNotIn("EVELYN_VOICE_CAPTURE_HOST_AUTH_TOKEN", browser)
        self.assertNotIn("EVELYN_VOICE_INPUT_LEASE_TOKEN", browser)
        self.assertNotIn("EVELYN_WORKSPACE_SANDBOX_AUTH_TOKEN", browser)
        self.assertLess(
            main.index("-Name 'LOCAL_BRIDGE_STATUS_AUTH_TOKEN'"),
            main.index("Assert-TtsProfileReady"),
        )
        self.assertLess(
            main.index("-Name 'EVELYN_INTERNAL_CONTROL_TOKEN'"),
            main.index("Assert-TtsProfileReady"),
        )
        self.assertLess(
            main.index("-Name 'EVELYN_VOICE_CAPTURE_HOST_AUTH_TOKEN'"),
            main.index("Assert-TtsProfileReady"),
        )
        self.assertLess(
            main.index("-Name 'EVELYN_VOICE_INPUT_LEASE_TOKEN'"),
            main.index("Assert-TtsProfileReady"),
        )
        self.assertLess(
            main.index("-Name 'EVELYN_WORKSPACE_SANDBOX_AUTH_TOKEN'"),
            main.index("Assert-TtsProfileReady"),
        )
        self.assertLess(main.index("Open-ControlPage"), main.index("} finally {"))

    def test_workspace_mutation_token_is_ephemeral_and_scoped_to_compose_and_host(self) -> None:
        script = self.read_script("start_local_background.ps1")
        docker_helper = script[
            script.index("function Invoke-DockerCommandWithRuntimeChannelTokens") :
            script.index("function Test-DockerContainerRunning")
        ]
        supervisor = script[
            script.index("function Start-HostSupervisor") :
            script.index("function Open-ControlPage")
        ]
        browser = script[
            script.index("function Open-ControlPage") :
            script.index("\ntry {\n    # Keep channel credentials", script.index("function Open-ControlPage"))
        ]

        self.assertIn("$workspaceMutationAuthToken = New-SecureRuntimeToken", script)
        self.assertNotIn("$previousWorkspaceMutationAuthToken.Trim()", script)
        self.assertIn("-Value $workspaceMutationAuthToken", docker_helper)
        self.assertIn("-Value $workspaceMutationAuthToken", supervisor)
        self.assertNotIn("EVELYN_WORKSPACE_MUTATION_AUTH_TOKEN", browser)
        self.assertIn("-Value $previousWorkspaceMutationAuthToken", script)

    def test_workspace_sandbox_token_is_ephemeral_and_scoped_to_bot_and_host(self) -> None:
        script = self.read_script("start_local_background.ps1")
        docker_helper = script[
            script.index("function Invoke-DockerCommandWithRuntimeChannelTokens") :
            script.index("function Test-DockerContainerRunning")
        ]
        supervisor = script[
            script.index("function Start-HostSupervisor") :
            script.index("function Open-ControlPage")
        ]
        browser = script[
            script.index("function Open-ControlPage") :
            script.index("\ntry {\n    # Keep channel credentials", script.index("function Open-ControlPage"))
        ]

        self.assertIn("$workspaceSandboxAuthToken = New-SecureRuntimeToken", script)
        self.assertNotIn("$previousWorkspaceSandboxAuthToken.Trim()", script)
        self.assertIn("-Value $workspaceSandboxAuthToken", docker_helper)
        self.assertIn("-Value $workspaceSandboxAuthToken", supervisor)
        self.assertNotIn("EVELYN_WORKSPACE_SANDBOX_AUTH_TOKEN", browser)
        self.assertIn("-Value $previousWorkspaceSandboxAuthToken", script)

    def test_keep_discord_requires_stable_voice_lease_token_before_mutation(self) -> None:
        script = self.read_script("start_local_background.ps1")

        capture = script.index("$previousVoiceInputLeaseToken =")
        keep = script.index("$keepDiscordBot =", capture)
        guard = script.index("if (\n    $keepDiscordBot -and (", keep)
        failure = script.index(
            "throw 'discord_keep_requires_stable_voice_input_lease_token'",
            guard,
        )
        first_mutation = script.index(
            "[Environment]::SetEnvironmentVariable(",
            failure,
        )

        self.assertEqual(script.count("$keepDiscordBot ="), 1)
        self.assertIn(
            "[string]::IsNullOrWhiteSpace($previousVoiceInputLeaseToken)",
            script[guard:failure],
        )
        self.assertIn(
            "$previousVoiceInputLeaseToken.Trim().Length -lt 32",
            script[guard:failure],
        )
        self.assertLess(capture, keep)
        self.assertLess(keep, guard)
        self.assertLess(guard, failure)
        self.assertLess(failure, first_mutation)
        self.assertLess(failure, script.rindex("Stop-PreviousHostSupervisorGeneration"))
        self.assertLess(failure, script.rindex("Start-DockerCore"))

    def test_keep_discord_reuses_valid_prior_voice_lease_token(self) -> None:
        script = self.read_script("start_local_background.ps1")
        assignment = script[
            script.index("$voiceInputLeaseToken = if (") :
            script.index("\n\nif (Test-Path $stopMarker)")
        ]

        self.assertIn(
            "-not [string]::IsNullOrWhiteSpace($previousVoiceInputLeaseToken)",
            assignment,
        )
        self.assertIn(
            "$previousVoiceInputLeaseToken.Trim().Length -ge 32",
            assignment,
        )
        self.assertIn("$previousVoiceInputLeaseToken.Trim()", assignment)
        self.assertIn("New-SecureRuntimeToken", assignment)

    def test_local_launcher_uses_path_safe_allowlisted_image_builder(self) -> None:
        launcher = self.read_script("start_local_background.ps1")
        builder = self.read_script("build_local_docker_images.ps1")

        self.assertIn("build_local_docker_images.ps1", launcher)
        self.assertIn("& $dockerImageBuilder -ProjectRoot $projectRoot", launcher)
        self.assertIn("$dockerBuildServices = @(", launcher)
        self.assertIn("$dockerBuildServices += 'discord_bot'", launcher)
        self.assertIn("$dockerBuildServices += 'tts'", launcher)
        self.assertIn("Test-DockerImageExists -Image $ttsImage", launcher)
        self.assertNotIn(
            "Test-DockerImageSourceRevision -Image $ttsImage",
            launcher,
        )
        self.assertIn("@('up', '-d', '--no-build')", launcher)
        self.assertIn(
            "& $dockerImageBuilder -ProjectRoot $projectRoot "
            "-Services $dockerBuildServices",
            launcher,
        )
        self.assertIn("function Test-DockerImageSourceRevision", launcher)
        revision_probe = launcher[
            launcher.index("function Test-DockerImageSourceRevision") :
            launcher.index("function Stop-BotApiForImageRefresh")
        ]
        self.assertIn("docker image inspect", revision_probe)
        self.assertIn("EVELYN_IMAGE_SOURCE_REVISION=", revision_probe)
        self.assertIn("$revisions.Count -eq 1", revision_probe)
        self.assertIn("$revisions[0] -ceq $ExpectedRevision", revision_probe)

        self.assertIn(
            "Test-DockerImageSourceRevision -Image $botApiImage "
            "-ExpectedRevision $sourceRevision",
            launcher,
        )
        self.assertIn(
            "Test-DockerImageSourceRevision -Image $controlPageImage "
            "-ExpectedRevision $sourceRevision",
            launcher,
        )
        self.assertIn(
            "Test-DockerImageSourceRevision -Image $discordBotImage "
            "-ExpectedRevision $sourceRevision",
            launcher,
        )
        core_build = launcher[
            launcher.index("if ($coreAppImagesNeedBuild)") :
            launcher.index("if ($discordImageNeedsBuild)")
        ]
        self.assertIn("'bot_api'", core_build)
        self.assertIn("'control_page'", core_build)
        self.assertIn("'vision'", core_build)
        self.assertNotIn("'discord_bot'", core_build)
        discord_build = launcher[
            launcher.index("if ($discordImageNeedsBuild)") :
            launcher.index("if ($buildEnabled -or $ttsImageMissing)")
        ]
        self.assertIn("$dockerBuildServices += 'discord_bot'", discord_build)
        self.assertIn("Stop-BotApiForImageRefresh", launcher)
        self.assertIn(
            "if ($dockerBuildServices -contains 'bot_api')",
            launcher,
        )
        self.assertIn("'--timeout', '130'", launcher)
        self.assertIn("$minecraftOwnerClaim", launcher)
        refresh = launcher[
            launcher.index("function Stop-BotApiForImageRefresh") :
            launcher.index("function Start-DockerCore")
        ]
        self.assertIn("Test-DockerContainerRunning", refresh)
        self.assertIn("Write-Warning", refresh)
        self.assertIn("claim JSON is diagnostic only", refresh)
        self.assertNotIn("$deadline", refresh)
        self.assertNotIn("did not release its Minecraft owner claim", refresh)
        self.assertNotIn(
            "Refusing to recreate it while ownership is ambiguous",
            refresh,
        )
        self.assertNotIn("@('compose') + $composeBaseArgs + @('build'", launcher)

        self.assertIn(
            "[ValidateSet('bot_api', 'control_page', 'discord_bot', "
            "'main_llm', 'tts', 'vision')]",
            builder,
        )
        self.assertIn("$requiresAsciiAlias", builder)
        self.assertIn("QueryDosDevice", builder)
        self.assertIn("& subst.exe $candidate $resolvedProjectRoot", builder)
        self.assertIn("Refusing to remove $mappedDrive because its target changed.", builder)
        self.assertIn("& subst.exe $mappedDrive '/D'", builder)
        self.assertIn("'evelyn-fast-control-bot_api'", builder)
        self.assertIn("'evelyn-fast-control-control_page'", builder)
        self.assertIn("'evelyn-fast-control-discord_bot'", builder)
        self.assertIn("'evelyn-omnivoice-tts:recipe-e8151492550b'", builder)
        self.assertIn("'docker\\Dockerfile.omnivoice'", builder)
        self.assertIn("--build-context", builder)
        self.assertIn("omnivoice_source=", builder)
        self.assertIn(
            "@($definition['BuildContexts'] | Where-Object { $_ })",
            builder,
        )
        self.assertIn("'evelyn-fast-control-vision'", builder)
        self.assertIn("'evelyn-fast-control-vision_runtime'", builder)
        self.assertIn("'docker\\Dockerfile.vision-ingress'", builder)
        self.assertIn("'docker\\Dockerfile.vision'", builder)
        self.assertNotIn("Invoke-Expression", builder)

    def test_local_launcher_seals_and_refreshes_only_the_main_llm_runtime_image(self) -> None:
        launcher = self.read_script("start_local_background.ps1")
        builder = self.read_script("build_local_docker_images.ps1")

        self.assertIn("function Test-MainLlmDockerImageContract", launcher)
        self.assertIn(
            "io.evelyn.llama-runtime-contract-sha256",
            launcher,
        )
        self.assertIn(
            "$mainLlmRuntimeContract = (Get-FileHash",
            launcher,
        )
        self.assertIn("-Algorithm SHA256).Hash.ToLowerInvariant()", launcher)
        self.assertIn(
            "$mainLlmImage = 'evelyn-fast-control-main_llm:latest'",
            launcher,
        )
        self.assertIn(
            "$mainLlmImageMissing = -not "
            "(Test-DockerImageExists -Image $mainLlmImage)",
            launcher,
        )
        self.assertIn("$mainLlmImageNeedsBuild = $buildEnabled -or", launcher)
        self.assertIn("$mainLlmImageMissing -or", launcher)
        main_build = launcher[
            launcher.index("if ($mainLlmImageNeedsBuild)") :
            launcher.index("if ($buildEnabled -or $ttsImageMissing)")
        ]
        self.assertIn("$dockerBuildServices += 'main_llm'", main_build)
        self.assertNotIn("router_llm", main_build)
        self.assertNotIn("minecraft_llm", main_build)
        self.assertNotIn("sub_llm", main_build)

        final_contract_check = launcher.rindex(
            "if (-not (Test-MainLlmDockerImageContract"
        )
        compose_up = launcher.index(
            "$composeArgs = $composeBaseArgs + @('up', '-d', '--no-build')"
        )
        self.assertLess(final_contract_check, compose_up)
        self.assertIn(
            "throw 'Main LLM runtime image does not match "
            "docker\\Dockerfile.llama.'",
            launcher[final_contract_check:compose_up],
        )

        self.assertIn("main_llm = @{", builder)
        self.assertIn("Dockerfile = 'docker\\Dockerfile.llama'", builder)
        self.assertIn("Image = 'evelyn-fast-control-main_llm'", builder)
        self.assertIn("SealDockerfile = $true", builder)
        self.assertIn("-DockerfileContract $dockerfileContract", builder)
        self.assertIn(
            '"io.evelyn.llama-runtime-contract-sha256=$DockerfileContract"',
            builder,
        )
        self.assertNotIn("router_llm = @{", builder)
        self.assertNotIn("minecraft_llm = @{", builder)
        self.assertNotIn("sub_llm = @{", builder)

    def test_local_launcher_selects_and_validates_native_main_build_only(self) -> None:
        launcher = self.read_script("start_local_background.ps1")

        self.assertIn("Join-Path $llamaCppRoot 'build-sm120-v1'", launcher)
        self.assertIn("$env:EVELYN_LLAMA_CPP_DIR = $llamaCppRoot", launcher)
        self.assertIn(
            "$env:EVELYN_MAIN_LLM_BUILD_DIR = $mainLlmBuildRoot",
            launcher,
        )
        self.assertIn(
            "-Pattern '^CMAKE_CUDA_ARCHITECTURES:[^=]+=120a-real$'",
            launcher,
        )
        self.assertLess(
            launcher.index("$mainLlmServer ="),
            launcher.index("Start-DockerCore"),
        )

    def test_runtime_launchers_require_an_exact_clean_source_revision(self) -> None:
        local_launcher = self.read_script("start_local_background.ps1")
        compose_launcher = self.read_script("start_docker_compose_services.ps1")
        builder = self.read_script("build_local_docker_images.ps1")
        revision_helper = self.read_script("source_revision.ps1")

        for script in (local_launcher, compose_launcher, builder):
            self.assertIn("source_revision.ps1", script)
            self.assertIn("Initialize-EvelynSourceRevision", script)
        self.assertIn("status --porcelain --untracked-files=all", revision_helper)
        self.assertIn(
            "-- . ':(exclude)docs/99_PROJECT_INBOX.md'",
            revision_helper,
        )
        self.assertEqual(revision_helper.count(":(exclude)"), 1)
        self.assertIn("rev-parse HEAD", revision_helper)
        self.assertIn("dirty source tree", revision_helper)
        self.assertIn("40- or 64-character hexadecimal revision", revision_helper)
        self.assertIn("does not match the checked-out Evelyn source revision", revision_helper)
        self.assertIn(
            "'--build-arg', \"EVELYN_SOURCE_REVISION=$sourceRevision\"",
            builder,
        )

    def test_local_launcher_reports_documented_fixed_startup_error_codes(self) -> None:
        launcher = self.read_script("start_local_background.ps1")
        root_start = (REPO_ROOT / "start.bat").read_text(encoding="utf-8")
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        quickstart = (
            REPO_ROOT / "docs" / "EVELYN_DOCKER_RUNTIME_QUICKSTART.md"
        ).read_text(encoding="utf-8")
        expected = {
            "EVL-START-1001",
            "EVL-START-1002",
            "EVL-START-1003",
            "EVL-START-2001",
            "EVL-START-2002",
            "EVL-START-3001",
            "EVL-START-4001",
            "EVL-START-4002",
            "EVL-START-4003",
            "EVL-START-9000",
        }

        self.assertEqual(set(re.findall(r"EVL-START-\d{4}", launcher)), expected)
        self.assertEqual(set(re.findall(r"EVL-START-\d{4}", quickstart)), expected)
        self.assertIn("function Assert-DockerReady", launcher)
        source_revision = launcher.index(
            "$sourceRevision = Initialize-EvelynSourceRevision"
        )
        unknown_stage = launcher.index("$startupStage = 'unknown'")
        self.assertLess(source_revision, unknown_stage)
        self.assertLess(
            unknown_stage,
            launcher.rindex("Assert-DockerReady"),
        )
        self.assertLess(
            launcher.rindex("Assert-DockerReady"),
            launcher.rindex("Start-DockerCore"),
        )
        self.assertIn("errorCode=$($failure.Code)", launcher)
        self.assertIn("evelyn.startup_error.v1", launcher)
        self.assertNotIn("$ErrorRecord.Exception.Message", launcher)
        self.assertNotIn("ScriptStackTrace", launcher)
        self.assertIn("$startupExitCode = 1", launcher)
        self.assertIn("exit $startupExitCode", launcher)
        self.assertIn("startup-error.log", launcher)
        self.assertIn("Remove-Item -LiteralPath", launcher)
        self.assertIn(
            "$startupStage = 'control_page_open'\n    Open-ControlPage",
            launcher,
        )
        self.assertIn("EVELYN_KEEP_CONSOLE_ON_EXIT", root_start)
        failure_branch = root_start[root_start.index('if not "%EXITCODE%"=="0"') :]
        self.assertIn("pause", failure_branch)
        self.assertIn('if /I "%LOCAL_BACKGROUND%"=="true" goto :background', (
            REPO_ROOT / "evelyn_core" / "start_local.bat"
        ).read_text(encoding="utf-8"))
        self.assertIn("Startup error codes", readme)
        self.assertIn("docs/EVELYN_DOCKER_RUNTIME_QUICKSTART.md", readme)

    def test_bot_launcher_prefers_explicit_bot_api_port_env(self) -> None:
        script = self.read_script("start_bot.ps1")

        self.assertIn("elseif ($env:CONTROL_PAGE_BOT_API_PORT) { $env:CONTROL_PAGE_BOT_API_PORT }", script)

    def test_stack_stop_targets_stack_ports_without_ssh_or_system_ports(self) -> None:
        script = self.read_script("stop_evelyn_stack.ps1")

        self.assertIn("$targetPorts = @(3000, 8765, 8787, 8798, 8799, 8880, 8891, 8912, 9820, 9821, 9822)", script)
        self.assertNotIn(" 22,", script)
        self.assertNotIn(" 3389,", script)

    def test_full_stack_launcher_waits_for_bot_api_before_opening_control_page(self) -> None:
        script = self.read_script("start_background_stack.ps1")

        bot_start_index = script.index("Start-SupervisedService -Title 'Bot' -Port 8798")
        bot_wait_index = script.index("Wait-Port -HostName '127.0.0.1' -Port 8798 -Label 'Evelyn Bot API'")
        page_wait_index = script.index("Wait-Port -HostName '127.0.0.1' -Port 8799 -Label 'Evelyn Control Page'")
        ready_index = script.index("Write-Host '[Evelyn] Full stack launch requested.")
        open_index = script.index("Open-ChromeToControlPage", ready_index)

        self.assertLess(bot_start_index, bot_wait_index)
        self.assertLess(bot_wait_index, page_wait_index)
        self.assertLess(page_wait_index, ready_index)
        self.assertLess(ready_index, open_index)

    def test_local_stop_shims_delegate_to_local_script(self) -> None:
        root_shim = (REPO_ROOT / "stop_local.bat").read_text(encoding="utf-8")
        core_shim = (REPO_ROOT / "evelyn_core" / "stop_local.bat").read_text(encoding="utf-8")

        self.assertIn("evelyn_core\\stop_local.bat", root_shim)
        self.assertIn("stop_evelyn_local.ps1", core_shim)
        self.assertNotIn("stop_evelyn_stack.ps1", root_shim + core_shim)

    def test_control_page_fallback_uses_local_stop_helper(self) -> None:
        server = (REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "control_page_server.py").read_text(encoding="utf-8")

        self.assertIn("stop_evelyn_local.ps1", server)
        self.assertNotIn('"stop_evelyn_stack.ps1"', server)

    def test_control_page_child_processes_scrub_voice_capture_auth(self) -> None:
        server = REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "control_page_server.py"
        tree = ast.parse(server.read_text(encoding="utf-8"))
        functions = {
            node.name: node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }

        for name in (
            "schedule_local_stack_shutdown",
            "schedule_local_stack_restart",
            "open_path_with_system",
            "open_url_with_system",
        ):
            with self.subTest(function=name):
                calls = [
                    node
                    for node in ast.walk(functions[name])
                    if isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "subprocess"
                    and node.func.attr == "Popen"
                ]
                self.assertTrue(calls)
                for call in calls:
                    environment = next(
                        (keyword.value for keyword in call.keywords if keyword.arg == "env"),
                        None,
                    )
                    self.assertEqual(
                        ast.unparse(environment) if environment is not None else None,
                        "voice_capture_auth_scrubbed_environment()",
                    )

    def test_control_page_shutdown_chat_proxies_before_container_fallback(self) -> None:
        server = (REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "control_page_server.py").read_text(encoding="utf-8")

        shutdown_branch = server[server.index("if normalized in LOCAL_SHUTDOWN_COMMANDS:") :]
        proxy_index = shutdown_branch.index('proxy_json(request, "POST", "/api/control-page/chat", body=payload)')
        fallback_index = shutdown_branch.index("ok, detail = schedule_local_stack_shutdown()")

        self.assertLess(proxy_index, fallback_index)

    def test_main_control_page_exposes_shutdown_endpoint(self) -> None:
        composition = (
            REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "control_page_composition_runtime.py"
        ).read_text(encoding="utf-8")
        control_page_state = (
            REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "control_page_state.py"
        ).read_text(encoding="utf-8")

        self.assertIn("async def shutdown(", composition)
        self.assertIn('("POST", "/api/control-page/shutdown", self.shutdown)', composition)
        self.assertIn('("OPTIONS", "/api/control-page/shutdown", self.shutdown)', composition)
        self.assertIn("handle_control_page_shutdown_request", composition)
        self.assertIn('handle_input(guild, "/shutdown")', control_page_state)

    def test_shutdown_command_copy_is_runtime_scoped(self) -> None:
        control_page_tools = (
            REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "control_page_tools.py"
        ).read_text(encoding="utf-8")
        page_html = (REPO_ROOT / "docs" / "index.html").read_text(encoding="utf-8")
        combined = control_page_tools + page_html

        self.assertNotIn("Shut down the full Evelyn stack", combined)
        self.assertIn("Shut down Evelyn runtime", control_page_tools)
        self.assertIn("Shut down Evelyn runtime", page_html)

    def test_stack_shutdown_reply_does_not_claim_whole_wsl_stops(self) -> None:
        main_py = (REPO_ROOT / "main.py").read_text(encoding="utf-8")
        command_handlers = (
            REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "discord_command_handlers.py"
        ).read_text(encoding="utf-8")

        self.assertNotIn("and WSL will stop", main_py + command_handlers)
        self.assertIn("Evelyn-owned WSL services will stop", command_handlers)


if __name__ == "__main__":
    unittest.main()
