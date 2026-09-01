from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = next(
    path for path in Path(__file__).resolve().parents if (path / "main.py").exists()
)
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core import conversation_archive_admin as admin  # noqa: E402


ATTESTATION_KEY = b"attestation-key-for-tests-is-at-least-32-bytes"
AUTH_KEY = b"authorization-key-for-tests-is-at-least-32-bytes"
ADMIN_SID = "S-1-5-21-111-222-333-1001"
ADMIN_ACCOUNT = r"EVELYN\LocalAdmin"
DISCORD_USER_ID = "123456789012345678"


class FakeClock:
    def __init__(self, value: int = 1_000) -> None:
        self.value = value

    def __call__(self) -> float:
        return float(self.value)


def volume(role: str) -> dict[str, object]:
    primary = role == "primary"
    on_primary_volume = role in {"primary", "anchor"}
    return {
        "role": role,
        "driveLetter": "C:" if on_primary_volume else "D:",
        "volumeId": "volume-c" if on_primary_volume else "volume-d",
        "diskId": "disk-0" if on_primary_volume else "disk-1",
        "driveType": "Fixed",
        "fileSystem": "NTFS",
        "healthStatus": "Healthy",
        "bitLockerProtectionStatus": "On",
        "bitLockerVolumeStatus": "FullyEncrypted",
        "lockStatus": "Unlocked",
        "ownerSid": ADMIN_SID,
        "mountNonce": (
            "CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC"
            if on_primary_volume
            else "DDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDD"
        ),
        "archivePath": (
            r"C:\ProgramData\Evelyn\private-audit-anchor"
            if role == "anchor"
            else (
                r"C:\ProgramData\Evelyn\private-audit"
                if primary
                else r"D:\EvelynBackup\private-audit"
            )
        ),
        "pathExists": True,
        "pathHasReparsePoint": False,
        "daclProtected": True,
        "nonAdminWriteDenied": True,
    }


def attestation(
    *, nonce: str = "abcdefghijklmnopqrstuvwxyzABCDE_1234567890abc", issued_at: int = 1_000
) -> dict[str, object]:
    unsigned = {
        "schema": admin.ADMIN_ATTESTATION_SCHEMA,
        "purpose": admin.ADMIN_ATTESTATION_PURPOSE,
        "adminSid": ADMIN_SID,
        "adminAccount": ADMIN_ACCOUNT,
        "registeredDiscordUserId": DISCORD_USER_ID,
        "hostId": "EVELYN-HOST",
        "bootId": "2026-08-28T00:00:00.0000000Z",
        "bootstrapNonce": nonce,
        "issuedAt": issued_at,
        "expiresAt": issued_at + 60,
        "elevated": True,
        "administratorMember": True,
        "primary": volume("primary"),
        "replica": volume("replica"),
        "anchor": volume("anchor"),
    }
    return admin.sign_host_attestation(unsigned, signing_key=ATTESTATION_KEY)


def resign(payload: dict[str, object]) -> dict[str, object]:
    unsigned = copy.deepcopy(payload)
    unsigned.pop("authTag", None)
    return admin.sign_host_attestation(unsigned, signing_key=ATTESTATION_KEY)


def host_session_marker(
    *,
    nonce: str = "abcdefghijklmnopqrstuvwxyzABCDE_1234567890abc",
    updated_at: int = 1_000,
    state: str = "active",
) -> dict[str, object]:
    return admin.sign_host_session_marker(
        {
            "schema": admin.ADMIN_HOST_SESSION_SCHEMA,
            "purpose": admin.ADMIN_HOST_SESSION_PURPOSE,
            "adminSid": ADMIN_SID,
            "hostId": "EVELYN-HOST",
            "bootId": "2026-08-28T00:00:00.0000000Z",
            "bootstrapNonce": nonce,
            "state": state,
            "updatedAt": updated_at,
            "expiresAt": updated_at + 300,
        },
        signing_key=ATTESTATION_KEY,
    )


def local_request(**overrides: str) -> admin.LoopbackRequestEvidence:
    values = {
        "scheme": "https",
        "host": "127.0.0.1:8799",
        "origin": "https://127.0.0.1:8799",
        "surface": "local_control_page",
    }
    values.update(overrides)
    return admin.LoopbackRequestEvidence(**values)


class HostAttestationTests(unittest.TestCase):
    def verify(self, payload: dict[str, object], *, now: int = 1_000) -> None:
        admin.verify_host_attestation(
            payload,
            signing_key=ATTESTATION_KEY,
            expected_admin_sid=ADMIN_SID,
            expected_admin_account=ADMIN_ACCOUNT.lower(),
            expected_registered_discord_user_id=DISCORD_USER_ID,
            expected_host_id="evelyn-host",
            now=now,
        )

    def test_valid_elevated_bound_host_and_two_protected_volumes_pass(self) -> None:
        self.verify(attestation())

    def test_signature_expiry_and_identity_mismatch_fail_closed(self) -> None:
        forged = attestation()
        forged["hostId"] = "OTHER-HOST"
        with self.assertRaisesRegex(
            admin.AdminSecurityError, "admin_host_attestation_invalid"
        ):
            self.verify(forged)

        with self.assertRaisesRegex(
            admin.AdminSecurityError, "admin_host_attestation_expired"
        ):
            self.verify(attestation(), now=1_061)

        not_elevated = attestation()
        not_elevated["elevated"] = False
        not_elevated = resign(not_elevated)
        with self.assertRaisesRegex(
            admin.AdminSecurityError, "admin_identity_mismatch"
        ):
            self.verify(not_elevated)

        wrong_account = resign({**attestation(), "adminAccount": "EVELYN\\Other"})
        with self.assertRaisesRegex(
            admin.AdminSecurityError, "admin_identity_mismatch"
        ):
            self.verify(wrong_account)

        wrong_discord = resign(
            {**attestation(), "registeredDiscordUserId": "999999999999999999"}
        )
        with self.assertRaisesRegex(
            admin.AdminSecurityError, "admin_identity_mismatch"
        ):
            self.verify(wrong_discord)

    def test_storage_requires_distinct_fixed_ntfs_healthy_bitlocker_volumes(self) -> None:
        cases = []
        same_volume = copy.deepcopy(attestation())
        same_volume["replica"]["volumeId"] = same_volume["primary"]["volumeId"]
        cases.append(resign(same_volume))

        same_disk = copy.deepcopy(attestation())
        same_disk["replica"]["diskId"] = same_disk["primary"]["diskId"]
        cases.append(resign(same_disk))

        bitlocker_off = copy.deepcopy(attestation())
        bitlocker_off["replica"]["bitLockerProtectionStatus"] = "Off"
        cases.append(resign(bitlocker_off))

        unhealthy = copy.deepcopy(attestation())
        unhealthy["primary"]["healthStatus"] = "Warning"
        cases.append(resign(unhealthy))

        writable = copy.deepcopy(attestation())
        writable["primary"]["nonAdminWriteDenied"] = False
        cases.append(resign(writable))

        reparse = copy.deepcopy(attestation())
        reparse["replica"]["pathHasReparsePoint"] = True
        cases.append(resign(reparse))

        untrusted_owner = copy.deepcopy(attestation())
        untrusted_owner["primary"]["ownerSid"] = "S-1-5-21-9-9-9-1002"
        cases.append(resign(untrusted_owner))

        invalid_mount_binding = copy.deepcopy(attestation())
        invalid_mount_binding["replica"]["mountNonce"] = "not a nonce"
        cases.append(resign(invalid_mount_binding))

        anchor_reparse = copy.deepcopy(attestation())
        anchor_reparse["anchor"]["pathHasReparsePoint"] = True
        cases.append(resign(anchor_reparse))

        anchor_wrong_volume = copy.deepcopy(attestation())
        anchor_wrong_volume["anchor"]["volumeId"] = "volume-elsewhere"
        cases.append(resign(anchor_wrong_volume))

        for payload in cases:
            with self.subTest(payload=payload["authTag"][:8]):
                with self.assertRaisesRegex(
                    admin.AdminSecurityError,
                    "admin_storage_preflight_failed",
                ):
                    self.verify(payload)


class AdminAuthenticationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.state_path = Path(self.temporary.name) / "admin-auth.json"
        self.clock = FakeClock()
        self.auth = self.make_auth()

    def make_auth(
        self, *, host_session_state_path: Path | None = None
    ) -> admin.ConversationArchiveAdminAuth:
        return admin.ConversationArchiveAdminAuth(
            state_path=self.state_path,
            authentication_key=AUTH_KEY,
            attestation_key=ATTESTATION_KEY,
            expected_admin_sid=ADMIN_SID,
            expected_admin_account=ADMIN_ACCOUNT,
            registered_discord_user_id=DISCORD_USER_ID,
            expected_host_id="EVELYN-HOST",
            host_session_state_path=host_session_state_path,
            now=self.clock,
        )

    def _write_host_session_marker(self, payload: dict[str, object]) -> Path:
        path = Path(self.temporary.name) / "host-session.json"
        path.write_text(
            json.dumps(payload, separators=(",", ":")), encoding="utf-8"
        )
        return path

    def test_otp_is_exact_case_sensitive_one_use_and_never_persisted(self) -> None:
        with patch.object(admin.secrets, "choice", side_effect=list("aB3z")):
            delivery = self.auth.begin_admin_login(attestation())
        self.assertEqual(delivery.code, "aB3z")
        self.assertEqual(len(delivery.code), 4)
        self.assertNotIn(delivery.code, repr(delivery))
        self.assertNotIn(DISCORD_USER_ID, repr(delivery))
        self.assertNotIn(delivery.challenge_id, repr(delivery))
        state_text = self.state_path.read_text(encoding="utf-8")
        self.assertNotIn(delivery.code, state_text)
        self.assertNotIn(DISCORD_USER_ID, state_text)
        self.assertNotIn(ADMIN_SID, state_text)
        self.assertNotIn(ADMIN_ACCOUNT, state_text)

        with self.assertRaisesRegex(
            admin.AdminSecurityError, "admin_otp_invalid"
        ):
            self.auth.complete_admin_login(
                challenge_id=delivery.challenge_id,
                code="Ab3z",
                request=local_request(),
            )
        grant = self.auth.complete_admin_login(
            challenge_id=delivery.challenge_id,
            code="aB3z",
            request=local_request(),
        )
        self.assertNotIn(grant.token, repr(grant))
        self.assertNotIn(
            grant.token, self.state_path.read_text(encoding="utf-8")
        )
        self.assertEqual(grant.cookie_name, "__Host-evelyn_archive_admin")
        self.assertEqual(
            grant.cookie_attributes,
            "Secure; HttpOnly; SameSite=Strict; Path=/",
        )
        with self.assertRaisesRegex(
            admin.AdminSecurityError, "admin_otp_expired"
        ):
            self.auth.complete_admin_login(
                challenge_id=delivery.challenge_id,
                code="aB3z",
                request=local_request(),
            )

    def test_three_failures_destroy_challenge(self) -> None:
        with patch.object(admin.secrets, "choice", side_effect=list("aB3z")):
            delivery = self.auth.begin_admin_login(attestation())
        for _ in range(3):
            with self.assertRaisesRegex(
                admin.AdminSecurityError, "admin_otp_invalid"
            ):
                self.auth.complete_admin_login(
                    challenge_id=delivery.challenge_id,
                    code="zzzz",
                    request=local_request(),
                )
        with self.assertRaisesRegex(
            admin.AdminSecurityError, "admin_otp_expired"
        ):
            self.auth.complete_admin_login(
                challenge_id=delivery.challenge_id,
                code="aB3z",
                request=local_request(),
            )

    def test_discord_or_non_https_request_cannot_open_admin_session(self) -> None:
        delivery = self.auth.begin_admin_login(attestation())
        rejected = (
            local_request(surface="discord"),
            local_request(scheme="http", origin="http://127.0.0.1:8799"),
            local_request(host="evil.example:8799", origin="https://evil.example:8799"),
            local_request(origin="https://127.0.0.1:8800"),
        )
        for request in rejected:
            with self.subTest(request=request):
                with self.assertRaisesRegex(
                    admin.AdminSecurityError, "admin_loopback_required"
                ):
                    self.auth.complete_admin_login(
                        challenge_id=delivery.challenge_id,
                        code=delivery.code,
                        request=request,
                    )
        grant = self.auth.complete_admin_login(
            challenge_id=delivery.challenge_id,
            code=delivery.code,
            request=local_request(),
        )
        self.auth.require_admin_session(
            token=grant.token, request=local_request()
        )
        with self.assertRaisesRegex(
            admin.AdminSecurityError, "admin_loopback_required"
        ):
            self.auth.require_admin_session(
                token=grant.token,
                request=local_request(surface="discord"),
            )

    def test_session_has_five_minute_absolute_and_two_minute_idle_limit(self) -> None:
        delivery = self.auth.begin_admin_login(attestation())
        grant = self.auth.complete_admin_login(
            challenge_id=delivery.challenge_id,
            code=delivery.code,
            request=local_request(),
        )
        self.assertEqual(grant.expires_at, self.clock.value + 300)
        self.clock.value += 119
        claims = self.auth.require_admin_session(
            token=grant.token, request=local_request()
        )
        self.assertEqual(claims.capability, admin.ADMIN_CONTROL_CAPABILITY)
        self.clock.value += 121
        with self.assertRaisesRegex(
            admin.AdminSecurityError, "admin_session_expired"
        ):
            self.auth.require_admin_session(
                token=grant.token, request=local_request()
            )

    def test_host_lock_marker_revokes_pending_challenge_and_live_session(self) -> None:
        marker_path = self._write_host_session_marker(host_session_marker())
        guarded = self.make_auth(host_session_state_path=marker_path)
        delivery = guarded.begin_admin_login(attestation())
        self._write_host_session_marker(host_session_marker(state="revoked"))
        with self.assertRaisesRegex(
            admin.AdminSecurityError, "admin_host_session_invalid"
        ):
            guarded.complete_admin_login(
                challenge_id=delivery.challenge_id,
                code=delivery.code,
                request=local_request(),
            )

        second_nonce = "BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"
        self._write_host_session_marker(
            host_session_marker(nonce=second_nonce)
        )
        second = guarded.begin_admin_login(attestation(nonce=second_nonce))
        grant = guarded.complete_admin_login(
            challenge_id=second.challenge_id,
            code=second.code,
            request=local_request(),
        )
        self._write_host_session_marker(
            host_session_marker(nonce=second_nonce, state="revoked")
        )
        with self.assertRaisesRegex(
            admin.AdminSecurityError, "admin_host_session_invalid"
        ):
            guarded.require_admin_session(
                token=grant.token,
                request=local_request(),
            )

    def test_host_session_heartbeat_is_exact_fresh_and_attestation_bound(self) -> None:
        marker_path = self._write_host_session_marker(
            host_session_marker(updated_at=self.clock.value - 16)
        )
        guarded = self.make_auth(host_session_state_path=marker_path)
        with self.assertRaisesRegex(
            admin.AdminSecurityError, "admin_host_session_invalid"
        ):
            guarded.begin_admin_login(attestation())

        self._write_host_session_marker(
            host_session_marker(nonce="Z" * 43)
        )
        with self.assertRaisesRegex(
            admin.AdminSecurityError, "admin_host_session_invalid"
        ):
            guarded.begin_admin_login(
                attestation(
                    nonce="CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC"
                )
            )

    def test_process_restart_revokes_outstanding_otp_and_admin_session(self) -> None:
        delivery = self.auth.begin_admin_login(attestation())
        restarted = self.make_auth()

        with self.assertRaisesRegex(
            admin.AdminSecurityError, "admin_otp_expired"
        ):
            restarted.complete_admin_login(
                challenge_id=delivery.challenge_id,
                code=delivery.code,
                request=local_request(),
            )

        fresh_delivery = restarted.begin_admin_login(
            attestation(nonce="B" * 32)
        )
        grant = restarted.complete_admin_login(
            challenge_id=fresh_delivery.challenge_id,
            code=fresh_delivery.code,
            request=local_request(),
        )
        restarted_again = self.make_auth()
        with self.assertRaisesRegex(
            admin.AdminSecurityError, "admin_session_expired"
        ):
            restarted_again.require_admin_session(
                token=grant.token,
                request=local_request(),
            )

    def test_attestation_replay_and_persistent_issue_rate_limit_are_blocked(self) -> None:
        first = attestation(nonce="AAAAAAAAAAAAAAAAAAAAAA_1234567890abc")
        first_delivery = self.auth.begin_admin_login(first)
        with self.assertRaisesRegex(
            admin.AdminSecurityError, "admin_host_attestation_replayed"
        ):
            self.auth.begin_admin_login(first)

        for index in (2, 3):
            newer = self.auth.begin_admin_login(
                attestation(
                    nonce=f"BBBBBBBBBBBBBBBBBBBBBB_{index:021d}"
                )
            )
            if index == 2:
                with self.assertRaisesRegex(
                    admin.AdminSecurityError, "admin_otp_expired"
                ):
                    self.auth.complete_admin_login(
                        challenge_id=first_delivery.challenge_id,
                        code=first_delivery.code,
                        request=local_request(),
                    )
            self.assertTrue(newer.code)
        restarted = self.make_auth()
        with self.assertRaisesRegex(
            admin.AdminSecurityError, "admin_auth_rate_limited"
        ):
            restarted.begin_admin_login(
                attestation(nonce="CCCCCCCCCCCCCCCCCCCCCC_1234567890abc")
            )

    def test_step_up_codes_share_the_persistent_issue_rate_limit(self) -> None:
        delivery = self.auth.begin_admin_login(attestation())
        grant = self.auth.complete_admin_login(
            challenge_id=delivery.challenge_id,
            code=delivery.code,
            request=local_request(),
        )
        for _ in range(2):
            self.auth.register_step_up_issue(
                token=grant.token,
                request=local_request(),
            )
        with self.assertRaisesRegex(
            admin.AdminSecurityError, "admin_auth_rate_limited"
        ):
            self.auth.register_step_up_issue(
                token=grant.token,
                request=local_request(),
            )

    def test_tampered_persistent_state_fails_closed(self) -> None:
        delivery = self.auth.begin_admin_login(attestation())
        envelope = json.loads(self.state_path.read_text(encoding="utf-8"))
        envelope["body"]["challenges"][delivery.challenge_id]["attempts"] = 2
        self.state_path.write_text(json.dumps(envelope), encoding="utf-8")
        with self.assertRaisesRegex(
            admin.AdminSecurityError, "admin_auth_state_invalid"
        ):
            self.make_auth().begin_admin_login(
                attestation(nonce="DDDDDDDDDDDDDDDDDDDDDD_1234567890abc")
            )

    def test_public_statuses_never_project_identifiers_paths_or_tokens(self) -> None:
        delivery = self.auth.begin_admin_login(attestation())
        projections = [
            delivery.public_projection(),
            admin.AdminSecurityError(
                "admin_storage_preflight_failed", retryable=False
            ).public_projection(),
            admin.AdminSecurityError(
                "admin_auth_rate_limited",
                retryable=True,
                retry_after_sec=30,
            ).public_projection(),
        ]
        rendered = json.dumps(projections, ensure_ascii=False)
        for secret in (
            delivery.code,
            delivery.challenge_id,
            DISCORD_USER_ID,
            ADMIN_SID,
            ADMIN_ACCOUNT,
            r"C:\ProgramData\Evelyn\private-audit",
            r"D:\EvelynBackup\private-audit",
        ):
            self.assertNotIn(secret, rendered)


class HostLauncherContractTests(unittest.TestCase):
    def test_launcher_is_elevated_one_shot_and_reports_content_free_status(self) -> None:
        source = (
            REPO_ROOT / "scripts" / "Start-EvelynConversationArchiveAdmin.ps1"
        ).read_text(encoding="utf-8")
        for required in (
            "-Verb RunAs",
            "Test-ElevatedAdministrator",
            "ExpectedAdminSid",
            "ExpectedAdminAccount",
            "RegisteredDiscordUserId",
            "Get-Volume",
            "Get-Disk",
            "Get-BitLockerVolume",
            "FullyEncrypted",
            "Test-ReparsePath",
            "Test-NonAdminWriteDenied",
            "IngestKeyPath",
            "UserViewKeyPath",
            "ProxyKeyPath",
            "MinecraftKeyPath",
            "#archive-bootstrap=",
            "HMACSHA256",
            "host_attestation_ready",
            "conversation_archive.admin-host-session.v1",
            "SessionSwitch",
            "SessionLock",
            "SessionLogoff",
            "RemoteDisconnect",
            "HostSessionStatePath",
        ):
            self.assertIn(required, source)
        self.assertIn("$allowedWriterSids -cnotcontains $sid", source)
        self.assertIn("$item -is [IO.DirectoryInfo]", source)
        self.assertIn("$item.Directory", source)
        self.assertIn(
            "Test-NonAdminWriteDenied $bindingAcl $ExpectedAdminSid", source
        )
        self.assertIn(
            "Test-NonAdminWriteDenied $attestationAcl $ExpectedAdminSid", source
        )
        self.assertIn("-WindowStyle Hidden", source)
        self.assertIn("$controlPagePort -gt 65535", source)
        self.assertIn("$controlPageUri.DnsSafeHost", source)
        self.assertIn(
            "[string]$ControlPageUrl = 'https://127.0.0.1:8800/archive/admin'",
            source,
        )
        self.assertIn("$controlPageUri.AbsolutePath -cne '/archive/admin'", source)
        self.assertIn("archive_control_page_url_invalid", source)
        self.assertIn("-WindowStyle Hidden", source)
        self.assertIn("Set-HostSessionMarker", source)
        self.assertIn("$stream.Flush($true)", source)
        self.assertIn("[Array]::Clear($key, 0, $key.Length)", source)
        self.assertNotIn(
            "[ValidatePattern('^[A-Za-z0-9_-]{22,128}$')]\n    [string]$HostSessionNonce",
            source,
        )
        self.assertNotIn("$broadWriterSids", source)
        self.assertNotIn("otp", source.casefold())
        self.assertNotIn("Write-Host", source)
        self.assertNotIn("ConvertTo-Json -Depth 5\n    Write", source)

        self.assertIn("ValidationAttestationOnly", source)
        self.assertIn("conversation_archive.validation-identity.v1", source)
        self.assertIn("[Console]::In.ReadLine()", source)
        self.assertIn("Set-ValidationIdentityFileAcl", source)
        self.assertIn("Import-ProtectedValidationIdentityFile", source)
        identity_writer = source[
            source.index("function New-ProtectedValidationIdentityFile") :
            source.index("function Import-ProtectedValidationIdentityFile")
        ]
        self.assertIn("catch {", identity_writer)
        self.assertIn("[IO.File]::Delete($path)", identity_writer)
        self.assertIn("archive_validation_admin_state_conflict", source)
        self.assertIn("host_validation_attestation_ready", source)
        validation_arguments = source[
            source.index("if ($ValidationAttestationOnly) {") :
            source.index("else {", source.index("if ($ValidationAttestationOnly) {"))
        ]
        self.assertIn("-ValidationIdentityInputPath", validation_arguments)
        self.assertNotIn("-ExpectedAdminSid", validation_arguments)
        self.assertNotIn("-ExpectedAdminAccount", validation_arguments)
        self.assertNotIn("-RegisteredDiscordUserId", validation_arguments)
        production_arguments = source[
            source.index("else {", source.index("if ($ValidationAttestationOnly) {")) :
            source.index("'-PrimaryArchivePath'", source.index("else {", source.index("if ($ValidationAttestationOnly) {")))
        ]
        for identity_argument in (
            "-ExpectedAdminSid",
            "-ExpectedAdminAccount",
            "-RegisteredDiscordUserId",
        ):
            self.assertIn(identity_argument, production_arguments)
        marker_write = source.index("Set-HostSessionMarker", source.index("$hostSessionExpiresAt"))
        watcher_start = source.index("Start-HostSessionWatcher", marker_write)
        self.assertLess(marker_write, watcher_start)

        planned_entrypoint = (
            REPO_ROOT / "tools" / "evelyn_private_archive_host.ps1"
        ).read_text(encoding="utf-8")
        self.assertIn("Start-EvelynConversationArchiveAdmin.ps1", planned_entrypoint)
        self.assertIn("@args", planned_entrypoint)
        self.assertNotIn("param()", planned_entrypoint)

    @unittest.skipUnless(
        os.name == "nt" and shutil.which("pwsh"),
        "PowerShell 7 is required",
    )
    def test_validation_identity_parser_is_exact_and_content_stays_off_argv(self) -> None:
        launcher = REPO_ROOT / "scripts" / "Start-EvelynConversationArchiveAdmin.ps1"
        environment = os.environ.copy()
        environment["EVELYN_HOST_LAUNCHER_UNDER_TEST"] = str(launcher)
        command = r"""
$tokens = $null
$errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile(
    $env:EVELYN_HOST_LAUNCHER_UNDER_TEST,
    [ref]$tokens,
    [ref]$errors
)
$function = $ast.Find({
    param($node)
    $node -is [Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -ceq 'Read-ValidationIdentityJson'
}, $true)
. ([scriptblock]::Create($function.Extent.Text))
$valid = @{
    schema = 'conversation_archive.validation-identity.v1'
    adminSid = 'S-1-5-21-1-2-3-1001'
    adminAccount = 'HOST\Admin'
    discordUserId = '12345'
    runId = ('a' * 32)
    attestationNonce = ('B' * 32)
} | ConvertTo-Json -Compress
Read-ValidationIdentityJson -Json $valid
$invalid = $valid | ConvertFrom-Json
$invalid | Add-Member NoteProperty unexpected $true
$rejected = ''
try {
    Read-ValidationIdentityJson -Json ($invalid | ConvertTo-Json -Compress)
} catch { $rejected = $_.Exception.Message }
[pscustomobject]@{
    parseErrors = @($errors).Count
    sid = $ExpectedAdminSid
    runId = $validationRunId
    nonce = $validationNonce
    rejected = $rejected
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
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["parseErrors"], 0)
        self.assertEqual(payload["sid"], "S-1-5-21-1-2-3-1001")
        self.assertEqual(payload["runId"], "a" * 32)
        self.assertEqual(payload["nonce"], "B" * 32)
        self.assertEqual(payload["rejected"], "archive_validation_identity_invalid")

    def test_reparse_check_handles_files_in_windows_powershell_51(self) -> None:
        launcher = (
            REPO_ROOT / "scripts" / "Start-EvelynConversationArchiveAdmin.ps1"
        )
        environment = os.environ.copy()
        environment["EVELYN_HOST_LAUNCHER_UNDER_TEST"] = str(launcher)
        command = r"""
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$tokens = $null
$errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile(
    $env:EVELYN_HOST_LAUNCHER_UNDER_TEST,
    [ref]$tokens,
    [ref]$errors
)
$function = $ast.Find(
    {
        param($node)
        $node -is [Management.Automation.Language.FunctionDefinitionAst] -and
            $node.Name -ceq 'Test-ReparsePath'
    },
    $true
)
. ([scriptblock]::Create($function.Extent.Text))
$fileResult = Test-ReparsePath `
    -LiteralPath $env:EVELYN_HOST_LAUNCHER_UNDER_TEST
$directoryResult = Test-ReparsePath `
    -LiteralPath (Split-Path -Parent $env:EVELYN_HOST_LAUNCHER_UNDER_TEST)
[pscustomobject]@{
    major = $PSVersionTable.PSVersion.Major
    parseErrors = @($errors).Count
    fileResult = $fileResult
    directoryResult = $directoryResult
} | ConvertTo-Json -Compress
"""
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", command],
            cwd=REPO_ROOT,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

        self.assertEqual(
            json.loads(result.stdout),
            {
                "major": 5,
                "parseErrors": 0,
                "fileResult": False,
                "directoryResult": False,
            },
        )


if __name__ == "__main__":
    unittest.main()
