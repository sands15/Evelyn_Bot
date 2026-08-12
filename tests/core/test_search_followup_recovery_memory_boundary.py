from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = next(
    path
    for path in Path(__file__).resolve().parents
    if (path / "main.py").exists()
)
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core import memory_deletion_journal as deletion_journal  # noqa: E402
from evelyn_core.conversation_memory_receipt import (  # noqa: E402
    CONVERSATION_MEMORY_RECEIPT_REF_SCHEMA,
    not_used_memory_receipt_ref,
    unattributed_memory_receipt_ref,
)
from evelyn_core.memory_exposure import MEMORY_INDEX_DB_NAME  # noqa: E402
from evelyn_core.memory_integrity_authenticity import (  # noqa: E402
    MEMORY_INTEGRITY_ANCHOR_DIR_ENV,
    MEMORY_INTEGRITY_BOOTSTRAP_ENV,
    MEMORY_INTEGRITY_KEY_FILE_ENV,
)
from evelyn_core.search_followup_recovery import (  # noqa: E402
    SearchFollowupRecoveryJournal,
)
from evelyn_core.search_followup_runtime import (  # noqa: E402
    SearchFollowupRuntimeDeps,
    recover_search_followups_from_runtime,
)
from tests.core.test_search_followup_runtime import build_deps  # noqa: E402


_MISSING = object()
_NOTE_ID = "concept-0123456789abcdef"


class SearchFollowupRecoveryMemoryBoundaryTests(
    unittest.IsolatedAsyncioTestCase
):
    @contextmanager
    def unconfigured_authenticity(self):
        with patch.dict(
            os.environ,
            {
                MEMORY_INTEGRITY_KEY_FILE_ENV: "",
                MEMORY_INTEGRITY_ANCHOR_DIR_ENV: "",
                MEMORY_INTEGRITY_BOOTSTRAP_ENV: "",
            },
        ):
            yield

    @staticmethod
    def write_memory_version(index_dir: Path, version: int) -> None:
        index_dir.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(
            str(index_dir / MEMORY_INDEX_DB_NAME)
        )
        try:
            connection.execute(
                "CREATE TABLE metadata (key TEXT PRIMARY KEY, value NOT NULL)"
            )
            connection.execute(
                "INSERT INTO metadata(key, value) VALUES(?, ?)",
                ("memory_version", str(version)),
            )
            connection.commit()
        finally:
            connection.close()

    @staticmethod
    def replace_memory_version(index_dir: Path, version: int) -> None:
        connection = sqlite3.connect(
            str(index_dir / MEMORY_INDEX_DB_NAME)
        )
        try:
            connection.execute(
                "UPDATE metadata SET value = ? WHERE key = ?",
                (str(version), "memory_version"),
            )
            connection.commit()
        finally:
            connection.close()

    @staticmethod
    def bound_receipt_ref(version: int) -> dict[str, object]:
        return {
            "schema": CONVERSATION_MEMORY_RECEIPT_REF_SCHEMA,
            "state": "bound",
            "memoryVersion": version,
            "suppliedNoteIds": [_NOTE_ID],
            "suppliedNoteCount": 1,
            "contentFree": True,
        }

    @staticmethod
    def assistant(
        content: str,
        receipt: object = _MISSING,
    ) -> dict[str, object]:
        row: dict[str, object] = {
            "role": "assistant",
            "content": content,
        }
        if receipt is not _MISSING:
            row["memoryReceiptRef"] = receipt
        return row

    async def run_ready_recovery(
        self,
        root: Path,
        *,
        receipt: object,
        source: str,
        phase: str = "delivery_ready",
        request_receipt: object | None = None,
        mutate_before_delivery=None,
    ) -> tuple[dict[str, int], list[str], list[str]]:
        index_dir = root / "memory_index"
        recovery = SearchFollowupRecoveryJournal(
            path=root / "recovery.json"
        )
        channel_id = 8 if source == "text" else None
        session_key = (
            "guild:7:text:8:user:9"
            if source == "text"
            else "guild:7:voice:8:user:9"
        )
        intent_id = recovery.begin(
            guild_id=7,
            session_key=session_key,
            source=source,
            turn_id="turn-1",
            room_key=None,
            person_key=None,
            session_memory_key=None,
            channel_id=channel_id,
            reply_to_message_id=10 if channel_id is not None else None,
            request_user_text="검색해줘",
            request_answer_text="찾아보고 알려줄게",
            query="검색 질의",
            continuity_generation=4,
        )
        if phase != "running":
            recovery.begin_delivery_prepare(
                intent_id,
                answer="검색 결과 답변",
                display_text="검색 결과 답변",
                delivery_turn_id="delivery-turn",
            )
        if phase == "delivery_ready":
            recovery.mark_delivery_ready(
                intent_id,
                answer="검색 결과 답변",
                display_text="검색 결과 답변",
                continuity_generation=5,
            )
        elif phase not in {"delivery_preparing", "running"}:
            raise AssertionError("unsupported synthetic recovery phase")
        request_receipt = (
            not_used_memory_receipt_ref()
            if request_receipt is None
            else request_receipt
        )
        history = [
            {"role": "user", "content": "검색해줘"},
            self.assistant(
                "찾아보고 알려줄게",
                request_receipt,
            ),
        ]
        if phase != "running":
            history.extend(
                [
                    {"role": "user", "content": "검색 질의"},
                    self.assistant("검색 결과 답변", receipt),
                ]
            )
        sent: list[str] = []
        spoken: list[str] = []

        class Channel:
            async def send(self, *_args, **_kwargs):
                return None

        class Voice:
            def is_connected(self):
                return True

        class Bot:
            user = type("User", (), {"id": 99})()

            def get_channel(self, _channel_id):
                return Channel() if channel_id is not None else None

            async def fetch_channel(self, _channel_id):
                return Channel() if channel_id is not None else None

            def get_guild(self, _guild_id):
                return type("Guild", (), {"voice_client": Voice()})()

        async def send(_channel, text, **_kwargs):
            sent.append(text)

        async def speak(_voice, text, **_kwargs):
            spoken.append(text)

        mutated = False

        def display(text, **_kwargs):
            nonlocal mutated
            if mutate_before_delivery is not None and not mutated:
                mutated = True
                mutate_before_delivery()
            return text

        base = build_deps(get_conversation_history_result=history)
        deps = SearchFollowupRuntimeDeps(
            **{
                **base.__dict__,
                "bot": Bot(),
                "memory_index_dir": index_dir,
                "get_conversation_history": lambda **_kwargs: history,
                "send_discord_text": send,
                "speak_answer": speak,
                "format_display_text": display,
                "current_turn_id": lambda _key: "delivery-turn",
                "search_followup_recovery": recovery,
                "continuity_status": lambda: {
                    "checkpointGeneration": 5,
                    "rollbackProtected": True,
                },
            }
        )
        result = await recover_search_followups_from_runtime(deps=deps)
        return result, sent, spoken

    async def test_invalid_assistant_pairs_never_redeliver(self) -> None:
        variants = (
            "missing",
            "unattributed",
            "stale",
            "tombstoned",
        )
        for variant in variants:
            for phase in (
                "running",
                "delivery_preparing",
                "delivery_ready",
            ):
                for source in ("text", "voice"):
                    with self.subTest(
                        variant=variant,
                        phase=phase,
                        source=source,
                    ):
                        with tempfile.TemporaryDirectory() as temporary:
                            root = Path(temporary)
                            index_dir = root / "memory_index"
                            if variant in {"stale", "tombstoned"}:
                                self.write_memory_version(
                                    index_dir,
                                    2 if variant == "stale" else 1,
                                )
                            receipt: object = _MISSING
                            if variant == "unattributed":
                                receipt = unattributed_memory_receipt_ref()
                            elif variant in {"stale", "tombstoned"}:
                                receipt = self.bound_receipt_ref(1)
                            with self.unconfigured_authenticity():
                                if variant == "tombstoned":
                                    deletion_journal.append_memory_deletion_tombstone(
                                        index_dir,
                                        {
                                            "schema": deletion_journal.MEMORY_DELETE_TOMBSTONE_V1_SCHEMA,
                                            "noteId": _NOTE_ID,
                                            "noteType": "concept",
                                            "sourceType": "conversation",
                                            "reason": "privacy_request",
                                            "deletedAt": "2026-08-01T00:00:00Z",
                                        },
                                    )
                                result, sent, spoken = (
                                    await self.run_ready_recovery(
                                        root,
                                        receipt=(
                                            not_used_memory_receipt_ref()
                                            if phase == "running"
                                            else receipt
                                        ),
                                        source=source,
                                        phase=phase,
                                        request_receipt=(
                                            receipt
                                            if phase == "running"
                                            else None
                                        ),
                                    )
                                )
                            self.assertEqual(sent, [])
                            self.assertEqual(spoken, [])
                            self.assertEqual(result["uncertain"], 1)

    async def test_current_text_pairs_redeliver_and_voice_fails_closed(self) -> None:
        for state in ("not_used", "bound"):
            for source in ("text", "voice"):
                with self.subTest(state=state, source=source):
                    with tempfile.TemporaryDirectory() as temporary:
                        root = Path(temporary)
                        receipt: object = not_used_memory_receipt_ref()
                        if state == "bound":
                            self.write_memory_version(
                                root / "memory_index",
                                1,
                            )
                            receipt = self.bound_receipt_ref(1)
                        with self.unconfigured_authenticity():
                            result, sent, spoken = (
                                await self.run_ready_recovery(
                                    root,
                                    receipt=receipt,
                                    source=source,
                                )
                            )
                        self.assertEqual(
                            result["redelivered"],
                            1 if source == "text" else 0,
                        )
                        self.assertEqual(
                            result["uncertain"],
                            0 if source == "text" else 1,
                        )
                        self.assertEqual(
                            sent,
                            ["검색 결과 답변"] if source == "text" else [],
                        )
                        self.assertEqual(
                            spoken,
                            [],
                        )

    async def test_bound_pair_version_race_blocks_before_delivery(self) -> None:
        for source in ("text", "voice"):
            with self.subTest(source=source):
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    index_dir = root / "memory_index"
                    self.write_memory_version(index_dir, 1)
                    with self.unconfigured_authenticity():
                        result, sent, spoken = await self.run_ready_recovery(
                            root,
                            receipt=self.bound_receipt_ref(1),
                            source=source,
                            mutate_before_delivery=lambda: (
                                self.replace_memory_version(index_dir, 2)
                            ),
                        )
                    self.assertEqual(sent, [])
                    self.assertEqual(spoken, [])
                    self.assertEqual(result["uncertain"], 1)


if __name__ == "__main__":
    unittest.main()
