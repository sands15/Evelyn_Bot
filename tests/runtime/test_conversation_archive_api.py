from __future__ import annotations

import asyncio
from contextlib import closing
import hashlib
import hmac
import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiohttp.test_utils import TestClient, TestServer


REPO_ROOT = next(
    path for path in Path(__file__).resolve().parents if (path / "main.py").exists()
)
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core import conversation_archive_admin as admin  # noqa: E402
from evelyn_core import conversation_archive as archive_core  # noqa: E402
from evelyn_core import fast_control_api as fast_api  # noqa: E402
from evelyn_core.conversation_archive_purge import (  # noqa: E402
    PurgePass,
    deletion_purge_scope_digest,
)
from evelyn_core.memory_deletion_journal import (  # noqa: E402
    read_memory_deletion_tombstones,
)


AUTH_KEY = b"archive-api-auth-key-is-at-least-thirty-two-bytes"
INGEST_KEY = b"archive-api-ingest-key-is-at-least-thirty-two-bytes"
USER_VIEW_KEY = b"archive-api-user-view-key-is-at-least-thirty-two-bytes"
PROXY_KEY = b"archive-api-proxy-key-is-at-least-thirty-two-bytes"
MINECRAFT_KEY = b"archive-api-minecraft-key-is-at-least-thirty-two-bytes"
ADMIN_SID = "S-1-5-21-111-222-333-1001"
ADMIN_ACCOUNT = r"EVELYN\LocalAdmin"
ADMIN_DISCORD_ID = "123456789012345678"
GUILD_ID = "223456789012345678"
TEXT_CHANNEL_ID = "323456789012345678"
VOICE_CHANNEL_ID = "423456789012345678"
USER_ID = "523456789012345678"
BOOTSTRAP_NONCE = "b" * 43


def _minecraft_body(observed_at: float, **changes: object) -> str:
    event: dict[str, object] = {
        "schema": "conversation.archive.minecraft-result.v1",
        "eventType": "minecraft_result",
        "goalRunId": "goal-run-1",
        "actionRunId": "action-run-1",
        "actionKey": "collect_food",
        "contractCode": "food_collection",
        "candidateSequence": 1,
        "executionSequence": 1,
        "observedAt": observed_at,
        "evidenceCode": "inventory_increased",
        "postconditionCode": "food_reserve_ready",
        "verified": True,
        "succeeded": True,
        "worldChanged": True,
        "goalProgress": True,
        "contentFree": True,
    }
    event.update(changes)
    return json.dumps(
        event,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _volume(role: str, nonce: str) -> dict[str, object]:
    on_c = role in {"primary", "anchor"}
    return {
        "role": role,
        "driveLetter": "C:" if on_c else "D:",
        "volumeId": "volume-c" if on_c else "volume-d",
        "diskId": "disk-c" if on_c else "disk-d",
        "driveType": "Fixed",
        "fileSystem": "NTFS",
        "healthStatus": "Healthy",
        "bitLockerProtectionStatus": "On",
        "bitLockerVolumeStatus": "FullyEncrypted",
        "lockStatus": "Unlocked",
        "ownerSid": ADMIN_SID,
        "mountNonce": nonce,
        "archivePath": (
            r"C:\ProgramData\Evelyn\private-audit-anchor"
            if role == "anchor"
            else (
                r"C:\ProgramData\Evelyn\private-audit"
                if role == "primary"
                else r"D:\EvelynBackup\private-audit"
            )
        ),
        "pathExists": True,
        "pathHasReparsePoint": False,
        "daclProtected": True,
        "nonAdminWriteDenied": True,
    }


class ConversationArchiveApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.primary_dir = root / "primary"
        self.replica_dir = root / "replica"
        self.anchor_dir = root / "anchor"
        for directory in (self.primary_dir, self.replica_dir, self.anchor_dir):
            directory.mkdir()
        self.nonces = {
            "primary": "P" * 43,
            "replica": "R" * 43,
            "anchor": "A" * 43,
        }
        for role, directory in (
            ("primary", self.primary_dir),
            ("replica", self.replica_dir),
            ("anchor", self.anchor_dir),
        ):
            (directory / ".evelyn-volume-binding").write_text(
                self.nonces[role], encoding="ascii"
            )
        self.auth_key_path = root / "auth.key"
        self.ingest_key_path = root / "ingest.key"
        self.user_view_key_path = root / "user-view.key"
        self.proxy_key_path = root / "proxy.key"
        self.minecraft_key_path = root / "minecraft.key"
        self.attestation_path = root / "host-attestation.json"
        self.host_session_state_path = root / "host-session.json"
        self.auth_key_path.write_bytes(AUTH_KEY)
        self.ingest_key_path.write_bytes(INGEST_KEY)
        self.user_view_key_path.write_bytes(USER_VIEW_KEY)
        self.proxy_key_path.write_bytes(PROXY_KEY)
        self.minecraft_key_path.write_bytes(MINECRAFT_KEY)
        self.now = int(time.time())
        self._write_attestation(self.now)
        self.options = {
            "primary_path": self.primary_dir / "conversation.sqlite3",
            "replica_path": self.replica_dir / "conversation.sqlite3",
            "anchor_dir": self.anchor_dir,
            "anchor_path": self.anchor_dir / "head.json",
            "auth_key_path": self.auth_key_path,
            "ingest_key_path": self.ingest_key_path,
            "user_view_key_path": self.user_view_key_path,
            "proxy_key_path": self.proxy_key_path,
            "minecraft_key_path": self.minecraft_key_path,
            "attestation_path": self.attestation_path,
            "host_session_state_path": self.host_session_state_path,
            "admin_state_path": self.anchor_dir / "admin-auth.json",
            "startup_replay_path": self.anchor_dir / "startup-replay.json",
            "expected_admin_sid": ADMIN_SID,
            "expected_admin_account": ADMIN_ACCOUNT,
            "registered_discord_user_id": ADMIN_DISCORD_ID,
            "expected_host_id": "EVELYN-HOST",
            "local_owner_external_id": "control-page:local",
            "local_owner_name": "정훈",
            "purge_memory_index_dir": root / "memory" / "memory_index",
            "purge_memory_root": root / "memory",
            "purge_voice_debug_root": root / "debug_audio",
            "retention_interval_seconds": 3600,
        }
        app = fast_api.create_app(
            enable_minecraft_world_lease_owner=False,
            conversation_archive_enabled=True,
            conversation_archive_options=self.options,
        )
        app.cleanup_ctx.remove(fast_api.fast_main_llm_warmup_context)
        self.client = TestClient(TestServer(app))
        await self.client.start_server()
        self.nonce_counter = 0

    async def asyncTearDown(self) -> None:
        await self.client.close()

    async def test_user_view_handles_expire_and_are_one_use(self) -> None:
        now = [100.0]
        handles = fast_api._ConversationArchiveUserViewHandles(
            master_key=USER_VIEW_KEY,
            clock=lambda: now[0],
        )
        token = handles.issue({"kind": "action"})
        self.assertEqual(handles.consume(token, kind="action")["kind"], "action")
        with self.assertRaises(fast_api._ConversationArchiveUserViewError):
            handles.consume(token, kind="action")
        expired = handles.issue({"kind": "action"})
        now[0] += fast_api.CONVERSATION_ARCHIVE_USER_VIEW_HANDLE_SECONDS + 1
        with self.assertRaises(fast_api._ConversationArchiveUserViewError):
            handles.consume(expired, kind="action")

    def _write_attestation(
        self,
        issued_at: int,
        *,
        registered_discord_user_id: str = ADMIN_DISCORD_ID,
        bootstrap_nonce: str = BOOTSTRAP_NONCE,
    ) -> None:
        unsigned = {
            "schema": admin.ADMIN_ATTESTATION_SCHEMA,
            "purpose": admin.ADMIN_ATTESTATION_PURPOSE,
            "adminSid": ADMIN_SID,
            "adminAccount": ADMIN_ACCOUNT,
            "registeredDiscordUserId": registered_discord_user_id,
            "hostId": "EVELYN-HOST",
            "bootId": "2026-08-28T00:00:00Z",
            "bootstrapNonce": bootstrap_nonce,
            "issuedAt": issued_at,
            "expiresAt": issued_at + 60,
            "elevated": True,
            "administratorMember": True,
            "primary": _volume("primary", self.nonces["primary"]),
            "replica": _volume("replica", self.nonces["replica"]),
            "anchor": _volume("anchor", self.nonces["anchor"]),
        }
        signed = admin.sign_host_attestation(unsigned, signing_key=AUTH_KEY)
        self.attestation_path.write_text(
            json.dumps(signed, separators=(",", ":")), encoding="utf-8"
        )
        self._write_host_session(
            issued_at,
            bootstrap_nonce=bootstrap_nonce,
        )

    def _write_host_session(
        self,
        updated_at: int,
        *,
        state: str = "active",
        bootstrap_nonce: str = BOOTSTRAP_NONCE,
    ) -> None:
        host_session = admin.sign_host_session_marker(
            {
                "schema": admin.ADMIN_HOST_SESSION_SCHEMA,
                "purpose": admin.ADMIN_HOST_SESSION_PURPOSE,
                "adminSid": ADMIN_SID,
                "hostId": "EVELYN-HOST",
                "bootId": "2026-08-28T00:00:00Z",
                "bootstrapNonce": bootstrap_nonce,
                "state": state,
                "updatedAt": updated_at,
                "expiresAt": updated_at + 300,
            },
            signing_key=AUTH_KEY,
        )
        self.host_session_state_path.write_text(
            json.dumps(host_session, separators=(",", ":")), encoding="utf-8"
        )

    def _headers(
        self,
        *,
        purpose: str,
        method: str,
        path: str,
        body: bytes,
        replay_nonce: str | None = None,
        control_evidence: tuple[str, str, str] | None = None,
        signing_master: bytes | None = None,
    ) -> dict[str, str]:
        self.nonce_counter += 1
        nonce = replay_nonce or f"{self.nonce_counter:032x}"
        timestamp = str(int(time.time()))
        master = signing_master or {
            "ingest": INGEST_KEY,
            "user-view-issue": USER_VIEW_KEY,
            "user-view": USER_VIEW_KEY,
            "otp-delivery": INGEST_KEY,
            "purge-owner": INGEST_KEY,
            "control-proxy": PROXY_KEY,
            "minecraft": MINECRAFT_KEY,
        }[purpose]
        subkey = hmac.new(
            master,
            fast_api._CONVERSATION_ARCHIVE_TRANSPORT_KEY_DOMAIN
            + purpose.encode("ascii"),
            hashlib.sha256,
        ).digest()
        lines = [
            purpose,
            method,
            path,
            timestamp,
            nonce,
            hashlib.sha256(body).hexdigest(),
        ]
        headers = {
            fast_api.CONVERSATION_ARCHIVE_TRANSPORT_TIMESTAMP_HEADER: timestamp,
            fast_api.CONVERSATION_ARCHIVE_TRANSPORT_NONCE_HEADER: nonce,
        }
        if purpose == "control-proxy":
            evidence = control_evidence or (
                "https", "127.0.0.1:8800", "https://127.0.0.1:8800"
            )
            lines.extend(evidence)
            headers.update(
                {
                    fast_api.CONVERSATION_ARCHIVE_CONTROL_SCHEME_HEADER: evidence[0],
                    fast_api.CONVERSATION_ARCHIVE_CONTROL_HOST_HEADER: evidence[1],
                    fast_api.CONVERSATION_ARCHIVE_CONTROL_ORIGIN_HEADER: evidence[2],
                }
            )
        headers[fast_api.CONVERSATION_ARCHIVE_TRANSPORT_SIGNATURE_HEADER] = (
            hmac.new(
                subkey,
                "\n".join(lines).encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()
        )
        return headers

    async def _post(
        self,
        path: str,
        payload: dict[str, object],
        *,
        purpose: str,
        cookie: str = "",
        replay_nonce: str | None = None,
        control_evidence: tuple[str, str, str] | None = None,
    ):
        body = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        headers = self._headers(
            purpose=purpose,
            method="POST",
            path=path,
            body=body,
            replay_nonce=replay_nonce,
            control_evidence=control_evidence,
        )
        headers["Content-Type"] = "application/json"
        if cookie:
            headers["Cookie"] = (
                f"{fast_api.CONVERSATION_ARCHIVE_ADMIN_COOKIE}={cookie}"
            )
        return await self.client.post(path, data=body, headers=headers)

    async def test_canary_api_accepts_no_client_aggregate_or_complete_phase(self) -> None:
        path = "/internal/conversation-archive/admin/feedback/canary"
        aggregate_response = await self._post(
            path,
            {
                "versionId": "candidate-v1",
                "canaryRunId": "canary-run-1",
                "phase": "begin",
                "aggregate": {
                    "sampleCount": 10,
                    "passedCount": 10,
                },
            },
            purpose="control-proxy",
        )
        self.assertGreaterEqual(aggregate_response.status, 400)
        self.assertNotEqual(aggregate_response.status, 401)

        complete_response = await self._post(
            path,
            {
                "versionId": "candidate-v1",
                "canaryRunId": "canary-run-1",
                "phase": "complete",
            },
            purpose="control-proxy",
        )
        self.assertGreaterEqual(complete_response.status, 400)
        self.assertNotEqual(complete_response.status, 401)

    async def _authorize_self(
        self,
        action: str,
        *,
        interaction_id: str,
        caller_user_id: str = USER_ID,
        guild_id: str = GUILD_ID,
        **query: object,
    ) -> str:
        response = await self._post(
            "/internal/conversation-archive/self/authorize",
            {
                "context": "GUILD",
                "interactionId": interaction_id,
                "callerUserId": caller_user_id,
                "guildId": guild_id,
                "action": action,
                **query,
            },
            purpose="user-view-issue",
        )
        self.assertEqual(response.status, 200, await response.text())
        return str((await response.json())["handle"])

    async def _self_action(
        self,
        path: str,
        *,
        handle: str,
        interaction_id: str,
        caller_user_id: str = USER_ID,
        guild_id: str = GUILD_ID,
    ):
        return await self._post(
            path,
            {
                "context": "GUILD",
                "interactionId": interaction_id,
                "callerUserId": caller_user_id,
                "guildId": guild_id,
                "handle": handle,
            },
            purpose="user-view",
        )

    async def _activate_discord(self, generation: str = "discord-boot-a"):
        return await self._post(
            "/internal/conversation-archive/generation",
            {"generation": generation},
            purpose="ingest",
        )

    async def _append_user_record(
        self,
        *,
        sequence: int = 1,
        record_id: str = "discord-user-record-1",
        body: str = "private body",
        kind: str = "user_text",
        lineage: dict[str, list[str]] | None = None,
    ):
        now = datetime.now(timezone.utc).isoformat()
        payload = {
            "generation": "discord-boot-a",
            "sequence": sequence,
            "idempotencyKey": f"record-{record_id}",
            "recordId": record_id,
            "guildId": GUILD_ID,
            "channelId": TEXT_CHANNEL_ID,
            "kind": kind,
            "startedAt": now,
            "endedAt": now,
            "sourceUserId": USER_ID,
            "ownerName": "참여자",
            "parentRecordIds": [],
            "body": body,
        }
        if lineage is not None:
            payload["lineage"] = lineage
        return await self._post(
            "/internal/conversation-archive/record",
            payload,
            purpose="ingest",
        )

    async def _voice_state(
        self,
        *,
        sequence: int,
        muted: bool,
    ):
        return await self._post(
            "/internal/conversation-archive/voice-state",
            {
                "generation": "discord-boot-a",
                "sequence": sequence,
                "idempotencyKey": f"voice-admin-page-{sequence}",
                "guildId": GUILD_ID,
                "userId": USER_ID,
                "ownerName": "참여자",
                "observedAt": datetime.now(timezone.utc).isoformat(),
                "snapshot": {
                    "channelId": VOICE_CHANNEL_ID,
                    "present": True,
                    "consentCurrent": True,
                    "gatewayKnown": True,
                    "selfMute": muted,
                    "serverMute": False,
                    "selfDeaf": False,
                    "serverDeaf": False,
                    "suppressed": False,
                },
            },
            purpose="ingest",
        )

    async def _admin_login(self) -> str:
        self._write_attestation(int(time.time()))
        challenge = await self._post(
            "/internal/conversation-archive/admin/challenge",
            {"bootstrapNonce": BOOTSTRAP_NONCE},
            purpose="control-proxy",
        )
        self.assertEqual(challenge.status, 200, await challenge.text())
        challenge_payload = await challenge.json()
        self.assertNotIn("code", challenge_payload)
        poll = await self._post(
            "/internal/conversation-archive/admin/otp-delivery/poll",
            {},
            purpose="otp-delivery",
        )
        delivery = (await poll.json())["deliveries"][0]
        ack = await self._post(
            "/internal/conversation-archive/admin/otp-delivery/ack",
            {"deliveryId": delivery["deliveryId"], "delivered": True},
            purpose="otp-delivery",
        )
        self.assertEqual(ack.status, 200)
        login = await self._post(
            "/internal/conversation-archive/admin/login",
            {
                "challengeId": challenge_payload["challengeId"],
                "code": delivery["code"],
            },
            purpose="control-proxy",
        )
        self.assertEqual(login.status, 200, await login.text())
        morsel = login.cookies[fast_api.CONVERSATION_ARCHIVE_ADMIN_COOKIE]
        self.assertTrue(morsel["secure"])
        self.assertTrue(morsel["httponly"])
        self.assertEqual(morsel["samesite"].lower(), "strict")
        return morsel.value

    async def test_signed_ingest_self_scope_replay_and_voice_admission(self) -> None:
        duplicate_body = b'{"generation":"old","generation":"new"}'
        duplicate = await self.client.post(
            "/internal/conversation-archive/generation",
            data=duplicate_body,
            headers={
                **self._headers(
                    purpose="ingest",
                    method="POST",
                    path="/internal/conversation-archive/generation",
                    body=duplicate_body,
                ),
                "Content-Type": "application/json",
            },
        )
        self.assertEqual(duplicate.status, 400, await duplicate.text())
        activated = await self._activate_discord()
        self.assertEqual(activated.status, 200)
        appended = await self._append_user_record()
        self.assertEqual(appended.status, 200, await appended.text())

        interaction_id = "623456789012345678"
        body = json.dumps(
            {
                "context": "GUILD",
                "interactionId": interaction_id,
                "callerUserId": USER_ID,
                "guildId": GUILD_ID,
                "action": "records",
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        path = "/internal/conversation-archive/self/authorize"
        nonce = "f" * 32
        headers = self._headers(
            purpose="user-view-issue",
            method="POST",
            path=path,
            body=body,
            replay_nonce=nonce,
        )
        headers["Content-Type"] = "application/json"
        first = await self.client.post(path, data=body, headers=headers)
        self.assertEqual(first.status, 200)
        handle = (await first.json())["handle"]
        replay = await self.client.post(path, data=body, headers=headers)
        self.assertEqual(replay.status, 409)
        records = await self._self_action(
            "/internal/conversation-archive/self/records",
            handle=handle,
            interaction_id=interaction_id,
        )
        self.assertEqual(records.status, 200, await records.text())
        self.assertEqual((await records.json())["records"][0]["body"], "private body")

        observed = datetime.now(timezone.utc).isoformat()
        voice = await self._post(
            "/internal/conversation-archive/voice-state",
            {
                "generation": "discord-boot-a",
                "sequence": 2,
                "idempotencyKey": "voice-2",
                "guildId": GUILD_ID,
                "userId": USER_ID,
                "ownerName": "참여자",
                "observedAt": observed,
                "snapshot": {
                    "channelId": VOICE_CHANNEL_ID,
                    "present": True,
                    "consentCurrent": True,
                    "gatewayKnown": True,
                    "selfMute": False,
                    "serverMute": False,
                    "selfDeaf": False,
                    "serverDeaf": False,
                    "suppressed": False,
                },
            },
            purpose="ingest",
        )
        self.assertEqual(voice.status, 200, await voice.text())
        admitted = await self._post(
            "/internal/conversation-archive/voice-admission",
            {
                "guildId": GUILD_ID,
                "channelId": VOICE_CHANNEL_ID,
                "userId": USER_ID,
            },
            purpose="ingest",
        )
        self.assertTrue((await admitted.json())["allowed"])
        muted = await self._post(
            "/internal/conversation-archive/voice-state",
            {
                "generation": "discord-boot-a",
                "sequence": 3,
                "idempotencyKey": "voice-3",
                "guildId": GUILD_ID,
                "userId": USER_ID,
                "ownerName": "참여자",
                "observedAt": datetime.now(timezone.utc).isoformat(),
                "snapshot": {
                    "channelId": VOICE_CHANNEL_ID,
                    "present": True,
                    "consentCurrent": True,
                    "gatewayKnown": True,
                    "selfMute": True,
                    "serverMute": False,
                    "selfDeaf": False,
                    "serverDeaf": False,
                    "suppressed": False,
                },
            },
            purpose="ingest",
        )
        self.assertEqual(muted.status, 200)
        denied = await self._post(
            "/internal/conversation-archive/voice-admission",
            {
                "guildId": GUILD_ID,
                "channelId": VOICE_CHANNEL_ID,
                "userId": USER_ID,
            },
            purpose="ingest",
        )
        self.assertFalse((await denied.json())["allowed"])

    async def test_discord_feedback_capture_rechecks_owner_scope_and_lineage(self) -> None:
        await self._activate_discord()
        task_id = "discord-turn-1"
        session_id = (
            f"guild:{GUILD_ID}:text:{TEXT_CHANNEL_ID}:user:{USER_ID}"
        )
        root = await self._append_user_record(
            sequence=1,
            record_id="discord-feedback-root",
            body="질문",
            lineage={
                "turn": [task_id],
                "session": [session_id],
            },
        )
        self.assertEqual(root.status, 200, await root.text())
        now = datetime.now(timezone.utc).isoformat()
        reply = await self._post(
            "/internal/conversation-archive/record",
            {
                "generation": "discord-boot-a",
                "sequence": 2,
                "idempotencyKey": "discord-feedback-reply",
                "recordId": "discord-feedback-reply",
                "guildId": GUILD_ID,
                "channelId": TEXT_CHANNEL_ID,
                "kind": "evelyn_reply",
                "startedAt": now,
                "endedAt": now,
                "sourceUserId": None,
                "ownerName": None,
                "parentRecordIds": ["discord-feedback-root"],
                "lineage": {"turn": [task_id]},
                "body": "답변",
            },
            purpose="ingest",
        )
        self.assertEqual(reply.status, 200, await reply.text())
        task_result = await self._post(
            "/internal/conversation-archive/record",
            {
                "generation": "discord-boot-a",
                "sequence": 3,
                "idempotencyKey": "discord-feedback-task-result",
                "recordId": "discord-feedback-task-result",
                "guildId": GUILD_ID,
                "channelId": TEXT_CHANNEL_ID,
                "kind": "task_result",
                "startedAt": now,
                "endedAt": now,
                "sourceUserId": None,
                "ownerName": None,
                "parentRecordIds": ["discord-feedback-root"],
                "lineage": {"turn": [task_id]},
                "body": "작업 결과",
            },
            purpose="ingest",
        )
        self.assertEqual(task_result.status, 200, await task_result.text())
        lease_id = "discord-feedback-lease-1"
        opened = await self._post(
            "/internal/conversation-archive/shared-session/open",
            {
                "generation": "discord-boot-a",
                "sequence": 4,
                "idempotencyKey": "discord-feedback-session-open",
                "operatorUserId": ADMIN_DISCORD_ID,
                "guildId": GUILD_ID,
                "textChannelId": TEXT_CHANNEL_ID,
                "voiceChannelId": VOICE_CHANNEL_ID,
                "leaseId": lease_id,
            },
            purpose="ingest",
        )
        self.assertEqual(opened.status, 200, await opened.text())

        async def submit(
            *,
            sequence: int,
            caller_user_id: str = USER_ID,
            submitted_task_id: str = task_id,
            submitted_source_record_id: str = "discord-feedback-reply",
            submitted_session_id: str = session_id,
            submitted_source_channel_id: str = TEXT_CHANNEL_ID,
            submitted_surface: str = "discord",
            category: str = "answer_quality",
            requested_change_scope: str = "none",
        ):
            return await self._post(
                "/internal/conversation-archive/feedback/capture",
                {
                    "generation": "discord-boot-a",
                    "sequence": sequence,
                    "idempotencyKey": f"discord-feedback-{sequence}",
                    "taskId": submitted_task_id,
                    "sourceRecordId": submitted_source_record_id,
                    "category": category,
                    "correction": "이 부분은 근거를 먼저 말해줘",
                    "nonce": f"feedback-nonce-{sequence}",
                    "callerUserId": caller_user_id,
                    "ownerName": "참여자",
                    "guildId": GUILD_ID,
                    "requestChannelId": TEXT_CHANNEL_ID,
                    "sourceChannelId": submitted_source_channel_id,
                    "sessionId": submitted_session_id,
                    "surface": submitted_surface,
                    "requestedChangeScope": requested_change_scope,
                    "sharedSessionLeaseId": lease_id,
                },
                purpose="ingest",
            )

        non_reply = await submit(
            sequence=5,
            submitted_source_record_id="discord-feedback-task-result",
        )
        self.assertEqual(non_reply.status, 403, await non_reply.text())
        captured = await submit(sequence=5)
        self.assertEqual(captured.status, 200, await captured.text())
        captured_workflow = (await captured.json())["workflow"]
        self.assertEqual(captured_workflow["route"], "review_only")
        self.assertFalse(captured_workflow["actionable"])
        self.assertIsNone(captured_workflow["versionId"])

        wrong_owner = await submit(
            sequence=6,
            caller_user_id="623456789012345678",
        )
        self.assertEqual(wrong_owner.status, 403, await wrong_owner.text())
        wrong_task = await submit(
            sequence=6,
            submitted_task_id="discord-turn-other",
        )
        self.assertEqual(wrong_task.status, 403, await wrong_task.text())
        wrong_session = await submit(
            sequence=6,
            submitted_session_id=(
                f"guild:{GUILD_ID}:text:{TEXT_CHANNEL_ID}:user:623456789012345678"
            ),
        )
        self.assertEqual(wrong_session.status, 403, await wrong_session.text())
        wrong_channel = await submit(
            sequence=6,
            submitted_source_channel_id=VOICE_CHANNEL_ID,
        )
        self.assertEqual(wrong_channel.status, 409, await wrong_channel.text())
        wrong_surface = await submit(
            sequence=6,
            submitted_source_channel_id=VOICE_CHANNEL_ID,
            submitted_surface="voice",
        )
        self.assertEqual(wrong_surface.status, 403, await wrong_surface.text())

        identity = await submit(sequence=6, category="tone_identity")
        self.assertEqual(identity.status, 200, await identity.text())
        identity_workflow = (await identity.json())["workflow"]
        self.assertEqual(identity_workflow["route"], "identity_review")
        self.assertFalse(identity_workflow["actionable"])
        self.assertIsNone(identity_workflow["versionId"])
        for sequence, scope in enumerate(
            ("evaluator", "tool", "approval", "source"),
            start=7,
        ):
            engineering = await submit(
                sequence=sequence,
                category="tool_failure",
                requested_change_scope=scope,
            )
            self.assertEqual(engineering.status, 200, await engineering.text())
            engineering_workflow = (await engineering.json())["workflow"]
            self.assertEqual(
                engineering_workflow["route"],
                "human_engineering_required",
            )
            self.assertFalse(engineering_workflow["actionable"])
            self.assertIsNone(engineering_workflow["versionId"])
        permission = await submit(
            sequence=11,
            category="permission_safety",
        )
        self.assertEqual(permission.status, 200, await permission.text())
        permission_workflow = (await permission.json())["workflow"]
        self.assertEqual(
            permission_workflow["route"],
            "human_engineering_required",
        )
        self.assertFalse(permission_workflow["actionable"])
        self.assertIsNone(permission_workflow["versionId"])
        for forbidden in (
            "promotion",
            "promote",
            "generalize",
            "eval",
            "evaluate",
            "approve",
            "approval",
            "activate",
        ):
            response = await self.client.post(
                f"/internal/conversation-archive/feedback/{forbidden}"
            )
            self.assertEqual(response.status, 404)

    async def test_feedback_capture_and_session_close_are_one_linearizable_race(
        self,
    ) -> None:
        await self._activate_discord()
        task_id = "discord-race-turn"
        session_id = (
            f"guild:{GUILD_ID}:text:{TEXT_CHANNEL_ID}:user:{USER_ID}"
        )
        root = await self._append_user_record(
            sequence=1,
            record_id="discord-race-root",
            body="질문",
            lineage={"turn": [task_id], "session": [session_id]},
        )
        self.assertEqual(root.status, 200, await root.text())
        now = datetime.now(timezone.utc).isoformat()
        reply = await self._post(
            "/internal/conversation-archive/record",
            {
                "generation": "discord-boot-a",
                "sequence": 2,
                "idempotencyKey": "discord-race-reply",
                "recordId": "discord-race-reply",
                "guildId": GUILD_ID,
                "channelId": TEXT_CHANNEL_ID,
                "kind": "evelyn_reply",
                "startedAt": now,
                "endedAt": now,
                "sourceUserId": None,
                "ownerName": None,
                "parentRecordIds": ["discord-race-root"],
                "lineage": {"turn": [task_id]},
                "body": "답변",
            },
            purpose="ingest",
        )
        self.assertEqual(reply.status, 200, await reply.text())
        lease_id = "discord-race-lease"
        opened = await self._post(
            "/internal/conversation-archive/shared-session/open",
            {
                "generation": "discord-boot-a",
                "sequence": 3,
                "idempotencyKey": "discord-race-open",
                "operatorUserId": ADMIN_DISCORD_ID,
                "guildId": GUILD_ID,
                "textChannelId": TEXT_CHANNEL_ID,
                "voiceChannelId": VOICE_CHANNEL_ID,
                "leaseId": lease_id,
            },
            purpose="ingest",
        )
        self.assertEqual(opened.status, 200, await opened.text())

        async def submit(*, sequence: int, suffix: str):
            return await self._post(
                "/internal/conversation-archive/feedback/capture",
                {
                    "generation": "discord-boot-a",
                    "sequence": sequence,
                    "idempotencyKey": f"discord-race-feedback-{suffix}",
                    "taskId": task_id,
                    "sourceRecordId": "discord-race-reply",
                    "category": "answer_quality",
                    "correction": "근거를 먼저 말해줘",
                    "nonce": f"discord-race-nonce-{suffix}",
                    "callerUserId": USER_ID,
                    "ownerName": "참여자",
                    "guildId": GUILD_ID,
                    "requestChannelId": TEXT_CHANNEL_ID,
                    "sourceChannelId": TEXT_CHANNEL_ID,
                    "sessionId": session_id,
                    "surface": "discord",
                    "requestedChangeScope": "none",
                    "sharedSessionLeaseId": lease_id,
                },
                purpose="ingest",
            )

        runtime = self.client.server.app[
            fast_api.CONVERSATION_ARCHIVE_RUNTIME_KEY
        ]
        controller = runtime.feedback_controller
        self.assertIsNotNone(controller)
        original_capture = controller.capture_correction
        capture_entered = threading.Event()
        release_capture = threading.Event()

        def blocked_capture(*args, **kwargs):
            capture_entered.set()
            if not release_capture.wait(timeout=5.0):
                raise AssertionError("feedback_capture_race_release_timeout")
            return original_capture(*args, **kwargs)

        with patch.object(
            controller,
            "capture_correction",
            side_effect=blocked_capture,
        ) as capture_mock:
            capture_task = asyncio.create_task(
                submit(sequence=4, suffix="winner")
            )
            close_task = None
            try:
                self.assertTrue(
                    await asyncio.to_thread(capture_entered.wait, 5.0)
                )
                close_task = asyncio.create_task(
                    self._post(
                        "/internal/conversation-archive/shared-session/close",
                        {
                            "generation": "discord-boot-a",
                            "sequence": 5,
                            "idempotencyKey": "discord-race-close",
                            "guildId": GUILD_ID,
                            "leaseId": lease_id,
                        },
                        purpose="ingest",
                    )
                )
                for _ in range(10):
                    await asyncio.sleep(0)
                self.assertFalse(close_task.done())
            finally:
                release_capture.set()
            if close_task is None:
                await capture_task
                self.fail("shared_session_close_race_not_started")
            captured, closed = await asyncio.gather(capture_task, close_task)
            self.assertEqual(captured.status, 200, await captured.text())
            self.assertEqual(closed.status, 200, await closed.text())
            stale = await submit(sequence=6, suffix="stale")
            self.assertEqual(stale.status, 409, await stale.text())
            self.assertEqual(capture_mock.call_count, 1)

        self.assertNotIn(GUILD_ID, runtime.discord_shared_session_leases)

    async def test_user_view_handle_rejects_forgery_replay_dm_and_stale_state(
        self,
    ) -> None:
        await self._activate_discord()
        await self._append_user_record()
        other_user = "623456789012345679"
        other_guild = "623456789012345680"

        forged_body = json.dumps(
            {
                "context": "GUILD",
                "interactionId": "633456789012345677",
                "callerUserId": USER_ID,
                "guildId": GUILD_ID,
                "action": "records",
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        forged_path = "/internal/conversation-archive/self/authorize"
        forged_admin_signature = await self.client.post(
            forged_path,
            data=forged_body,
            headers={
                **self._headers(
                    purpose="user-view-issue",
                    method="POST",
                    path=forged_path,
                    body=forged_body,
                    signing_master=AUTH_KEY,
                ),
                "Content-Type": "application/json",
            },
        )
        self.assertEqual(
            forged_admin_signature.status,
            403,
            await forged_admin_signature.text(),
        )

        wrong_user_handle = await self._authorize_self(
            "records", interaction_id="633456789012345678"
        )
        wrong_user = await self._self_action(
            "/internal/conversation-archive/self/records",
            handle=wrong_user_handle,
            interaction_id="633456789012345678",
            caller_user_id=other_user,
        )
        self.assertEqual(wrong_user.status, 403, await wrong_user.text())
        consumed_after_forgery = await self._self_action(
            "/internal/conversation-archive/self/records",
            handle=wrong_user_handle,
            interaction_id="633456789012345678",
        )
        self.assertEqual(consumed_after_forgery.status, 403)

        wrong_guild_handle = await self._authorize_self(
            "records", interaction_id="633456789012345679"
        )
        wrong_guild = await self._self_action(
            "/internal/conversation-archive/self/records",
            handle=wrong_guild_handle,
            interaction_id="633456789012345679",
            guild_id=other_guild,
        )
        self.assertEqual(wrong_guild.status, 403)

        wrong_interaction_handle = await self._authorize_self(
            "records", interaction_id="633456789012345680"
        )
        wrong_interaction = await self._self_action(
            "/internal/conversation-archive/self/records",
            handle=wrong_interaction_handle,
            interaction_id="633456789012345681",
        )
        self.assertEqual(wrong_interaction.status, 403)

        replay_interaction = await self._post(
            "/internal/conversation-archive/self/authorize",
            {
                "context": "GUILD",
                "interactionId": "633456789012345680",
                "callerUserId": USER_ID,
                "guildId": GUILD_ID,
                "action": "records",
            },
            purpose="user-view-issue",
        )
        self.assertEqual(replay_interaction.status, 409)

        dm = await self._post(
            "/internal/conversation-archive/self/authorize",
            {
                "context": "BOT_DM",
                "interactionId": "633456789012345682",
                "callerUserId": USER_ID,
                "guildId": GUILD_ID,
                "action": "records",
            },
            purpose="user-view-issue",
        )
        self.assertEqual(dm.status, 403)

        stale_handle = await self._authorize_self(
            "records", interaction_id="633456789012345683"
        )
        await self._append_user_record(
            sequence=2,
            record_id="discord-user-record-2",
            body="new generation",
        )
        stale = await self._self_action(
            "/internal/conversation-archive/self/records",
            handle=stale_handle,
            interaction_id="633456789012345683",
        )
        self.assertEqual(stale.status, 409, await stale.text())

    async def test_user_view_page_handle_is_opaque_one_use_and_scope_bound(
        self,
    ) -> None:
        await self._activate_discord()
        for sequence in range(1, 31):
            response = await self._append_user_record(
                sequence=sequence,
                record_id=f"discord-self-page-{sequence}",
                body=f"self-page-body-{sequence}",
            )
            self.assertEqual(response.status, 200, await response.text())

        first_interaction = "643456789012345678"
        first_handle = await self._authorize_self(
            "records", interaction_id=first_interaction
        )
        first = await self._self_action(
            "/internal/conversation-archive/self/records",
            handle=first_handle,
            interaction_id=first_interaction,
        )
        self.assertEqual(first.status, 200, await first.text())
        first_payload = await first.json()
        self.assertEqual(len(first_payload["records"]), 25)
        page_handle = first_payload["nextPageHandle"]
        self.assertRegex(page_handle, r"^[A-Za-z0-9_-]+$")
        self.assertNotIn(USER_ID, page_handle)
        self.assertNotIn(GUILD_ID, page_handle)

        wrong_guild = await self._post(
            "/internal/conversation-archive/self/authorize",
            {
                "context": "GUILD",
                "interactionId": "643456789012345679",
                "callerUserId": USER_ID,
                "guildId": "623456789012345680",
                "action": "records",
                "pageHandle": page_handle,
            },
            purpose="user-view-issue",
        )
        self.assertEqual(wrong_guild.status, 403, await wrong_guild.text())
        page_replay = await self._post(
            "/internal/conversation-archive/self/authorize",
            {
                "context": "GUILD",
                "interactionId": "643456789012345680",
                "callerUserId": USER_ID,
                "guildId": GUILD_ID,
                "action": "records",
                "pageHandle": page_handle,
            },
            purpose="user-view-issue",
        )
        self.assertEqual(page_replay.status, 403)

        retry_interaction = "643456789012345681"
        retry_handle = await self._authorize_self(
            "records", interaction_id=retry_interaction
        )
        retry = await self._self_action(
            "/internal/conversation-archive/self/records",
            handle=retry_handle,
            interaction_id=retry_interaction,
        )
        retry_page = (await retry.json())["nextPageHandle"]
        next_interaction = "643456789012345682"
        next_handle = await self._authorize_self(
            "records",
            interaction_id=next_interaction,
            pageHandle=retry_page,
        )
        next_page = await self._self_action(
            "/internal/conversation-archive/self/records",
            handle=next_handle,
            interaction_id=next_interaction,
        )
        next_payload = await next_page.json()
        self.assertEqual(len(next_payload["records"]), 5)
        self.assertIsNone(next_payload["nextPageHandle"])
        self.assertTrue(
            set(row["recordId"] for row in first_payload["records"]).isdisjoint(
                row["recordId"] for row in next_payload["records"]
            )
        )

        stale_source_interaction = "643456789012345683"
        stale_source_handle = await self._authorize_self(
            "records", interaction_id=stale_source_interaction
        )
        stale_source = await self._self_action(
            "/internal/conversation-archive/self/records",
            handle=stale_source_handle,
            interaction_id=stale_source_interaction,
        )
        stale_page_handle = (await stale_source.json())["nextPageHandle"]
        appended = await self._append_user_record(
            sequence=31,
            record_id="discord-self-page-31",
            body="generation changes",
        )
        self.assertEqual(appended.status, 200, await appended.text())
        stale_page = await self._post(
            "/internal/conversation-archive/self/authorize",
            {
                "context": "GUILD",
                "interactionId": "643456789012345684",
                "callerUserId": USER_ID,
                "guildId": GUILD_ID,
                "action": "records",
                "pageHandle": stale_page_handle,
            },
            purpose="user-view-issue",
        )
        self.assertEqual(stale_page.status, 409, await stale_page.text())

    async def test_user_view_response_stays_below_client_byte_limit(self) -> None:
        await self._activate_discord()
        body = "가" * 40_000
        for sequence in range(1, 9):
            response = await self._append_user_record(
                sequence=sequence,
                record_id=f"discord-large-self-{sequence}",
                body=f"{sequence}:{body}",
            )
            self.assertEqual(response.status, 200, await response.text())
        interaction_id = "653456789012345678"
        handle = await self._authorize_self("records", interaction_id=interaction_id)
        response = await self._self_action(
            "/internal/conversation-archive/self/records",
            handle=handle,
            interaction_id=interaction_id,
        )
        encoded = await response.read()
        self.assertEqual(response.status, 200, encoded[:200])
        self.assertLess(len(encoded), 1024 * 1024)
        self.assertLessEqual(
            len(encoded), fast_api.CONVERSATION_ARCHIVE_SELF_RESPONSE_BUDGET_BYTES
        )
        payload = json.loads(encoded)
        self.assertLess(len(payload["records"]), 8)
        self.assertTrue(payload["nextPageHandle"])

    async def test_retention_fault_latches_until_full_cycle_succeeds(self) -> None:
        await self._activate_discord()
        baseline = await self._append_user_record()
        self.assertEqual(baseline.status, 200, await baseline.text())
        runtime = self.client.server.app[
            fast_api.CONVERSATION_ARCHIVE_RUNTIME_KEY
        ]
        self.assertIsNotNone(runtime.archive)
        self.assertIsNotNone(runtime.purge_coordinator)

        with (
            patch.object(
                runtime.archive,
                "reconcile_replica",
                side_effect=RuntimeError("private maintenance detail"),
            ),
            patch.object(runtime.purge_coordinator, "purge_pending"),
            patch.object(runtime.archive, "prune_expired"),
        ):
            await runtime._run_maintenance_cycle()
        self.assertTrue(runtime.maintenance_fault)

        path = "/internal/conversation-archive/status"
        status = await self.client.get(
            path,
            headers=self._headers(
                purpose="ingest",
                method="GET",
                path=path,
                body=b"",
            ),
        )
        self.assertEqual(status.status, 200, await status.text())
        status_payload = await status.json()
        self.assertEqual(status_payload["state"], "archive_maintenance_fault")
        self.assertFalse(status_payload["writesAllowed"])
        self.assertTrue(status_payload["contentFree"])
        self.assertNotIn("private maintenance detail", await status.text())

        blocked = await self._append_user_record(
            sequence=2,
            record_id="maintenance-blocked-record",
        )
        self.assertEqual(blocked.status, 503, await blocked.text())
        self.assertEqual(
            (await blocked.json())["error"],
            "archive_maintenance_fault",
        )

        with (
            patch.object(runtime.archive, "reconcile_replica"),
            patch.object(
                runtime.purge_coordinator,
                "purge_pending",
                side_effect=RuntimeError("private purge detail"),
            ),
            patch.object(runtime.archive, "prune_expired"),
        ):
            await runtime._run_maintenance_cycle()
        self.assertTrue(runtime.maintenance_fault)

        with (
            patch.object(runtime.archive, "reconcile_replica"),
            patch.object(runtime.purge_coordinator, "purge_pending"),
            patch.object(
                runtime.archive,
                "prune_expired",
                side_effect=RuntimeError("private retention detail"),
            ),
        ):
            await runtime._run_maintenance_cycle()
        self.assertTrue(runtime.maintenance_fault)

        with (
            patch.object(runtime.archive, "reconcile_replica"),
            patch.object(runtime.purge_coordinator, "purge_pending"),
            patch.object(runtime.archive, "prune_expired", return_value=None),
        ):
            await runtime._run_maintenance_cycle()
        self.assertFalse(runtime.maintenance_fault)
        recovered = await self._append_user_record(
            sequence=2,
            record_id="maintenance-recovered-record",
        )
        self.assertEqual(recovered.status, 200, await recovered.text())

    async def test_admin_otp_page_and_step_up_delete(self) -> None:
        await self._activate_discord()
        await self._append_user_record()
        cookie = await self._admin_login()
        runtime = self.client.server.app[
            fast_api.CONVERSATION_ARCHIVE_RUNTIME_KEY
        ]
        runtime.maintenance_fault = True
        records = await self._post(
            "/internal/conversation-archive/admin/records",
            {},
            purpose="control-proxy",
            cookie=cookie,
        )
        self.assertEqual(records.status, 200, await records.text())
        page = await records.json()
        self.assertEqual(page["records"][0]["ownerName"], "참여자")
        self.assertIn("nextCursor", page)
        with patch.object(
            fast_api,
            "CONVERSATION_ARCHIVE_ADMIN_RESPONSE_BUDGET_BYTES",
            32,
        ):
            capped = await self._post(
                "/internal/conversation-archive/admin/records",
                {},
                purpose="control-proxy",
                cookie=cookie,
            )
        self.assertEqual(capped.status, 503, await capped.text())
        self.assertEqual(
            (await capped.json())["error"],
            "archive_admin_response_too_large",
        )

        preview = await self._post(
            "/internal/conversation-archive/admin/delete/preview",
            {"recordIds": ["discord-user-record-1"]},
            purpose="control-proxy",
            cookie=cookie,
        )
        self.assertEqual(preview.status, 200, await preview.text())
        preview_payload = await preview.json()
        self.assertNotIn("code", preview_payload)
        poll = await self._post(
            "/internal/conversation-archive/admin/otp-delivery/poll",
            {},
            purpose="otp-delivery",
        )
        delivery = (await poll.json())["deliveries"][0]
        await self._post(
            "/internal/conversation-archive/admin/otp-delivery/ack",
            {"deliveryId": delivery["deliveryId"], "delivered": True},
            purpose="otp-delivery",
        )
        applied = await self._post(
            "/internal/conversation-archive/admin/delete/apply",
            {
                "previewToken": preview_payload["previewToken"],
                "code": delivery["code"],
            },
            purpose="control-proxy",
            cookie=cookie,
        )
        self.assertEqual(applied.status, 200, await applied.text())
        applied_payload = await applied.json()
        self.assertEqual(
            applied_payload["state"], "local_cleanup_pending"
        )
        self.assertIsNotNone(runtime.purge_coordinator)
        self.assertEqual(
            set(runtime.purge_coordinator.registered_sinks),
            set(archive_core.ARCHIVE_REQUIRED_PURGE_SINKS),
        )
        self.assertEqual(
            len(runtime.archive.pending_purge_work_orders()),
            1,
        )
        legal = await self._post(
            "/internal/conversation-archive/admin/legal-minimal",
            {},
            purpose="control-proxy",
            cookie=cookie,
        )
        self.assertEqual(legal.status, 200, await legal.text())
        legal_payload = await legal.json()
        self.assertTrue(legal_payload["events"])
        self.assertEqual(
            set(legal_payload["events"][0]),
            {"ownerName", "occurredAt"},
        )
        legal_text = await legal.text()
        self.assertNotIn("discord-user-record-1", legal_text)
        self.assertNotIn(USER_ID, legal_text)
        self.assertNotIn("private body", legal_text)
        purge_run = await runtime.purge_deletion(
            applied_payload["requestId"]
        )
        self.assertEqual(
            next(
                status.state
                for status in purge_run.sinks
                if status.sink == "voice_debug_audio"
            ),
            "purged",
        )
        journal_rows = read_memory_deletion_tombstones(
            self.options["purge_memory_index_dir"]
        )
        self.assertEqual(len(journal_rows), 1)
        self.assertNotIn(
            applied_payload["requestId"],
            json.dumps(journal_rows, sort_keys=True),
        )
        reused = await self._post(
            "/internal/conversation-archive/admin/delete/apply",
            {
                "previewToken": preview_payload["previewToken"],
                "code": delivery["code"],
            },
            purpose="control-proxy",
            cookie=cookie,
        )
        self.assertEqual(reused.status, 403)

    async def test_remote_purge_owner_protocol_is_exact_content_free_and_volatile(
        self,
    ) -> None:
        await self._activate_discord()
        appended = await self._append_user_record(
            lineage={
                "turn": ["turn-private-1"],
                "session": ["session-private-1"],
            }
        )
        self.assertEqual(appended.status, 200, await appended.text())
        runtime = self.client.server.app[
            fast_api.CONVERSATION_ARCHIVE_RUNTIME_KEY
        ]
        archive = runtime.archive
        preview = await asyncio.to_thread(
            archive.preview_admin_deletion,
            authorized=True,
            record_ids=("discord-user-record-1",),
        )
        deletion = await asyncio.to_thread(
            archive.apply_admin_deletion,
            authorized=True,
            preview_id=preview.preview_id,
        )
        await runtime.purge_deletion(deletion.request_id)

        wrong_domain = await self._post(
            "/internal/conversation-archive/purge-owner/poll",
            {},
            purpose="otp-delivery",
        )
        self.assertEqual(wrong_domain.status, 403, await wrong_domain.text())
        replay_nonce = "f" * 32
        poll = await self._post(
            "/internal/conversation-archive/purge-owner/poll",
            {},
            purpose="purge-owner",
            replay_nonce=replay_nonce,
        )
        self.assertEqual(poll.status, 200, await poll.text())
        replayed_poll = await self._post(
            "/internal/conversation-archive/purge-owner/poll",
            {},
            purpose="purge-owner",
            replay_nonce=replay_nonce,
        )
        self.assertEqual(replayed_poll.status, 409, await replayed_poll.text())
        self.assertEqual(
            (await replayed_poll.json())["error"],
            "archive_transport_replayed",
        )

        poll_payload = await poll.json()
        self.assertEqual(set(poll_payload), {"ok", "workOrders", "contentFree"})
        self.assertTrue(poll_payload["contentFree"])
        self.assertEqual(len(poll_payload["workOrders"]), 1)
        work = poll_payload["workOrders"][0]
        self.assertEqual(
            set(work),
            {
                "requestId",
                "deletionGeneration",
                "scopeDigest",
                "reason",
                "requestedAt",
                "scopeAll",
                "guildId",
                "startedAt",
                "endedAt",
                "lineageHandles",
                "lineageComplete",
                "remainingSinks",
                "contentFree",
            },
        )
        self.assertEqual(
            set(work["remainingSinks"]),
            set(fast_api._CONVERSATION_ARCHIVE_REMOTE_PURGE_SINKS),
        )
        self.assertEqual(
            {item["kind"] for item in work["lineageHandles"]},
            {"session", "turn"},
        )
        serialized = json.dumps(poll_payload, ensure_ascii=False)
        self.assertNotIn(USER_ID, serialized)
        self.assertNotIn("참여자", serialized)
        self.assertNotIn("private body", serialized)
        self.assertNotIn("turn-private-1", serialized)
        self.assertNotIn("session-private-1", serialized)

        receipt = {
            "requestId": work["requestId"],
            "deletionGeneration": work["deletionGeneration"],
            "scopeDigest": work["scopeDigest"],
            "sink": "continuity_checkpoint",
            "contentFree": True,
            "complete": True,
            "remainingCopies": 0,
            "manualReviewCount": 0,
        }
        tampered = await self._post(
            "/internal/conversation-archive/purge-owner/ack",
            {**receipt, "scopeDigest": "0" * 64},
            purpose="purge-owner",
        )
        self.assertEqual(tampered.status, 409, await tampered.text())
        invalid_claim = await self._post(
            "/internal/conversation-archive/purge-owner/ack",
            {**receipt, "remainingCopies": 1},
            purpose="purge-owner",
        )
        self.assertEqual(invalid_claim.status, 400, await invalid_claim.text())
        current_work = archive.deletion_purge_work_order(
            request_id=work["requestId"]
        )
        incomplete_work = replace(
            current_work,
            lineage_handles=(),
            lineage_complete=False,
        )
        with patch.object(
            archive,
            "deletion_purge_work_order",
            return_value=incomplete_work,
        ):
            incomplete = await self._post(
                "/internal/conversation-archive/purge-owner/ack",
                {
                    **receipt,
                    "scopeDigest": deletion_purge_scope_digest(
                        incomplete_work
                    ),
                },
                purpose="purge-owner",
            )
        self.assertEqual(incomplete.status, 409, await incomplete.text())
        self.assertEqual(
            (await incomplete.json())["error"],
            "archive_purge_lineage_incomplete",
        )
        acknowledged = await self._post(
            "/internal/conversation-archive/purge-owner/ack",
            receipt,
            purpose="purge-owner",
        )
        self.assertEqual(acknowledged.status, 200, await acknowledged.text())
        self.assertFalse((await acknowledged.json())["archiveCompleted"])
        semantic_replay = await self._post(
            "/internal/conversation-archive/purge-owner/ack",
            receipt,
            purpose="purge-owner",
        )
        self.assertEqual(
            semantic_replay.status, 409, await semantic_replay.text()
        )
        self.assertEqual(
            (await semantic_replay.json())["error"],
            "archive_purge_receipt_replayed",
        )

        current_work = archive.deletion_purge_work_order(
            request_id=work["requestId"]
        )
        self.assertEqual(
            runtime.process_tool_cache_purge_pass(
                current_work
            ).manual_review_count,
            1,
        )
        prompt_ack = await self._post(
            "/internal/conversation-archive/purge-owner/ack",
            {**receipt, "sink": "prompt_tool_cache"},
            purpose="purge-owner",
        )
        self.assertEqual(prompt_ack.status, 200, await prompt_ack.text())
        current_work = archive.deletion_purge_work_order(
            request_id=work["requestId"]
        )
        self.assertEqual(
            runtime.process_tool_cache_purge_pass(
                current_work
            ).manual_review_count,
            0,
        )
        self.assertFalse(runtime.remote_writer_fence_current(current_work))
        for remote_sink in fast_api._CONVERSATION_ARCHIVE_REMOTE_PURGE_SINKS:
            runtime.remote_purge_receipts.add(
                (
                    work["requestId"],
                    work["deletionGeneration"],
                    work["scopeDigest"],
                    remote_sink,
                )
            )
        self.assertTrue(runtime.remote_writer_fence_current(current_work))
        for remote_sink in fast_api._CONVERSATION_ARCHIVE_REMOTE_PURGE_SINKS:
            if remote_sink in {"continuity_checkpoint", "prompt_tool_cache"}:
                continue
            runtime.remote_purge_receipts.discard(
                (
                    work["requestId"],
                    work["deletionGeneration"],
                    work["scopeDigest"],
                    remote_sink,
                )
            )
        runtime.options["purge_process_tool_cache"] = (
            lambda _work_order: PurgePass(manual_review_count=1)
        )
        self.assertEqual(
            runtime.process_tool_cache_purge_pass(
                current_work
            ).manual_review_count,
            1,
        )
        runtime.options["purge_process_tool_cache"] = None

        after_ack = await self._post(
            "/internal/conversation-archive/purge-owner/poll",
            {},
            purpose="purge-owner",
        )
        remaining = (await after_ack.json())["workOrders"][0][
            "remainingSinks"
        ]
        self.assertNotIn("continuity_checkpoint", remaining)
        self.assertNotIn("prompt_tool_cache", remaining)
        runtime.remote_purge_receipts.clear()
        after_restart = await self._post(
            "/internal/conversation-archive/purge-owner/poll",
            {},
            purpose="purge-owner",
        )
        self.assertIn(
            "continuity_checkpoint",
            (await after_restart.json())["workOrders"][0]["remainingSinks"],
        )

        runtime.remote_purge_receipts.add(
            (
                work["requestId"],
                work["deletionGeneration"],
                "0" * 64,
                "ingress_journal",
            )
        )
        await runtime.reconcile_remote_purge_receipts()
        self.assertFalse(
            any(row[2] == "0" * 64 for row in runtime.remote_purge_receipts)
        )
        runtime.remote_purge_receipts.add(
            (
                work["requestId"],
                work["deletionGeneration"],
                work["scopeDigest"],
                "ingress_journal",
            )
        )
        with patch.object(
            archive,
            "deletion_purge_work_order",
            return_value=None,
        ):
            await runtime.reconcile_remote_purge_receipts()
        self.assertFalse(runtime.remote_purge_receipts)

    async def test_startup_restores_every_pending_fence_after_first_1000(
        self,
    ) -> None:
        requested_at = datetime(2026, 8, 1, tzinfo=timezone.utc)
        rows = tuple(
            SimpleNamespace(
                requested_at=requested_at,
                request_id=f"request-{index:06d}",
            )
            for index in range(1002)
        )
        calls: list[tuple[int, object]] = []
        restored_pages: list[tuple[str, ...]] = []

        class Archive:
            def pending_purge_work_orders(self, *, limit=100, after=None):
                calls.append((limit, after))
                start = 0
                if after is not None:
                    start = next(
                        (
                            index
                            for index, row in enumerate(rows)
                            if (row.requested_at, row.request_id) > after
                        ),
                        len(rows),
                    )
                return rows[start : start + limit]

        class Coordinator:
            def restore_pending_fences(self, work_orders):
                restored_pages.append(
                    tuple(row.request_id for row in work_orders)
                )

        restored = await fast_api._ConversationArchiveApiRuntime(
            {"clock": time.time}
        )._restore_all_pending_purge_fences(Archive(), Coordinator())

        self.assertTrue(restored)
        self.assertEqual([len(page) for page in restored_pages], [1000, 2])
        self.assertEqual(
            tuple(request_id for page in restored_pages for request_id in page),
            tuple(row.request_id for row in rows),
        )
        self.assertEqual(
            calls,
            [
                (1000, None),
                (1000, (requested_at, "request-000999")),
            ],
        )

    async def test_remote_purge_poll_round_robin_reaches_work_after_1000_stuck(
        self,
    ) -> None:
        runtime = self.client.server.app[
            fast_api.CONVERSATION_ARCHIVE_RUNTIME_KEY
        ]
        archive = runtime.archive
        requested_at = datetime(2026, 8, 1, tzinfo=timezone.utc)
        rows = tuple(
            archive_core.DeletionPurgeWorkOrder(
                request_id=f"request-{index:06d}",
                reason="user_requested",
                requested_at=requested_at,
                deletion_generation=index + 1,
                principal_id=f"principal-{index}",
                owned_record_ids=(f"record-{index}",),
                dependent_record_ids=(),
                interval_ids=(),
                scope_all=True,
                guild_id=GUILD_ID,
                started_at=None,
                ended_at=None,
                required_sinks=(
                    ("continuity_checkpoint",)
                    if index == 1001
                    else ("voice_debug_audio",)
                ),
                principal_ids=(f"principal-{index}",),
                lineage_handles=(
                    (
                        "turn",
                        hashlib.sha256(
                            f"turn-{index}".encode("ascii")
                        ).hexdigest(),
                    ),
                ),
                lineage_complete=True,
            )
            for index in range(1002)
        )
        calls: list[tuple[int, object]] = []

        def page(*, limit=100, after=None):
            calls.append((limit, after))
            start = 0
            if after is not None:
                start = next(
                    (
                        index
                        for index, row in enumerate(rows)
                        if (row.requested_at, row.request_id) > after
                    ),
                    len(rows),
                )
            return rows[start : start + limit]

        with patch.object(
            archive,
            "pending_purge_work_orders",
            side_effect=page,
        ):
            first = await self._post(
                "/internal/conversation-archive/purge-owner/poll",
                {},
                purpose="purge-owner",
            )
            second = await self._post(
                "/internal/conversation-archive/purge-owner/poll",
                {},
                purpose="purge-owner",
            )

        self.assertEqual(first.status, 200, await first.text())
        self.assertEqual((await first.json())["workOrders"], [])
        self.assertEqual(second.status, 200, await second.text())
        self.assertEqual(
            [
                item["requestId"]
                for item in (await second.json())["workOrders"]
            ],
            ["request-001001"],
        )
        self.assertEqual(
            [limit for limit, _after in calls],
            [1000, 1000, 998],
        )
        self.assertEqual(
            runtime.remote_purge_poll_cursor,
            (requested_at, "request-000997"),
        )

    async def test_archive_runtime_close_clears_remote_purge_poll_cursor(
        self,
    ) -> None:
        active = self.client.server.app[
            fast_api.CONVERSATION_ARCHIVE_RUNTIME_KEY
        ]
        runtime = fast_api._ConversationArchiveApiRuntime(active.options)
        runtime.remote_purge_poll_cursor = (
            datetime(2026, 8, 1, tzinfo=timezone.utc),
            "request-1",
        )

        await runtime.close()

        self.assertIsNone(runtime.remote_purge_poll_cursor)

    async def test_admin_participation_pages_use_scope_bound_opaque_cursor(
        self,
    ) -> None:
        await self._activate_discord()
        for sequence, muted in ((1, False), (2, True), (3, False)):
            response = await self._voice_state(
                sequence=sequence,
                muted=muted,
            )
            self.assertEqual(response.status, 200, await response.text())
        cookie = await self._admin_login()
        with patch.object(
            fast_api,
            "CONVERSATION_ARCHIVE_ADMIN_METADATA_PAGE_LIMIT",
            2,
        ):
            first = await self._post(
                "/internal/conversation-archive/admin/participation",
                {},
                purpose="control-proxy",
                cookie=cookie,
            )
            self.assertEqual(first.status, 200, await first.text())
            first_payload = await first.json()
            self.assertEqual(len(first_payload["intervals"]), 2)
            cursor = first_payload["nextCursor"]
            self.assertRegex(cursor, r"^[0-9a-f]{64}$")
            self.assertNotIn(USER_ID, cursor)
            self.assertNotIn(VOICE_CHANNEL_ID, cursor)

            second = await self._post(
                "/internal/conversation-archive/admin/participation",
                {"cursor": cursor},
                purpose="control-proxy",
                cookie=cookie,
            )
            self.assertEqual(second.status, 200, await second.text())
            self.assertEqual(len((await second.json())["intervals"]), 1)

            wrong_kind = await self._post(
                "/internal/conversation-archive/admin/legal-minimal",
                {"cursor": cursor},
                purpose="control-proxy",
                cookie=cookie,
            )
            self.assertEqual(wrong_kind.status, 400, await wrong_kind.text())

            voice_first = await self._post(
                "/internal/conversation-archive/admin/voice-state-transitions",
                {},
                purpose="control-proxy",
                cookie=cookie,
            )
            self.assertEqual(voice_first.status, 200, await voice_first.text())
            voice_payload = await voice_first.json()
            self.assertEqual(len(voice_payload["transitions"]), 2)
            self.assertRegex(voice_payload["nextCursor"], r"^[0-9a-f]{64}$")
            self.assertNotIn(USER_ID, voice_payload["nextCursor"])
            voice_second = await self._post(
                "/internal/conversation-archive/admin/voice-state-transitions",
                {"cursor": voice_payload["nextCursor"]},
                purpose="control-proxy",
                cookie=cookie,
            )
            self.assertEqual(voice_second.status, 200, await voice_second.text())
            self.assertEqual(len((await voice_second.json())["transitions"]), 1)

        routes = {
            resource.canonical
            for resource in self.client.server.app.router.resources()
        }
        self.assertIn(
            "/internal/conversation-archive/admin/participation",
            routes,
        )
        self.assertIn(
            "/internal/conversation-archive/admin/legal-minimal",
            routes,
        )
        self.assertIn(
            "/internal/conversation-archive/admin/voice-state-transitions",
            routes,
        )

    async def test_admin_metadata_cursor_rejects_restart_expiry_and_stale_generation(
        self,
    ) -> None:
        await self._activate_discord()
        for sequence, muted in ((1, False), (2, True), (3, False)):
            self.assertEqual(
                (await self._voice_state(sequence=sequence, muted=muted)).status,
                200,
            )
        cookie = await self._admin_login()
        runtime = self.client.server.app[
            fast_api.CONVERSATION_ARCHIVE_RUNTIME_KEY
        ]
        with patch.object(
            fast_api,
            "CONVERSATION_ARCHIVE_ADMIN_METADATA_PAGE_LIMIT",
            2,
        ):
            first = await self._post(
                "/internal/conversation-archive/admin/voice-state-transitions",
                {},
                purpose="control-proxy",
                cookie=cookie,
            )
            cursor = (await first.json())["nextCursor"]
            runtime.admin_metadata_handles.clear()
            restarted = await self._post(
                "/internal/conversation-archive/admin/voice-state-transitions",
                {"cursor": cursor},
                purpose="control-proxy",
                cookie=cookie,
            )
            self.assertEqual(restarted.status, 409, await restarted.text())

            fresh = await self._post(
                "/internal/conversation-archive/admin/voice-state-transitions",
                {},
                purpose="control-proxy",
                cookie=cookie,
            )
            expired_cursor = (await fresh.json())["nextCursor"]
            original_clock = runtime.clock
            runtime.clock = lambda: original_clock() + 181
            try:
                expired = await self._post(
                    "/internal/conversation-archive/admin/voice-state-transitions",
                    {"cursor": expired_cursor},
                    purpose="control-proxy",
                    cookie=cookie,
                )
            finally:
                runtime.clock = original_clock
            self.assertEqual(expired.status, 409, await expired.text())

            stale_first = await self._post(
                "/internal/conversation-archive/admin/voice-state-transitions",
                {},
                purpose="control-proxy",
                cookie=cookie,
            )
            stale_cursor = (await stale_first.json())["nextCursor"]
            appended = await self._append_user_record(
                sequence=4,
                record_id="metadata-cursor-generation-change",
            )
            self.assertEqual(appended.status, 200, await appended.text())
            stale = await self._post(
                "/internal/conversation-archive/admin/voice-state-transitions",
                {"cursor": stale_cursor},
                purpose="control-proxy",
                cookie=cookie,
            )
            self.assertEqual(stale.status, 409, await stale.text())

            live = await self._post(
                "/internal/conversation-archive/admin/voice-state-transitions",
                {},
                purpose="control-proxy",
                cookie=cookie,
            )
            live_cursor = (await live.json())["nextCursor"]
            self.assertTrue(runtime.admin_metadata_handles)
            logout = await self._post(
                "/internal/conversation-archive/admin/logout",
                {},
                purpose="control-proxy",
                cookie=cookie,
            )
            self.assertEqual(logout.status, 200, await logout.text())
            self.assertFalse(runtime.admin_metadata_handles)
            revoked = await self._post(
                "/internal/conversation-archive/admin/voice-state-transitions",
                {"cursor": live_cursor},
                purpose="control-proxy",
                cookie=cookie,
            )
            self.assertNotEqual(revoked.status, 200, await revoked.text())

    async def test_admin_legal_minimal_reads_all_5001_rows_in_bounded_pages(
        self,
    ) -> None:
        cookie = await self._admin_login()
        runtime = self.client.server.app[
            fast_api.CONVERSATION_ARCHIVE_RUNTIME_KEY
        ]
        archive = runtime.archive
        self.assertIsNotNone(archive)
        rows = tuple(
            archive_core.LegalMinimalEvent(
                event_id=f"internal-event-{index}",
                owner_name=f"사용자 {index}",
                occurred_at=datetime.now(timezone.utc),
            )
            for index in range(5001)
        )

        def read_page(*, authorized, cursor=None, limit=100):
            self.assertTrue(authorized)
            self.assertLessEqual(limit, 100)
            offset = 0 if cursor is None else int(str(cursor).split(":", 1)[1])
            page_rows = rows[offset : offset + limit]
            next_offset = offset + len(page_rows)
            return archive_core.LegalMinimalEventPage(
                events=page_rows,
                next_cursor=(
                    f"offset:{next_offset}" if next_offset < len(rows) else None
                ),
                snapshot_generation=archive.generation,
            )

        count = 0
        cursor = None
        with (
            patch.object(
                archive,
                "read_legal_minimal_events_page",
                side_effect=read_page,
            ) as paged,
            patch.object(
                archive,
                "read_legal_minimal_events",
                side_effect=AssertionError("bulk legal read used"),
            ),
        ):
            while True:
                response = await self._post(
                    "/internal/conversation-archive/admin/legal-minimal",
                    {} if cursor is None else {"cursor": cursor},
                    purpose="control-proxy",
                    cookie=cookie,
                )
                self.assertEqual(response.status, 200, await response.text())
                payload = await response.json()
                self.assertLessEqual(len(payload["events"]), 100)
                self.assertNotIn("internal-event-", await response.text())
                count += len(payload["events"])
                cursor = payload["nextCursor"]
                if cursor is None:
                    break
        self.assertEqual(count, 5001)
        self.assertEqual(paged.call_count, 51)
        routes = {
            resource.canonical
            for resource in self.client.server.app.router.resources()
        }
        self.assertNotIn(
            "/internal/conversation-archive/self/participation",
            routes,
        )
        self.assertNotIn(
            "/internal/conversation-archive/self/legal-minimal",
            routes,
        )

    async def test_admin_session_is_revoked_when_host_session_is_revoked(
        self,
    ) -> None:
        cookie = await self._admin_login()
        self._write_host_session(int(time.time()), state="revoked")
        records = await self._post(
            "/internal/conversation-archive/admin/records",
            {},
            purpose="control-proxy",
            cookie=cookie,
        )
        self.assertEqual(records.status, 403, await records.text())

    async def test_new_step_up_code_invalidates_prior_code_in_same_session(
        self,
    ) -> None:
        await self._activate_discord()
        await self._append_user_record()
        cookie = await self._admin_login()
        previews = []
        for _ in range(2):
            response = await self._post(
                "/internal/conversation-archive/admin/delete/preview",
                {"recordIds": ["discord-user-record-1"]},
                purpose="control-proxy",
                cookie=cookie,
            )
            self.assertEqual(response.status, 200, await response.text())
            previews.append((await response.json())["previewToken"])
        poll = await self._post(
            "/internal/conversation-archive/admin/otp-delivery/poll",
            {},
            purpose="otp-delivery",
        )
        deliveries = (await poll.json())["deliveries"]
        self.assertEqual(len(deliveries), 1)
        code = deliveries[0]["code"]
        stale = await self._post(
            "/internal/conversation-archive/admin/delete/apply",
            {"previewToken": previews[0], "code": code},
            purpose="control-proxy",
            cookie=cookie,
        )
        self.assertEqual(stale.status, 403, await stale.text())
        current = await self._post(
            "/internal/conversation-archive/admin/delete/apply",
            {"previewToken": previews[1], "code": code},
            purpose="control-proxy",
            cookie=cookie,
        )
        self.assertEqual(current.status, 200, await current.text())

    async def test_admin_challenge_is_bound_to_one_attested_bootstrap_nonce(
        self,
    ) -> None:
        missing = await self._post(
            "/internal/conversation-archive/admin/challenge",
            {},
            purpose="control-proxy",
        )
        self.assertEqual(missing.status, 400)
        forged = await self._post(
            "/internal/conversation-archive/admin/challenge",
            {"bootstrapNonce": "x" * 43},
            purpose="control-proxy",
        )
        self.assertEqual(forged.status, 400)
        exact = await self._post(
            "/internal/conversation-archive/admin/challenge",
            {"bootstrapNonce": BOOTSTRAP_NONCE},
            purpose="control-proxy",
        )
        self.assertEqual(exact.status, 200, await exact.text())
        reused = await self._post(
            "/internal/conversation-archive/admin/challenge",
            {"bootstrapNonce": BOOTSTRAP_NONCE},
            purpose="control-proxy",
        )
        self.assertEqual(reused.status, 403, await reused.text())

    async def test_admin_proxy_is_bound_to_configured_loopback_origin(self) -> None:
        wrong_origin = await self._post(
            "/internal/conversation-archive/admin/challenge",
            {"bootstrapNonce": BOOTSTRAP_NONCE},
            purpose="control-proxy",
            control_evidence=(
                "https",
                "127.0.0.1:9999",
                "https://127.0.0.1:9999",
            ),
        )
        self.assertEqual(wrong_origin.status, 403, await wrong_origin.text())

    async def test_minecraft_result_infers_parent_scope_and_requires_parent(self) -> None:
        await self._activate_discord()
        await self._append_user_record(kind="minecraft_command")
        generation = await self._post(
            "/internal/conversation-archive/minecraft/generation",
            {},
            purpose="minecraft",
        )
        self.assertEqual(generation.status, 200)
        generation_id = (await generation.json())["generation"]
        ready = await self._post(
            "/internal/conversation-archive/minecraft/ready",
            {"generation": generation_id},
            purpose="minecraft",
        )
        self.assertTrue((await ready.json())["ready"])
        now = time.time()
        body = _minecraft_body(now)
        rejected = await self._post(
            "/internal/conversation-archive/minecraft/record",
            {
                "generation": generation_id,
                "sequence": 1,
                "idempotencyKey": "minecraft-no-parent",
                "recordId": "minecraft-result-no-parent",
                "startedAt": now,
                "endedAt": now,
                "parentRecordIds": [],
                "body": body,
            },
            purpose="minecraft",
        )
        self.assertEqual(rejected.status, 400)
        accepted = await self._post(
            "/internal/conversation-archive/minecraft/record",
            {
                "generation": generation_id,
                "sequence": 1,
                "idempotencyKey": "minecraft-result-1",
                "recordId": "minecraft-result-1",
                "startedAt": now,
                "endedAt": now,
                "parentRecordIds": ["discord-user-record-1"],
                "body": body,
            },
            purpose="minecraft",
        )
        self.assertEqual(accepted.status, 200, await accepted.text())
        runtime = self.client.server.app[fast_api.CONVERSATION_ARCHIVE_RUNTIME_KEY]
        page = runtime.archive.read_admin_page(authorized=True, limit=10)
        stored = page.records[-1]
        self.assertEqual(
            stored.body,
            "마인크래프트 작업 검증 완료: "
            "collect_food · food_reserve_ready · inventory_increased",
        )
        self.assertNotIn("goal-run-1", stored.body)

    async def test_minecraft_event_rejects_raw_fields_and_false_claims(self) -> None:
        await self._activate_discord()
        await self._append_user_record(kind="minecraft_command")
        generation_response = await self._post(
            "/internal/conversation-archive/minecraft/generation",
            {},
            purpose="minecraft",
        )
        generation = (await generation_response.json())["generation"]
        now = time.time()
        for index, body in enumerate(
            (
                _minecraft_body(now, command="/give @p diamond"),
                _minecraft_body(now, params={"item": "diamond"}),
                _minecraft_body(now, verified=False),
                "raw private result",
            ),
            start=1,
        ):
            rejected = await self._post(
                "/internal/conversation-archive/minecraft/record",
                {
                    "generation": generation,
                    "sequence": 1,
                    "idempotencyKey": f"minecraft-invalid-{index}",
                    "recordId": f"minecraft-invalid-{index}",
                    "startedAt": now,
                    "endedAt": now,
                    "parentRecordIds": ["discord-user-record-1"],
                    "body": body,
                },
                purpose="minecraft",
            )
            self.assertEqual(rejected.status, 400, await rejected.text())

    async def test_minecraft_generation_is_server_owned_and_never_resets_sequence(
        self,
    ) -> None:
        await self._activate_discord()
        await self._append_user_record(kind="minecraft_command")
        first = await self._post(
            "/internal/conversation-archive/minecraft/generation",
            {},
            purpose="minecraft",
        )
        first_generation = (await first.json())["generation"]
        forged_activation = await self._post(
            "/internal/conversation-archive/minecraft/generation",
            {"generation": "client-owned-generation"},
            purpose="minecraft",
        )
        self.assertEqual(forged_activation.status, 400)
        second = await self._post(
            "/internal/conversation-archive/minecraft/generation",
            {},
            purpose="minecraft",
        )
        self.assertEqual((await second.json())["generation"], first_generation)
        now = time.time()
        first_record = await self._post(
            "/internal/conversation-archive/minecraft/record",
            {
                "generation": first_generation,
                "sequence": 1,
                "idempotencyKey": "minecraft-currentness-1",
                "recordId": "minecraft-currentness-1",
                "startedAt": now,
                "endedAt": now,
                "parentRecordIds": ["discord-user-record-1"],
                "body": _minecraft_body(now),
            },
            purpose="minecraft",
        )
        self.assertEqual(first_record.status, 200, await first_record.text())
        await self._post(
            "/internal/conversation-archive/minecraft/generation",
            {},
            purpose="minecraft",
        )
        stale_sequence = await self._post(
            "/internal/conversation-archive/minecraft/record",
            {
                "generation": first_generation,
                "sequence": 1,
                "idempotencyKey": "minecraft-currentness-stale",
                "recordId": "minecraft-currentness-stale",
                "startedAt": now,
                "endedAt": now,
                "parentRecordIds": ["discord-user-record-1"],
                "body": _minecraft_body(now),
            },
            purpose="minecraft",
        )
        self.assertEqual(stale_sequence.status, 409, await stale_sequence.text())
        stale_generation = await self._post(
            "/internal/conversation-archive/minecraft/record",
            {
                "generation": "old-process-generation",
                "sequence": 2,
                "idempotencyKey": "minecraft-old-process",
                "recordId": "minecraft-old-process",
                "startedAt": now,
                "endedAt": now,
                "parentRecordIds": ["discord-user-record-1"],
                "body": _minecraft_body(now),
            },
            purpose="minecraft",
        )
        self.assertEqual(stale_generation.status, 409, await stale_generation.text())

    async def test_self_delete_without_period_removes_all_guilds(self) -> None:
        await self._activate_discord()
        await self._append_user_record()
        now = datetime.now(timezone.utc).isoformat()
        second = await self._post(
            "/internal/conversation-archive/record",
            {
                "generation": "discord-boot-a",
                "sequence": 2,
                "idempotencyKey": "record-other-guild",
                "recordId": "discord-user-record-2",
                "guildId": "623456789012345678",
                "channelId": "723456789012345678",
                "kind": "user_text",
                "startedAt": now,
                "endedAt": now,
                "sourceUserId": USER_ID,
                "ownerName": "참여자",
                "parentRecordIds": [],
                "body": "other guild body",
            },
            purpose="ingest",
        )
        self.assertEqual(second.status, 200)
        preview_interaction = "723456789012345678"
        preview_handle = await self._authorize_self(
            "delete-preview",
            interaction_id=preview_interaction,
        )
        preview = await self._self_action(
            "/internal/conversation-archive/self/delete/preview",
            handle=preview_handle,
            interaction_id=preview_interaction,
        )
        preview_payload = await preview.json()
        self.assertTrue(preview_payload["allGuilds"])
        self.assertEqual(preview_payload["ownedRecordCount"], 2)
        apply_interaction = "823456789012345678"
        apply_handle = await self._authorize_self(
            "delete-apply",
            interaction_id=apply_interaction,
            previewId=preview_payload["previewId"],
        )
        applied = await self._self_action(
            "/internal/conversation-archive/self/delete/apply",
            handle=apply_handle,
            interaction_id=apply_interaction,
        )
        self.assertEqual(applied.status, 200, await applied.text())
        read_interaction = "923456789012345678"
        read_handle = await self._authorize_self(
            "records",
            interaction_id=read_interaction,
            guild_id="623456789012345678",
        )
        other = await self._self_action(
            "/internal/conversation-archive/self/records",
            handle=read_handle,
            interaction_id=read_interaction,
            guild_id="623456789012345678",
        )
        rows = (await other.json())["records"]
        self.assertNotIn("other guild body", [row["body"] for row in rows])

    async def test_admin_cursor_is_snapshot_bound(self) -> None:
        await self._activate_discord()
        for sequence in range(1, 12):
            response = await self._append_user_record(
                sequence=sequence,
                record_id=f"discord-page-record-{sequence}",
                body=f"body-{sequence}",
            )
            self.assertEqual(response.status, 200, await response.text())
        cookie = await self._admin_login()
        first = await self._post(
            "/internal/conversation-archive/admin/records",
            {},
            purpose="control-proxy",
            cookie=cookie,
        )
        first_payload = await first.json()
        self.assertEqual(len(first_payload["records"]), 2)
        self.assertTrue(first_payload["nextCursor"])
        appended = await self._append_user_record(
            sequence=12,
            record_id="discord-page-record-12",
            body="body-12",
        )
        self.assertEqual(appended.status, 200, await appended.text())
        stale = await self._post(
            "/internal/conversation-archive/admin/records",
            {"cursor": first_payload["nextCursor"]},
            purpose="control-proxy",
            cookie=cookie,
        )
        self.assertEqual(stale.status, 409, await stale.text())

    async def test_consumed_startup_attestation_cannot_open_second_writer(self) -> None:
        await self.client.close()
        app = fast_api.create_app(
            enable_minecraft_world_lease_owner=False,
            conversation_archive_enabled=True,
            conversation_archive_options=self.options,
        )
        app.cleanup_ctx.remove(fast_api.fast_main_llm_warmup_context)
        second = TestClient(TestServer(app))
        with patch.object(
            archive_core.ConversationArchive,
            "open",
            side_effect=AssertionError("archive opened before replay rejection"),
        ):
            with self.assertRaisesRegex(
                RuntimeError, "archive_startup_attestation_replayed"
            ):
                await second.start_server()
        await second.close()

    async def test_one_shot_restore_uses_only_current_attested_replica(
        self,
    ) -> None:
        await self._activate_discord()
        appended = await self._append_user_record(body="replica survives")
        self.assertEqual(appended.status, 200, await appended.text())
        await self.client.close()
        with closing(sqlite3.connect(self.options["primary_path"])) as connection:
            connection.execute(
                "UPDATE records SET body = 'corrupt primary' "
                "WHERE record_id = 'discord-user-record-1'"
            )
            connection.commit()
        self._write_attestation(
            int(time.time()),
            bootstrap_nonce="c" * 43,
        )

        result = await asyncio.to_thread(
            fast_api._restore_conversation_archive,
            self.options,
        )

        self.assertEqual(result["state"], "restored")
        self.assertTrue(result["contentFree"])
        archive = archive_core.ConversationArchive(
            primary_path=self.options["primary_path"],
            replica_path=self.options["replica_path"],
            anchor_path=self.options["anchor_path"],
            integrity_key=fast_api._conversation_archive_subkey(
                AUTH_KEY,
                fast_api._CONVERSATION_ARCHIVE_INTEGRITY_KEY_DOMAIN,
            ),
            lineage_key=fast_api._conversation_archive_subkey(
                INGEST_KEY,
                fast_api._CONVERSATION_ARCHIVE_PURGE_LINEAGE_KEY_DOMAIN,
            ),
            required_purge_sinks=archive_core.ARCHIVE_REQUIRED_PURGE_SINKS,
        ).open()
        try:
            records = archive.read_self(
                actor_external_id=USER_ID,
                guild_id=GUILD_ID,
            )
            self.assertEqual([record.body for record in records], ["replica survives"])
        finally:
            archive.close()

    async def test_attestation_is_bound_to_registered_discord_identity(self) -> None:
        await self.client.close()
        self._write_attestation(
            int(time.time()),
            registered_discord_user_id="999999999999999999",
        )
        app = fast_api.create_app(
            enable_minecraft_world_lease_owner=False,
            conversation_archive_enabled=True,
            conversation_archive_options=self.options,
        )
        app.cleanup_ctx.remove(fast_api.fast_main_llm_warmup_context)
        candidate = TestClient(TestServer(app))
        with patch.object(
            archive_core.ConversationArchive,
            "open",
            side_effect=AssertionError("archive opened before identity rejection"),
        ):
            with self.assertRaisesRegex(
                admin.AdminSecurityError, "admin_identity_mismatch"
            ):
                await candidate.start_server()
        await candidate.close()

    async def test_local_chat_archives_user_and_final_reply_before_response(self) -> None:
        token = "local-control-token-that-is-at-least-32-bytes"
        with (
            patch.object(fast_api, "EVELYN_INTERNAL_CONTROL_TOKEN", token),
            patch.object(fast_api, "fast_main_llm_warmup_ready", return_value=True),
            patch.object(
                fast_api,
                "resolve_pre_llm_reply",
                new=AsyncMock(return_value="확정 답변"),
            ),
            patch.object(
                fast_api,
                "_prepare_fast_control_ingress",
                return_value=(None, None, None),
            ),
            patch.object(
                fast_api,
                "commit_fast_control_turn",
                return_value={"durable": True},
            ),
        ):
            response = await self.client.post(
                "/api/control-page/chat",
                json={"text": "로컬 질문", "requestId": "local-turn-0001"},
                headers={fast_api.EVELYN_INTERNAL_CONTROL_HEADER: token},
            )
        self.assertEqual(response.status, 200, await response.text())
        runtime = self.client.server.app[fast_api.CONVERSATION_ARCHIVE_RUNTIME_KEY]
        page = runtime.archive.read_admin_page(authorized=True, limit=10)
        bodies = [record.body for record in page.records]
        self.assertEqual(bodies[-2:], ["로컬 질문", "확정 답변"])
        self.assertEqual(page.records[-1].record_type, "evelyn_reply")
        with closing(sqlite3.connect(self.options["primary_path"])) as connection:
            lineage_rows = connection.execute(
                "SELECT record_type, lineage_json FROM records "
                "WHERE body IN (?, ?) ORDER BY created_seq",
                ("로컬 질문", "확정 답변"),
            ).fetchall()
        expected_kinds = {
            "turn",
            "session",
            "memory_owner",
            "memory_evidence",
        }
        self.assertEqual(len(lineage_rows), 2)
        first_lineage = json.loads(lineage_rows[0][1])
        second_lineage = json.loads(lineage_rows[1][1])
        self.assertEqual({item["kind"] for item in first_lineage}, expected_kinds)
        self.assertEqual(second_lineage, first_lineage)

    async def test_local_stream_buffers_delivery_until_final_reply_is_archived(self) -> None:
        token = "local-control-token-that-is-at-least-32-bytes"
        with (
            patch.object(fast_api, "EVELYN_INTERNAL_CONTROL_TOKEN", token),
            patch.object(fast_api, "fast_main_llm_warmup_ready", return_value=True),
            patch.object(
                fast_api,
                "resolve_pre_llm_reply",
                new=AsyncMock(return_value="스트림 확정 답변"),
            ),
            patch.object(
                fast_api,
                "_prepare_fast_control_ingress",
                return_value=(None, None, None),
            ),
            patch.object(
                fast_api,
                "commit_fast_control_turn",
                return_value={"durable": True},
            ),
        ):
            response = await self.client.post(
                "/api/control-page/chat-stream",
                json={"text": "스트림 질문", "requestId": "local-stream-0001"},
                headers={fast_api.EVELYN_INTERNAL_CONTROL_HEADER: token},
            )
            encoded = await response.text()
        self.assertEqual(response.status, 200, encoded)
        events = [json.loads(line) for line in encoded.splitlines() if line]
        self.assertEqual(events[-1]["type"], "done")
        runtime = self.client.server.app[fast_api.CONVERSATION_ARCHIVE_RUNTIME_KEY]
        page = runtime.archive.read_admin_page(authorized=True, limit=10)
        self.assertEqual(
            [record.body for record in page.records][-2:],
            ["스트림 질문", "스트림 확정 답변"],
        )

    async def test_bound_task_terminal_uses_exact_local_conversation_parent(self) -> None:
        runtime = self.client.server.app[fast_api.CONVERSATION_ARCHIVE_RUNTIME_KEY]
        now = datetime.now(timezone.utc)
        parent = runtime.archive.append_record(
            mode="local_private",
            surface="local",
            record_type="user_text",
            body="작업 요청",
            started_at=now,
            ended_at=now,
            actor_external_id="control-page:local",
            owner_name="정훈",
            idempotency_key="task-parent",
            record_id="local-task-parent",
        )
        task = fast_api.ACTION_COORDINATOR.start(
            kind="test",
            source="control_page",
            user_text="작업 요청",
            start_reply="시작",
        )
        fast_api.CONVERSATION_ARCHIVE_LOCAL_TASK_BINDINGS[task.task_id] = (
            runtime,
            parent.record_id,
            "task-turn-key",
        )
        try:
            await fast_api._conversation_archive_append_task_terminal(
                task,
                body="검증된 작업 결과",
                outcome="completed",
            )
        finally:
            fast_api.CONVERSATION_ARCHIVE_LOCAL_TASK_BINDINGS.pop(
                task.task_id, None
            )
            fast_api.ACTION_COORDINATOR.clear()
        page = runtime.archive.read_admin_page(authorized=True, limit=10)
        terminal = page.records[-1]
        self.assertEqual(terminal.record_type, "task_result")
        self.assertEqual(terminal.body, "검증된 작업 결과")

    async def test_archive_failure_cannot_leave_background_task_running(self) -> None:
        fast_api.ACTION_COORDINATOR.clear()
        task = fast_api.ACTION_COORDINATOR.start(
            kind="test",
            source="control_page",
            user_text="작업 요청",
            start_reply="시작",
        )

        async def runner(_text: str, _source: str) -> str:
            return "완료 결과"

        try:
            with (
                patch.object(
                    fast_api,
                    "_conversation_archive_append_task_terminal",
                    new=AsyncMock(side_effect=RuntimeError("archive down")),
                ),
                patch.object(fast_api, "append_chat_message"),
                patch.object(fast_api, "queue_local_bridge_speech"),
                patch.object(
                    fast_api.FAST_ACTION_RECOVERY_JOURNAL,
                    "mark_interrupted",
                ),
            ):
                await fast_api.launch_background_action(task, runner)
                await asyncio.sleep(0)
            self.assertEqual(task.status, "failed")
            self.assertEqual(task.error, "conversation_archive_unavailable")
        finally:
            fast_api.ACTION_COORDINATOR.clear()


class ConversationArchiveDisabledTests(unittest.TestCase):
    def test_missing_voice_debug_root_remains_unconfigured(self) -> None:
        environment = "EVELYN_CONVERSATION_ARCHIVE_PURGE_VOICE_DEBUG_ROOT"
        with patch.dict(os.environ, {environment: ""}):
            for raw in (None, "", " \t "):
                with self.subTest(raw=raw):
                    options = fast_api._conversation_archive_env_options(
                        {"purge_voice_debug_root": raw}
                    )
                    self.assertIsNone(options["purge_voice_debug_root"])

    def test_default_off_does_not_resolve_paths_or_register_archive_routes(self) -> None:
        with patch.object(
            fast_api,
            "_conversation_archive_env_options",
            side_effect=AssertionError("archive configuration was accessed"),
        ):
            app = fast_api.create_app(
                enable_minecraft_world_lease_owner=False,
                conversation_archive_enabled=False,
            )
        self.assertNotIn(fast_api.CONVERSATION_ARCHIVE_RUNTIME_KEY, app)
        paths = {resource.canonical for resource in app.router.resources()}
        self.assertNotIn("/internal/conversation-archive/status", paths)


if __name__ == "__main__":
    unittest.main()
