from __future__ import annotations

import re
import unittest
from pathlib import Path


REPO_ROOT = next(
    path for path in Path(__file__).resolve().parents if (path / "main.py").exists()
)
LAUNCHER = REPO_ROOT / "tools" / "run_discord_voice_corpus_capture.ps1"
DOCKERIGNORE = REPO_ROOT / ".dockerignore"


class DiscordVoiceCorpusCaptureLauncherContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = LAUNCHER.read_text(encoding="utf-8")
        cls.dockerignore = DOCKERIGNORE.read_text(encoding="utf-8")

    def test_uses_clean_head_and_isolated_exact_owned_resources(self) -> None:
        source = self.source

        self.assertTrue(source.startswith("#Requires -Version 7.2\n"))
        self.assertIn("'status', '--porcelain', '--untracked-files=all', '--', '.'", source)
        self.assertIn("'rev-parse', 'HEAD'", source)
        self.assertIn("source_tree_not_clean", source)
        self.assertIn("[Guid]::NewGuid().ToString('N')", source)
        self.assertIn("ai.evelyn.owner", source)
        self.assertIn("ai.evelyn.run-id", source)
        self.assertIn("Assert-OwnedContainer", source)
        self.assertIn("Assert-OwnedNetwork", source)
        self.assertIn("Assert-OwnedImage", source)
        self.assertIn("Assert-OwnedDockerResourcesZero", source)
        self.assertIn("Assert-NoExistingCaptureOwner", source)
        self.assertIn("Recover-OwnedResourceIds", source)
        self.assertIn("docker\\Dockerfile.bot-api", source)
        self.assertIn("docker\\Dockerfile.discord-bot", source)
        self.assertIn("--pull=false", source)
        self.assertIn("capture-tool-sha256", source)
        self.assertIn("--read-only", source)
        self.assertIn("/app/tools/discord_voice_corpus_capture.py", source)
        self.assertIn("!tools/discord_voice_corpus_capture.py", self.dockerignore)
        self.assertNotIn("source=$captureTool", source)
        self.assertIn("Get-ProtectedImageSnapshot", source)
        self.assertIn("Join-Path $projectRoot 'runtime_artifacts'", source)
        self.assertIn("Join-Path $runtimeArtifactsRoot 'validation'", source)
        self.assertIn("voice_asr_staging", source)
        self.assertIn("capture-labs", source)
        self.assertIn("captures", source)
        self.assertIn("Assert-CaptureRootsSafe", source)
        self.assertIn("capture_root_unsafe", source)
        self.assertNotIn("runtime_artifacts\\validation\\voice_asr'", source)
        self.assertNotIn("docker compose", source.lower())
        self.assertNotIn("compose down", source.lower())
        self.assertNotIn("prune", source.lower())
        self.assertNotIn("--gpus", source)
        self.assertNotIn("--publish", source)
        self.assertNotIn("--device", source)
        self.assertNotIn("/dev/snd", source)

        mandatory_channel = re.compile(
            r"\[Parameter\(Mandatory = \$true\)\]\s*"
            r"\[ValidatePattern\('\^\[0-9\]\{17,20\}\$'\)\]\s*"
            r"\[string\]\$ChannelId,"
        )
        self.assertRegex(source, mandatory_channel)
        forbidden_channel_id = "".join(("116348740", "9024024667"))
        self.assertNotIn(forbidden_channel_id, source)
        self.assertNotIn(forbidden_channel_id, Path(__file__).read_text(encoding="utf-8"))

        preflight = source.index("\n    Assert-NoExistingCaptureOwner\n")
        first_build = source.index("$botImageId = Build-OwnedImage")
        self.assertLess(preflight, first_build)
        self.assertIn("Local\\EvelynDiscordVoiceCorpusCaptureV1", source)
        self.assertIn("$captureMutex.WaitOne(0)", source)
        self.assertIn("$captureMutex.ReleaseMutex()", source)
        self.assertIn("Acquire-HostVoiceExclusion", source)
        self.assertIn("Release-HostVoiceExclusion", source)
        self.assertIn(".evelyn_bot.lock", source)
        self.assertIn("local_bridge", source)
        self.assertIn("instance.lock", source)
        self.assertIn("$stream.Lock(0, 1)", source)
        self.assertIn("$stream.Unlock(0, 1)", source)
        self.assertIn("host_voice_owner_active", source)

        mutating_lines = [
            line
            for line in source.splitlines()
            if any(word in line for word in ("'build'", "'rm'", "'stop'", "'kill'"))
        ]
        self.assertFalse(
            any("evelyn-fast-control-" in line for line in mutating_lines),
            mutating_lines,
        )

    def test_keeps_bot_token_on_redirected_stdin_and_lease_ephemeral(self) -> None:
        source = self.source

        self.assertIn("Read-Host 'Discord bot token' -AsSecureString", source)
        self.assertIn("[System.Diagnostics.ProcessStartInfo]::new()", source)
        self.assertIn("$startInfo.RedirectStandardInput = $true", source)
        self.assertIn("@('start', '--attach', '--interactive', $captureContainerId)", source)
        self.assertIn("$process.StandardInput.WriteLine($plainToken)", source)
        self.assertIn("$process.StandardInput.Close()", source)
        self.assertIn("--token-stdin", source)
        self.assertNotRegex(source, r"'--env',\s*'DISCORD_BOT_TOKEN'")
        self.assertNotIn("DISCORD_BOT_TOKEN=$plainToken", source)
        self.assertIn("[byte[]]::new(48)", source)
        self.assertIn("RandomNumberGenerator", source)
        self.assertGreaterEqual(
            source.count("'--env', 'EVELYN_VOICE_INPUT_LEASE_TOKEN'"),
            2,
        )
        self.assertIn("[Array]::Clear($leaseBytes", source)
        self.assertIn("ZeroFreeBSTR", source)

    def test_bounds_capture_validates_ten_wavs_and_restores_host_state(self) -> None:
        source = self.source

        self.assertIn("[int]$CaptureTimeoutSec = 1800", source)
        self.assertIn("$captureOuterGraceSec = 60", source)
        self.assertIn("$clipCount = 10", source)
        self.assertIn("capture_complete clips=10", source)
        self.assertIn("Get-AllowlistedRunFailureCode", source)
        self.assertIn("discord_capture_failed code=$runFailureCode", source)
        self.assertIn("Test-CanonicalWaveFile", source)
        self.assertIn("[BitConverter]::ToUInt16($header, 22) -ne 1", source)
        self.assertIn("[BitConverter]::ToUInt32($header, 24) -ne 16000", source)
        self.assertIn("[BitConverter]::ToUInt16($header, 34) -ne 16", source)
        self.assertIn("capture_wav_duplicate", source)
        self.assertIn("Move-Item -LiteralPath $capturePath -Destination $stagingAttempt", source)
        self.assertIn("Remove-Item -LiteralPath $resolved -Recurse -Force", source)
        self.assertIn("Get-DockerInitialState", source)
        self.assertIn("Get-Process -Name 'Docker Desktop'", source)
        self.assertIn("if ($desktopProcesses.Count -eq 0)", source)
        self.assertIn("Start-DockerDesktop", source)
        self.assertIn("Stop-DockerDesktop", source)
        self.assertIn("Get-ContainerSnapshot", source)
        self.assertIn("protected_image_drift", source)
        self.assertIn("ConvertTo-Json -InputObject @($snapshot)", source)
        self.assertIn("$hostSnapshotCaptured", source)
        self.assertIn("$ownedDockerResourcesZero", source)
        self.assertIn("$hostDockerStateUnchanged", source)
        self.assertIn("$previousDiscordToken", source)
        self.assertIn("$previousLeaseToken", source)

        wait_index = source.index("Wait-CaptureProcess -CaptureHandle $captureProcess")
        lease_index = source.index("Assert-VoiceLeaseReleased", wait_index)
        publish_index = source.index("Publish-ValidatedCapture", lease_index)
        self.assertLess(wait_index, lease_index)
        self.assertLess(lease_index, publish_index)
        self.assertIn("voice_input_lease\\owner.json", source)
        self.assertIn("voice_input_lease.owner.v1", source)
        self.assertIn("[string]$lease.state -cne 'unowned'", source)
        self.assertIn("[string]$lease.source -cne ''", source)
        self.assertIn("[string]$lease.instanceId -cne ''", source)
        self.assertIn("[string]$lease.leaseId -cne ''", source)

        failure_cleanup = re.compile(
            r"\(\$runFailed -or -not \$captureSucceeded -or "
            r"\$cleanupFailures\.Count -ne 0\).*?"
            r"Remove-OwnedDirectory.*?-Path \$stagingAttempt",
            re.DOTALL,
        )
        self.assertRegex(source, failure_cleanup)

        destructive_file_lines = [
            line.strip()
            for line in source.splitlines()
            if "Remove-Item" in line
        ]
        self.assertEqual(
            destructive_file_lines,
            ["Remove-Item -LiteralPath $resolved -Recurse -Force"],
        )
        self.assertFalse(any("*" in line for line in destructive_file_lines))


if __name__ == "__main__":
    unittest.main()
