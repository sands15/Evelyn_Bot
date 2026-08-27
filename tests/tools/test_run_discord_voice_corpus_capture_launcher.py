from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = next(
    path for path in Path(__file__).resolve().parents if (path / "main.py").exists()
)
LAUNCHER = REPO_ROOT / "tools" / "run_discord_voice_corpus_capture.ps1"
CREDENTIAL_MODULE = REPO_ROOT / "tools" / "discord_capture_credential.psm1"
DOCKERIGNORE = REPO_ROOT / ".dockerignore"
CAPTURE_DOCKERFILE = REPO_ROOT / "docker" / "Dockerfile.discord-capture"
CAPTURE_DOCKERIGNORE = REPO_ROOT / "docker" / "Dockerfile.discord-capture.dockerignore"
CAPTURE_REQUIREMENTS = REPO_ROOT / "docker" / "requirements.discord-capture.txt"


class DiscordVoiceCorpusCaptureLauncherContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = LAUNCHER.read_text(encoding="utf-8")
        cls.credential_source = CREDENTIAL_MODULE.read_text(encoding="utf-8")
        cls.dockerignore = DOCKERIGNORE.read_text(encoding="utf-8")
        cls.capture_dockerfile = CAPTURE_DOCKERFILE.read_text(encoding="utf-8")
        cls.capture_dockerignore = CAPTURE_DOCKERIGNORE.read_text(encoding="utf-8")
        cls.capture_requirements = CAPTURE_REQUIREMENTS.read_text(encoding="utf-8")

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
        self.assertIn("docker\\Dockerfile.discord-capture", source)
        self.assertNotIn("docker\\Dockerfile.discord-bot", source)
        self.assertIn("--pull=false", source)
        self.assertIn("capture-tool-sha256", source)
        self.assertIn("--read-only", source)
        self.assertIn(
            "$null -ne $container.HostConfig.DeviceRequests -and",
            source,
        )
        self.assertIn(
            "$null -ne $container.HostConfig.Devices -and",
            source,
        )
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

        capture_requirements = self.capture_requirements.lower()
        for forbidden_dependency in (
            "torch",
            "silero",
            "sounddevice",
            "pillow",
            "psutil",
            "requests",
        ):
            self.assertNotIn(forbidden_dependency, capture_requirements)
        for required_dependency in ("aiohttp", "discord.py", "numpy", "pynacl", "davey"):
            self.assertIn(required_dependency, capture_requirements)

        capture_dockerfile = self.capture_dockerfile
        self.assertIn("apt-get install -y --no-install-recommends libopus0", capture_dockerfile)
        self.assertNotIn("COPY .", capture_dockerfile)
        self.assertNotIn("ffmpeg", capture_dockerfile)
        self.assertNotIn("libportaudio", capture_dockerfile)
        self.assertNotIn("libsndfile", capture_dockerfile)
        dependency_install = capture_dockerfile.index("python -m pip install")
        source_revision_arg = capture_dockerfile.index("ARG EVELYN_SOURCE_REVISION")
        self.assertLess(dependency_install, source_revision_arg)
        self.assertIn("!evelyn_core/runtime/**", self.capture_dockerignore)
        self.assertIn("!evelyn_voice/**", self.capture_dockerignore)
        self.assertIn("!tools/discord_voice_corpus_capture.py", self.capture_dockerignore)

        mandatory_channel = re.compile(
            r"\[Parameter\(Mandatory = \$true, ParameterSetName = 'Capture'\)\]\s*"
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

    def test_caches_bot_token_with_dpapi_and_keeps_stdin_handoff(self) -> None:
        source = self.source
        credential = self.credential_source

        self.assertIn("tools\\discord_capture_credential.psm1", source)
        self.assertIn("discord-capture-credential-v1", source)
        self.assertIn("Read-Host 'Discord bot token' -AsSecureString", source)
        self.assertEqual(source.count("Read-Host 'Discord bot token'"), 1)
        self.assertIn("Get-SavedDiscordToken", source)
        self.assertIn("Read-EvelynDiscordTokenCache", source)
        self.assertIn("Write-EvelynDiscordTokenCache", source)
        self.assertIn("Remove-EvelynDiscordTokenCache", source)
        self.assertIn("'discord_token_cache_invalid'", source)
        self.assertIn("'discord_token_cache_unsafe'", source)
        self.assertIn("[switch]$ClearSavedDiscordToken", source)

        docker_start = source.index("$initialDockerRunning = Get-DockerInitialState")
        bot_ready = source.index("\n    Wait-BotApiReady\n")
        token_acquire = source.index(
            "[byte[]]$discordTokenBytes = Get-SavedDiscordToken",
            bot_ready,
        )
        token_handoff = source.index(
            "$captureProcess = Start-CaptureWithTokenBytes",
            token_acquire,
        )
        self.assertLess(docker_start, bot_ready)
        self.assertLess(bot_ready, token_acquire)
        self.assertLess(token_acquire, token_handoff)

        self.assertIn("[Security.SecureString]$SecureToken", source)
        self.assertIn("$secureToken.MakeReadOnly()", source)
        self.assertIn("[Runtime.InteropServices.Marshal]::Copy(", source)
        self.assertIn("ZeroFreeBSTR", source)
        self.assertIn("$process.StandardInput.BaseStream", source)
        self.assertIn("$stdinStream.Write($TokenBytes", source)
        self.assertIn("$stdinStream.WriteByte(10)", source)
        self.assertIn("[Array]::Clear($tokenChars", source)
        self.assertIn("[Array]::Clear($discordTokenBytes", source)
        self.assertIn("return ,$tokenBytes", source)
        self.assertIn("$processStarted = $true", source)
        self.assertNotIn("$process.StandardInput.Write($tokenChars", source)
        self.assertNotIn("PtrToStringBSTR", source)
        self.assertNotIn("$plainToken", source)
        self.assertIn("[System.Diagnostics.ProcessStartInfo]::new()", source)
        self.assertIn("$startInfo.RedirectStandardInput = $true", source)
        self.assertIn("@('start', '--attach', '--interactive', $captureContainerId)", source)
        self.assertIn("$stdinStream.Close()", source)
        self.assertIn("--token-stdin", source)
        self.assertNotRegex(source, r"'--env',\s*'DISCORD_BOT_TOKEN'")
        self.assertNotIn("DISCORD_BOT_TOKEN=$plainToken", source)

        self.assertIn("ProtectedData]::Protect", credential)
        self.assertIn("ProtectedData]::Unprotect", credential)
        self.assertIn("DataProtectionScope]::CurrentUser", credential)
        self.assertIn("[IO.FileOptions]::WriteThrough", credential)
        self.assertIn("$stream.Flush($true)", credential)
        self.assertIn(
            "[IO.File]::Move($paths.Temporary, $paths.Token, $true)",
            credential,
        )
        self.assertIn("$script:maxCiphertextBytes = 4096", credential)
        self.assertIn("S-1-5-18", credential)
        self.assertIn("SetAccessRuleProtection($true, $false)", credential)
        self.assertNotIn("DISCORD_BOT_TOKEN", credential)
        self.assertIn("return ,$plaintext", credential)
        self.assertIn("'discord_auth_failed'", source)
        self.assertIn(
            "$removedRejectedToken = Remove-EvelynDiscordTokenCache",
            source,
        )

        self.assertIn("[byte[]]::new(48)", source)
        self.assertIn("RandomNumberGenerator", source)
        self.assertGreaterEqual(
            source.count("'--env', 'EVELYN_VOICE_INPUT_LEASE_TOKEN'"),
            2,
        )
        self.assertIn("[Array]::Clear($leaseBytes", source)

    @unittest.skipUnless(
        os.name == "nt" and shutil.which("pwsh"),
        "Windows PowerShell and CurrentUser DPAPI are required",
    )
    def test_dpapi_cache_roundtrip_corruption_and_exact_clear(self) -> None:
        fake_token = "dummy-not-a-real-discord-token"
        script = r"""
$ErrorActionPreference = 'Stop'
$Module = $env:EVELYN_CREDENTIAL_TEST_MODULE
$TrustedRoot = $env:EVELYN_CREDENTIAL_TEST_TRUSTED_ROOT
$CredentialRoot = $env:EVELYN_CREDENTIAL_TEST_STORE
Import-Module -Name $Module -Force
$tokenText = [Console]::In.ReadToEnd()
$tokenBytes = [Text.Encoding]::UTF8.GetBytes($tokenText)
$readBytes = $null
$replacementBytes = $null
try {
    Write-EvelynDiscordTokenCache `
        -TrustedRoot $TrustedRoot `
        -CredentialRoot $CredentialRoot `
        -TokenBytes $tokenBytes
    $tokenPath = Join-Path $CredentialRoot 'discord-bot-token.dpapi'
    $partPath = Join-Path $CredentialRoot '.discord-bot-token.dpapi.part'
    $ciphertext = [IO.File]::ReadAllBytes($tokenPath)
    $readBytes = Read-EvelynDiscordTokenCache `
        -TrustedRoot $TrustedRoot `
        -CredentialRoot $CredentialRoot
    $roundTrip = [Linq.Enumerable]::SequenceEqual[byte](
        $tokenBytes,
        $readBytes
    )
    $ciphertextContainsPlain = (
        [Text.Encoding]::Latin1.GetString($ciphertext).Contains($tokenText)
    )
    $tokenAcl = Get-Acl -LiteralPath $tokenPath
    $rules = @($tokenAcl.GetAccessRules(
        $true,
        $true,
        [Security.Principal.SecurityIdentifier]
    ))
    $sentinel = Join-Path $CredentialRoot 'keep-me.txt'
    [IO.File]::WriteAllText($sentinel, 'sentinel')

    [IO.File]::WriteAllBytes($tokenPath, [byte[]](1, 2, 3))
    $corruptCode = ''
    try {
        $null = Read-EvelynDiscordTokenCache `
            -TrustedRoot $TrustedRoot `
            -CredentialRoot $CredentialRoot
    } catch {
        $corruptCode = [string]$_.Exception.Message
    }
    $corruptRemoved = Remove-EvelynDiscordTokenCache `
        -TrustedRoot $TrustedRoot `
        -CredentialRoot $CredentialRoot

    $replacementBytes = [Text.Encoding]::UTF8.GetBytes($tokenText + '-rotated')
    Write-EvelynDiscordTokenCache `
        -TrustedRoot $TrustedRoot `
        -CredentialRoot $CredentialRoot `
        -TokenBytes $replacementBytes
    Write-EvelynDiscordTokenCache `
        -TrustedRoot $TrustedRoot `
        -CredentialRoot $CredentialRoot `
        -TokenBytes $tokenBytes
    $firstClear = Remove-EvelynDiscordTokenCache `
        -TrustedRoot $TrustedRoot `
        -CredentialRoot $CredentialRoot
    $secondClear = Remove-EvelynDiscordTokenCache `
        -TrustedRoot $TrustedRoot `
        -CredentialRoot $CredentialRoot
    $credentialDirectory = [IO.DirectoryInfo]::new($CredentialRoot)
    $aclSections = (
        [Security.AccessControl.AccessControlSections]::Access -bor
        [Security.AccessControl.AccessControlSections]::Owner -bor
        [Security.AccessControl.AccessControlSections]::Group
    )
    $unsafeAcl = [IO.FileSystemAclExtensions]::GetAccessControl(
        $credentialDirectory,
        $aclSections
    )
    $builtinUsers = [Security.Principal.SecurityIdentifier]::new(
        'S-1-5-32-545'
    )
    $unsafeRule = [Security.AccessControl.FileSystemAccessRule]::new(
        $builtinUsers,
        [Security.AccessControl.FileSystemRights]::ReadAndExecute,
        [Security.AccessControl.AccessControlType]::Allow
    )
    $null = $unsafeAcl.AddAccessRule($unsafeRule)
    [IO.FileSystemAclExtensions]::SetAccessControl(
        $credentialDirectory,
        $unsafeAcl
    )
    $unsafeCode = ''
    try {
        $null = Read-EvelynDiscordTokenCache `
            -TrustedRoot $TrustedRoot `
            -CredentialRoot $CredentialRoot
    } catch {
        $unsafeCode = [string]$_.Exception.Message
    }

    [pscustomobject]@{
        roundTrip = $roundTrip
        readType = $readBytes.GetType().FullName
        ciphertextContainsPlain = $ciphertextContainsPlain
        aclProtected = $tokenAcl.AreAccessRulesProtected
        aclRuleCount = $rules.Count
        corruptCode = $corruptCode
        corruptRemoved = $corruptRemoved
        firstClear = $firstClear
        secondClear = $secondClear
        unsafeCode = $unsafeCode
        sentinelPreserved = Test-Path -LiteralPath $sentinel
        tokenAbsent = -not (Test-Path -LiteralPath $tokenPath)
        partAbsent = -not (Test-Path -LiteralPath $partPath)
    } | ConvertTo-Json -Compress
} finally {
    [Array]::Clear($tokenBytes, 0, $tokenBytes.Length)
    if ($null -ne $readBytes) {
        [Array]::Clear($readBytes, 0, $readBytes.Length)
    }
    if ($null -ne $replacementBytes) {
        [Array]::Clear($replacementBytes, 0, $replacementBytes.Length)
    }
}
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            trusted_root = Path(temp_dir) / "trusted"
            credential_root = trusted_root / "Evelyn" / "credential-v1"
            trusted_root.mkdir()
            test_environment = os.environ.copy()
            test_environment.update(
                {
                    "EVELYN_CREDENTIAL_TEST_MODULE": str(CREDENTIAL_MODULE),
                    "EVELYN_CREDENTIAL_TEST_TRUSTED_ROOT": str(trusted_root),
                    "EVELYN_CREDENTIAL_TEST_STORE": str(credential_root),
                }
            )
            completed = subprocess.run(
                [
                    shutil.which("pwsh") or "pwsh",
                    "-NoProfile",
                    "-Command",
                    script,
                ],
                input=fake_token,
                text=True,
                capture_output=True,
                check=False,
                env=test_environment,
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertTrue(result["roundTrip"])
        self.assertEqual(result["readType"], "System.Byte[]")
        self.assertFalse(result["ciphertextContainsPlain"])
        self.assertTrue(result["aclProtected"])
        self.assertEqual(result["aclRuleCount"], 2)
        self.assertEqual(result["corruptCode"], "discord_token_cache_invalid")
        self.assertTrue(result["corruptRemoved"])
        self.assertTrue(result["firstClear"])
        self.assertFalse(result["secondClear"])
        self.assertEqual(result["unsafeCode"], "discord_token_cache_unsafe")
        self.assertTrue(result["sentinelPreserved"])
        self.assertTrue(result["tokenAbsent"])
        self.assertTrue(result["partAbsent"])

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
        self.assertIn("Get-DockerDesktopOwnerProcesses", source)
        self.assertIn("Test-DockerDesktopWslStopped", source)
        self.assertIn("'docker-desktop-data'", source)
        self.assertIn("Test-DockerDesktopFullyStopped", source)
        self.assertIn("Quarantine-StaleDockerRuntimeSockets", source)
        self.assertIn("'dockerEthernetVfkit'", source)
        self.assertIn("'dockerInference'", source)
        self.assertIn("'userAnalyticsOtlpHttp.sock'", source)
        self.assertIn("'engine.sock'", source)
        self.assertIn("[int64]$entry.Length -ne 0", source)
        self.assertIn("$null -ne $entry.LinkType", source)
        self.assertIn("$null -ne $entry.Target", source)
        self.assertIn("Move-Item `\n            -LiteralPath $specification.Source", source)
        self.assertIn("'desktop', 'start', '--detach', '--timeout', '30'", source)
        self.assertIn("'desktop', 'stop', '--detach', '--timeout', '30'", source)
        self.assertIn("$dockerStartAttemptedByLauncher = $true", source)
        self.assertNotIn("'--force'", source)
        self.assertNotIn("factory reset", source.lower())
        self.assertIn("if ($desktopProcesses.Count -eq 0)", source)
        self.assertIn("Start-DockerDesktop", source)
        self.assertIn("Stop-DockerDesktop", source)
        self.assertIn("Get-ContainerSnapshot", source)
        self.assertIn("protected_image_drift", source)
        self.assertIn("ConvertTo-Json -InputObject @($snapshot)", source)
        self.assertIn("$hostSnapshotCaptured", source)
        self.assertIn("$ownedDockerResourcesZero", source)
        self.assertIn("$hostDockerStateUnchanged", source)
        self.assertNotIn("$previousDiscordToken", source)
        self.assertNotIn("$previousLeaseToken", source)

        restore_start = source.index(
            "} elseif ($dockerStartAttemptedByLauncher) {"
        )
        stop_guard = source.index(
            "if (-not (Test-DockerDesktopFullyStopped))",
            restore_start,
        )
        stop_call = source.index("Stop-DockerDesktop", stop_guard)
        quarantine_call = source.index(
            "Quarantine-StaleDockerRuntimeSockets",
            stop_call,
        )
        off_proof = source.index(
            "} elseif (-not (Test-DockerDesktopFullyStopped))",
            quarantine_call,
        )
        self.assertLess(restore_start, stop_guard)
        self.assertLess(stop_guard, stop_call)
        self.assertLess(stop_call, quarantine_call)
        self.assertLess(quarantine_call, off_proof)

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
