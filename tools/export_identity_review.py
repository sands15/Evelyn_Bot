from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.identity_review import export_identity_review  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export Evelyn identity review candidates to TSV and Markdown.")
    parser.add_argument(
        "--input",
        type=Path,
        default=None,
        help="Identity review queue JSONL path. Defaults to runtime_artifacts/evelyn_identity_review_queue.jsonl.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory for exported review files. Defaults to runtime_artifacts/identity_review.",
    )
    parser.add_argument(
        "--include-all",
        action="store_true",
        help="Include rows whose status is already accepted/rejected/handled.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = export_identity_review(
        input_path=args.input,
        output_dir=args.output_dir,
        include_all=args.include_all,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
