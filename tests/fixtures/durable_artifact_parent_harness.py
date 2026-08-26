from __future__ import annotations

import sys
import threading
from pathlib import Path


REPO_ROOT = next(
    path for path in Path(__file__).resolve().parents if (path / "main.py").exists()
)
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.durable_artifact_process import DurableArtifactProcess  # noqa: E402


def main(arguments: list[str]) -> int:
    if len(arguments) != 1:
        return 2
    process = DurableArtifactProcess()
    process.ensure_started()
    worker_pid = process.pid
    if worker_pid is None:
        return 3
    Path(arguments[0]).write_text(str(worker_pid), encoding="utf-8")
    threading.Event().wait()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
