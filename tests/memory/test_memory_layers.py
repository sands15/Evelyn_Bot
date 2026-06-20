from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

import evelyn_core.memory as memory  # noqa: E402
from evelyn_core.memory_layers import collect_memory_layers  # noqa: E402


class TemporaryMemoryRoot:
    def __init__(self) -> None:
        self.tmp = TemporaryDirectory()
        self.old_root = memory.MEMORY_ROOT

    def __enter__(self) -> Path:
        memory.MEMORY_ROOT = Path(self.tmp.name)
        return memory.MEMORY_ROOT

    def __exit__(self, exc_type, exc, tb) -> None:
        memory.MEMORY_ROOT = self.old_root
        self.tmp.cleanup()


class MemoryLayersTests(unittest.TestCase):
    def test_collect_memory_layers_reads_all_requested_scopes(self) -> None:
        with TemporaryMemoryRoot():
            scopes = [
                ("guild", None, "공용 방 기억", "guild summary"),
                ("room", "room-1", "방 기억", "room summary"),
                ("person", "person-1", "이 사람 기억", "person summary"),
                ("session", "session-1", "현재 세션 기억", "session summary"),
            ]
            for scope_type, scope_key, _label, summary in scopes:
                memory.write_text_file(
                    memory.memory_summary_path(123, scope_type=scope_type, scope_key=scope_key),
                    summary,
                )
                memory.append_raw_transcript_rows(
                    123,
                    [{"role": "user", "speaker": scope_type, "source": "test", "text": f"{scope_type} raw"}],
                    scope_type=scope_type,
                    scope_key=scope_key,
                )
                memory.append_unique_memory_rows(
                    memory.memory_facts_path(123, scope_type=scope_type, scope_key=scope_key),
                    [{"type": "fact", "text": f"{scope_type} fact"}],
                    20,
                    mirror_path=memory.vault_facts_path(123, scope_type=scope_type, scope_key=scope_key),
                )
                memory.append_unique_memory_rows(
                    memory.memory_questions_path(123, scope_type=scope_type, scope_key=scope_key),
                    [{"type": "question", "text": f"{scope_type} question"}],
                    20,
                    mirror_path=memory.vault_questions_path(123, scope_type=scope_type, scope_key=scope_key),
                )

            layers = collect_memory_layers(
                123,
                room_key="room-1",
                person_key="person-1",
                session_memory_key="session-1",
            )

        self.assertEqual(list(layers), ["guild", "room", "person", "session"])
        for key, (_scope_type, scope_key, label, summary) in zip(layers, scopes):
            layer = layers[key]
            self.assertEqual(layer["label"], label)
            self.assertEqual(layer["scope_key"], scope_key)
            self.assertEqual(layer["summary"], summary)
            self.assertEqual(layer["raw"][0]["text"], f"{key} raw")
            self.assertEqual(layer["vault_raw"][0]["text"], f"{key} raw")
            self.assertEqual(layer["facts"][0]["text"], f"{key} fact")
            self.assertEqual(layer["questions"][0]["text"], f"{key} question")

    def test_collect_memory_layers_only_includes_requested_optional_scopes(self) -> None:
        with TemporaryMemoryRoot():
            layers = collect_memory_layers(123)

        self.assertEqual(list(layers), ["guild"])
        self.assertEqual(layers["guild"]["label"], "공용 방 기억")
        self.assertEqual(layers["guild"]["raw"], [])


if __name__ == "__main__":
    unittest.main()
