from __future__ import annotations

import json
import os
import ssl
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


REPO_ROOT = next(
    path for path in Path(__file__).resolve().parents if (path / "main.py").exists()
)
PROVISIONER = (
    REPO_ROOT / "scripts" / "Initialize-EvelynConversationArchiveTest.ps1"
)
ENTRYPOINT = REPO_ROOT / "tools" / "evelyn_private_archive_test_provision.ps1"


def _assert_loopback_tls_handshake(cert_file: Path, key_file: Path) -> None:
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(cert_file, key_file)
    client_context = ssl.create_default_context(cafile=cert_file)
    client_in = ssl.MemoryBIO()
    client_out = ssl.MemoryBIO()
    server_in = ssl.MemoryBIO()
    server_out = ssl.MemoryBIO()
    client = client_context.wrap_bio(
        client_in,
        client_out,
        server_side=False,
        server_hostname="127.0.0.1",
    )
    server = server_context.wrap_bio(server_in, server_out, server_side=True)
    client_done = False
    server_done = False
    for _ in range(100):
        if not client_done:
            try:
                client.do_handshake()
                client_done = True
            except ssl.SSLWantReadError:
                pass
        client_bytes = client_out.read()
        if client_bytes:
            server_in.write(client_bytes)
        if not server_done:
            try:
                server.do_handshake()
                server_done = True
            except ssl.SSLWantReadError:
                pass
        server_bytes = server_out.read()
        if server_bytes:
            client_in.write(server_bytes)
        if client_done and server_done:
            return
    raise AssertionError("loopback TLS handshake did not complete")


def _run_powershell(script: str, *, root: Path) -> dict[str, object]:
    environment = os.environ.copy()
    environment["EVELYN_PROVISIONER_UNDER_TEST"] = str(PROVISIONER)
    environment["EVELYN_PROVISIONER_TEST_ROOT"] = str(root)
    result = subprocess.run(
        ["pwsh", "-NoProfile", "-Command", script],
        cwd=REPO_ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return json.loads(result.stdout)


class ConversationArchiveProvisionerContractTests(unittest.TestCase):
    def test_fixed_entrypoint_preserves_attestation_boundary(self) -> None:
        source = PROVISIONER.read_text(encoding="utf-8")
        entrypoint = ENTRYPOINT.read_text(encoding="utf-8")

        for required in (
            r"C:\ProgramData\Evelyn\private-audit",
            r"D:\EvelynBackup\private-audit",
            r"C:\ProgramData\Evelyn\private-audit-anchor",
            r"C:\ProgramData\Evelyn\private-audit-secrets",
            "-Verb RunAs",
            "-WindowStyle Hidden",
            "ExpectedAdminSid",
            "ExpectedAdminAccount",
            "Get-Volume",
            "Get-Disk",
            "Get-BitLockerVolume",
            "FullyEncrypted",
            "SetAccessRuleProtection($true, $false)",
            "RandomNumberGenerator]::Fill",
            "CertificateRequest",
            "AddIpAddress([Net.IPAddress]::Parse('127.0.0.1'))",
            "AddIpAddress([Net.IPAddress]::Parse('::1'))",
            "ExportPkcs8PrivateKey",
            "[IO.FileMode]::CreateNew",
            "$stream.Flush($true)",
            "[IO.Directory]::Move($staging, $Target)",
            "conversation_archive.test-provision-owner.v1",
            "Undo-EvelynArchiveProvisionCreation",
            "archive_provision_services_running",
        ):
            self.assertIn(required, source)
        for key_name in (
            "auth.key",
            "ingest.key",
            "user-view.key",
            "proxy.key",
            "minecraft.key",
        ):
            self.assertEqual(source.count(f"'{key_name}'"), 1)
        main = source.split("if ($LibraryOnly)", 1)[1]
        self.assertLess(
            main.index("Assert-EvelynArchiveVolumesReady"),
            main.index("$result = Initialize-EvelynArchiveTestProvision"),
        )
        self.assertNotIn("Write-Host", source)
        self.assertNotIn("Start-EvelynConversationArchiveAdmin.ps1", source)
        self.assertIn("Initialize-EvelynConversationArchiveTest.ps1", entrypoint)
        self.assertIn("Get-Command pwsh.exe", entrypoint)
        self.assertIn("@args", entrypoint)

    def test_powershell_ast_is_valid(self) -> None:
        script = textwrap.dedent(
            r"""
            $tokens = $null
            $errors = $null
            $null = [Management.Automation.Language.Parser]::ParseFile(
                $env:EVELYN_PROVISIONER_UNDER_TEST,
                [ref]$tokens,
                [ref]$errors
            )
            [pscustomobject]@{ errors = @($errors).Count } |
                ConvertTo-Json -Compress
            """
        )
        with tempfile.TemporaryDirectory() as temporary:
            payload = _run_powershell(script, root=Path(temporary))
        self.assertEqual(payload, {"errors": 0})

    def test_wrapper_forwards_named_arguments_from_windows_powershell(self) -> None:
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(ENTRYPOINT),
                "-LibraryOnly",
            ],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

        self.assertEqual(result.stdout, "")

    def test_temp_provision_is_idempotent_and_keeps_key_tls_material(self) -> None:
        script = textwrap.dedent(
            r"""
            . $env:EVELYN_PROVISIONER_UNDER_TEST -LibraryOnly
            $base = $env:EVELYN_PROVISIONER_TEST_ROOT
            $roots = [ordered]@{
                primary = Join-Path $base 'c/private-audit'
                replica = Join-Path $base 'd/private-audit'
                anchor = Join-Path $base 'c/private-audit-anchor'
                secrets = Join-Path $base 'c/private-audit-secrets'
            }
            $null = New-Item -ItemType Directory -Path (Join-Path $base 'c')
            $null = New-Item -ItemType Directory -Path (Join-Path $base 'd')
            $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
            $first = Initialize-EvelynArchiveTestProvision -Roots $roots -AdminSid $sid
            $names = @(
                'auth.key', 'ingest.key', 'user-view.key', 'proxy.key',
                'minecraft.key', 'control-page-cert.pem', 'control-page-key.pem'
            )
            $before = @{}
            foreach ($name in $names) {
                $before[$name] = (Get-FileHash -Algorithm SHA256 -LiteralPath (
                    Join-Path $roots.secrets $name
                )).Hash
            }
            $second = Initialize-EvelynArchiveTestProvision -Roots $roots -AdminSid $sid
            $after = @{}
            foreach ($name in $names) {
                $after[$name] = (Get-FileHash -Algorithm SHA256 -LiteralPath (
                    Join-Path $roots.secrets $name
                )).Hash
            }
            $keyHashes = @($names[0..4] | ForEach-Object { $after[$_] })
            [pscustomobject]@{
                firstCreated = $first.Created
                firstReused = $first.Reused
                secondCreated = $second.Created
                secondReused = $second.Reused
                sameInstall = $first.InstallId -ceq $second.InstallId
                unchanged = @($names | Where-Object {
                    $before[$_] -cne $after[$_]
                }).Count -eq 0
                independentKeys = @($keyHashes | Select-Object -Unique).Count -eq 5
            } | ConvertTo-Json -Compress
            """
        )
        with tempfile.TemporaryDirectory() as temporary:
            payload = _run_powershell(script, root=Path(temporary))
            secrets = Path(temporary) / "c" / "private-audit-secrets"
            _assert_loopback_tls_handshake(
                secrets / "control-page-cert.pem",
                secrets / "control-page-key.pem",
            )
        self.assertEqual(
            payload,
            {
                "firstCreated": 4,
                "firstReused": 0,
                "secondCreated": 0,
                "secondReused": 4,
                "sameInstall": True,
                "unchanged": True,
                "independentKeys": True,
            },
        )

    def test_partial_owned_install_resumes_with_the_same_marker(self) -> None:
        script = textwrap.dedent(
            r"""
            . $env:EVELYN_PROVISIONER_UNDER_TEST -LibraryOnly
            $base = $env:EVELYN_PROVISIONER_TEST_ROOT
            $roots = [ordered]@{
                primary = Join-Path $base 'c/private-audit'
                replica = Join-Path $base 'd/private-audit'
                anchor = Join-Path $base 'c/private-audit-anchor'
                secrets = Join-Path $base 'c/private-audit-secrets'
            }
            $null = New-Item -ItemType Directory -Path (Join-Path $base 'c')
            $null = New-Item -ItemType Directory -Path (Join-Path $base 'd')
            $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
            $id = [Guid]::NewGuid().ToString('N')
            New-EvelynArchiveProvisionDirectory `
                -Target $roots.primary -AdminSid $sid `
                -ProvisionId $id -Role primary
            New-EvelynArchiveProvisionDirectory `
                -Target $roots.replica -AdminSid $sid `
                -ProvisionId $id -Role replica
            $result = Initialize-EvelynArchiveTestProvision -Roots $roots -AdminSid $sid
            [pscustomobject]@{
                sameInstall = $result.InstallId -ceq $id
                created = $result.Created
                reused = $result.Reused
                allPresent = @($roots.Values | Where-Object {
                    -not (Test-Path -LiteralPath $_ -PathType Container)
                }).Count -eq 0
            } | ConvertTo-Json -Compress
            """
        )
        with tempfile.TemporaryDirectory() as temporary:
            payload = _run_powershell(script, root=Path(temporary))
        self.assertEqual(
            payload,
            {
                "sameInstall": True,
                "created": 2,
                "reused": 2,
                "allPresent": True,
            },
        )

    def test_existing_invalid_key_fails_without_rotation(self) -> None:
        script = textwrap.dedent(
            r"""
            . $env:EVELYN_PROVISIONER_UNDER_TEST -LibraryOnly
            $base = $env:EVELYN_PROVISIONER_TEST_ROOT
            $roots = [ordered]@{
                primary = Join-Path $base 'c/private-audit'
                replica = Join-Path $base 'd/private-audit'
                anchor = Join-Path $base 'c/private-audit-anchor'
                secrets = Join-Path $base 'c/private-audit-secrets'
            }
            $null = New-Item -ItemType Directory -Path (Join-Path $base 'c')
            $null = New-Item -ItemType Directory -Path (Join-Path $base 'd')
            $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
            $null = Initialize-EvelynArchiveTestProvision -Roots $roots -AdminSid $sid
            $other = Join-Path $roots.secrets 'ingest.key'
            $otherBefore = (Get-FileHash -Algorithm SHA256 -LiteralPath $other).Hash
            [IO.File]::WriteAllBytes(
                (Join-Path $roots.secrets 'auth.key'),
                [byte[]]@(1, 2, 3)
            )
            $failed = $false
            try {
                $null = Initialize-EvelynArchiveTestProvision -Roots $roots -AdminSid $sid
            }
            catch {
                $failed = $true
            }
            [pscustomobject]@{
                failed = $failed
                malformedPreserved = (Get-Item -LiteralPath (
                    Join-Path $roots.secrets 'auth.key'
                )).Length -eq 3
                otherUnchanged = (Get-FileHash -Algorithm SHA256 -LiteralPath $other).Hash -ceq $otherBefore
            } | ConvertTo-Json -Compress
            """
        )
        with tempfile.TemporaryDirectory() as temporary:
            payload = _run_powershell(script, root=Path(temporary))
        self.assertEqual(
            payload,
            {
                "failed": True,
                "malformedPreserved": True,
                "otherUnchanged": True,
            },
        )

    def test_unowned_existing_root_fails_before_creating_missing_roots(self) -> None:
        script = textwrap.dedent(
            r"""
            . $env:EVELYN_PROVISIONER_UNDER_TEST -LibraryOnly
            $base = $env:EVELYN_PROVISIONER_TEST_ROOT
            $roots = [ordered]@{
                primary = Join-Path $base 'c/private-audit'
                replica = Join-Path $base 'd/private-audit'
                anchor = Join-Path $base 'c/private-audit-anchor'
                secrets = Join-Path $base 'c/private-audit-secrets'
            }
            $null = New-Item -ItemType Directory -Path $roots.primary -Force
            $null = New-Item -ItemType Directory -Path (Join-Path $base 'd')
            $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
            $failed = $false
            try {
                $null = Initialize-EvelynArchiveTestProvision -Roots $roots -AdminSid $sid
            }
            catch {
                $failed = $true
            }
            [pscustomobject]@{
                failed = $failed
                primaryPreserved = Test-Path -LiteralPath $roots.primary
                replicaCreated = Test-Path -LiteralPath $roots.replica
                anchorCreated = Test-Path -LiteralPath $roots.anchor
                secretsCreated = Test-Path -LiteralPath $roots.secrets
            } | ConvertTo-Json -Compress
            """
        )
        with tempfile.TemporaryDirectory() as temporary:
            payload = _run_powershell(script, root=Path(temporary))
        self.assertEqual(
            payload,
            {
                "failed": True,
                "primaryPreserved": True,
                "replicaCreated": False,
                "anchorCreated": False,
                "secretsCreated": False,
            },
        )

    def test_rollback_removes_only_targets_created_by_this_run(self) -> None:
        script = textwrap.dedent(
            r"""
            . $env:EVELYN_PROVISIONER_UNDER_TEST -LibraryOnly
            $base = $env:EVELYN_PROVISIONER_TEST_ROOT
            $parent = Join-Path $base 'owned-parent'
            $null = New-Item -ItemType Directory -Path $parent
            $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
            $targets = [Collections.Generic.List[string]]::new()
            $parents = [Collections.Generic.List[string]]::new()
            $first = Join-Path $parent 'first'
            $second = Join-Path $parent 'second'
            $id = [Guid]::NewGuid().ToString('N')
            New-EvelynArchiveProvisionDirectory `
                -Target $first -AdminSid $sid -ProvisionId $id -Role primary
            $targets.Add($first)
            New-EvelynArchiveProvisionDirectory `
                -Target $second -AdminSid $sid -ProvisionId $id -Role replica
            $targets.Add($second)
            Undo-EvelynArchiveProvisionCreation `
                -CreatedTargets $targets -CreatedParents $parents
            [pscustomobject]@{
                firstRemoved = -not (Test-Path -LiteralPath $first)
                secondRemoved = -not (Test-Path -LiteralPath $second)
                preexistingParentPreserved = Test-Path -LiteralPath $parent
            } | ConvertTo-Json -Compress
            """
        )
        with tempfile.TemporaryDirectory() as temporary:
            payload = _run_powershell(script, root=Path(temporary))
        self.assertEqual(
            payload,
            {
                "firstRemoved": True,
                "secondRemoved": True,
                "preexistingParentPreserved": True,
            },
        )


if __name__ == "__main__":
    unittest.main()
