from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


REPO_ROOT = next(
    path for path in Path(__file__).resolve().parents if (path / "main.py").exists()
)
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core import mindcraft_service  # noqa: E402
from evelyn_core.mindcraft_conversation_archive_runtime import (  # noqa: E402
    MindcraftConversationArchiveClient,
)


MASTER_KEY = b"m" * 32
GENERATION = "generation-1"


def verified_event(**updates: object) -> dict[str, object]:
    event: dict[str, object] = {
        "schema": "conversation.archive.minecraft-result.v1",
        "eventType": "minecraft_result",
        "mode": "discord_shared",
        "surface": "minecraft",
        "recordType": "minecraft_result",
        "guildId": "7",
        "parentRecordIds": ["grant-1"],
        "goalRunId": "goal-run-1",
        "actionRunId": "action-run-1",
        "actionKey": "minecraft:find_food_source",
        "contractCode": "mindcraft_food_recovery.v1",
        "candidateSequence": 1,
        "executionSequence": 2,
        "observedAt": 1_800_000_000.25,
        "evidenceCode": "verified_inventory_delta",
        "postconditionCode": "food_reserve_increased",
        "verified": True,
        "succeeded": True,
        "worldChanged": True,
        "goalProgress": True,
        "idempotencyKey": "minecraft-result:action-run-1:2",
        "contentFree": True,
    }
    event.update(updates)
    return event


class ArchiveServerStub:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.active = False
        self.last_sequence = 0

    def __call__(
        self,
        method: str,
        url: str,
        body: bytes,
        headers: object,
        timeout: float,
    ) -> tuple[int, bytes]:
        header_map = dict(headers)  # type: ignore[arg-type]
        path = "/" + url.split("/", 3)[3]
        payload = json.loads(body.decode("utf-8"))
        timestamp = header_map["X-Evelyn-Archive-Timestamp"]
        nonce = header_map["X-Evelyn-Archive-Nonce"]
        subkey = hmac.new(
            MASTER_KEY,
            b"evelyn.private-conversation-archive.transport-key.v1\nminecraft",
            hashlib.sha256,
        ).digest()
        canonical = "\n".join(
            (
                "minecraft",
                method,
                path,
                timestamp,
                nonce,
                hashlib.sha256(body).hexdigest(),
            )
        ).encode("utf-8")
        self.assert_equal(
            header_map["X-Evelyn-Archive-Signature"],
            hmac.new(subkey, canonical, hashlib.sha256).hexdigest(),
        )
        self.calls.append(
            {
                "path": path,
                "body": body,
                "payload": payload,
                "nonce": nonce,
                "timeout": timeout,
            }
        )
        if path.endswith("/generation"):
            self.active = True
            response = {
                "ok": True,
                "generation": GENERATION,
            }
        elif path.endswith("/ready"):
            response = {
                "ok": True,
                "ready": self.active,
                "state": "healthy",
                "contentFree": True,
            }
        else:
            self.last_sequence = payload["sequence"]
            response = {"ok": True, "recordId": payload["recordId"]}
        return 200, json.dumps(response).encode("utf-8")

    @staticmethod
    def assert_equal(left: object, right: object) -> None:
        if left != right:
            raise AssertionError(f"{left!r} != {right!r}")


class MindcraftConversationArchiveClientTests(unittest.TestCase):
    def client(self, transport: object) -> MindcraftConversationArchiveClient:
        nonces = iter(f"{value:032x}" for value in range(1, 30))
        return MindcraftConversationArchiveClient(
            base_url="http://bot-api:8798",
            master_key=MASTER_KEY,
            http_request=transport,  # type: ignore[arg-type]
            clock=lambda: 1_800_000_000.0,
            nonce_factory=lambda _size: next(nonces),
        )

    def test_ready_and_verified_effect_use_signed_exact_lineage(self) -> None:
        server = ArchiveServerStub()
        client = self.client(server)

        self.assertEqual(client.validate_ready(), (True, ""))
        self.assertEqual(client.archive_verified_effect(verified_event()), (True, ""))
        self.assertEqual(
            client.archive_verified_effect(
                verified_event(
                    actionRunId="action-run-2",
                    executionSequence=3,
                    idempotencyKey="minecraft-result:action-run-2:3",
                )
            ),
            (True, ""),
        )

        records = [
            call["payload"]
            for call in server.calls
            if str(call["path"]).endswith("/record")
        ]
        self.assertEqual([row["sequence"] for row in records], [1, 2])
        self.assertEqual(records[0]["parentRecordIds"], ["grant-1"])
        self.assertEqual(set(records[0]), {
            "generation",
            "sequence",
            "idempotencyKey",
            "recordId",
            "startedAt",
            "endedAt",
            "parentRecordIds",
            "body",
        })
        body = json.loads(str(records[0]["body"]))
        self.assertTrue(body["contentFree"])
        self.assertNotIn("guildId", body)
        self.assertNotIn("parentRecordIds", body)
        self.assertNotIn("parameters", body)

    def test_transport_retry_reuses_body_with_fresh_nonce(self) -> None:
        server = ArchiveServerStub()
        attempts: list[tuple[bytes, str]] = []

        def flaky(
            method: str,
            url: str,
            body: bytes,
            headers: object,
            timeout: float,
        ) -> tuple[int, bytes]:
            attempts.append(
                (body, dict(headers)["X-Evelyn-Archive-Nonce"])  # type: ignore[arg-type]
            )
            if len(attempts) == 1:
                raise OSError("response_lost")
            return server(method, url, body, headers, timeout)

        self.assertEqual(self.client(flaky).validate_ready(), (True, ""))
        self.assertEqual(attempts[0][0], attempts[1][0])
        self.assertNotEqual(attempts[0][1], attempts[1][1])

    def test_unverified_or_expanded_event_is_rejected_before_http(self) -> None:
        transport = Mock()
        client = self.client(transport)

        expanded = verified_event(parameters={"command": "private"})
        self.assertEqual(
            client.archive_verified_effect(expanded),
            (False, "mindcraft_world_effect_archive_event_invalid"),
        )
        self.assertEqual(
            client.archive_verified_effect(verified_event(verified=False)),
            (False, "mindcraft_world_effect_archive_event_invalid"),
        )
        transport.assert_not_called()

    def test_record_receipt_must_match_exact_record_id(self) -> None:
        server = ArchiveServerStub()

        def mismatched(
            method: str,
            url: str,
            body: bytes,
            headers: object,
            timeout: float,
        ) -> tuple[int, bytes]:
            status, response = server(method, url, body, headers, timeout)
            if url.endswith("/record"):
                response = b'{"ok":true,"recordId":"wrong"}'
            return status, response

        self.assertEqual(
            self.client(mismatched).archive_verified_effect(verified_event()),
            (False, "archive_record_receipt_invalid"),
        )

    def test_key_file_must_exist_and_contain_at_least_32_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            key_path = Path(temporary) / "minecraft.key"
            with self.assertRaisesRegex(ValueError, "key_file_invalid"):
                MindcraftConversationArchiveClient.from_key_file(
                    base_url="http://bot-api:8798",
                    key_file=key_path,
                )
            key_path.write_bytes(b"short")
            with self.assertRaisesRegex(ValueError, "key_too_short"):
                MindcraftConversationArchiveClient.from_key_file(
                    base_url="http://bot-api:8798",
                    key_file=key_path,
                )


class MindcraftArchiveEnvironmentTests(unittest.TestCase):
    def test_disabled_default_does_not_read_a_key(self) -> None:
        with patch.dict(os.environ, {}, clear=True), patch.object(
            mindcraft_service.MindcraftConversationArchiveClient,
            "from_key_file",
        ) as load:
            self.assertEqual(
                mindcraft_service._conversation_archive_callbacks_from_environment(),
                (False, None, None),
            )
            load.assert_not_called()

    def test_only_literal_true_builds_archive_callbacks(self) -> None:
        fake = Mock()
        fake.archive_verified_effect = Mock()
        fake.validate_ready = Mock()
        environment = {
            "EVELYN_CONVERSATION_ARCHIVE_ENABLED": "true",
            "EVELYN_CONVERSATION_ARCHIVE_BOT_API_URL": "http://bot_api:8798",
            "EVELYN_CONVERSATION_ARCHIVE_MINECRAFT_KEY_FILE": "/run/key",
        }
        with patch.dict(os.environ, environment, clear=True), patch.object(
            mindcraft_service.MindcraftConversationArchiveClient,
            "from_key_file",
            return_value=fake,
        ) as load:
            enabled, sink, ready = (
                mindcraft_service._conversation_archive_callbacks_from_environment()
            )
        self.assertTrue(enabled)
        self.assertIs(sink, fake.archive_verified_effect)
        self.assertIs(ready, fake.validate_ready)
        load.assert_called_once_with(
            base_url="http://bot_api:8798", key_file="/run/key"
        )

        with patch.dict(
            os.environ,
            {"EVELYN_CONVERSATION_ARCHIVE_ENABLED": "TRUE"},
            clear=True,
        ):
            self.assertEqual(
                mindcraft_service._conversation_archive_callbacks_from_environment(),
                (False, None, None),
            )


if __name__ == "__main__":
    unittest.main()
