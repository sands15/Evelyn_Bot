from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.vision_remote_model_lock import (  # noqa: E402
    VisionRemoteModelLockError,
    load_remote_model_lock,
    verify_remote_model_snapshot,
)


def _cache_root(value: str) -> Path:
    candidate = Path(value)
    if not candidate.is_absolute():
        raise argparse.ArgumentTypeError("cache_dir_must_be_absolute")
    try:
        if candidate.is_symlink() or not candidate.is_dir():
            raise argparse.ArgumentTypeError("cache_dir_rejected")
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(REPO_ROOT.resolve())
    except ValueError:
        return resolved
    except OSError:
        raise argparse.ArgumentTypeError("cache_dir_unavailable") from None
    raise argparse.ArgumentTypeError("cache_dir_inside_repository")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Download and verify Evelyn's exact Falcon-OCR snapshot without "
            "executing its remote model code."
        )
    )
    parser.add_argument(
        "mode",
        choices=("download", "verify"),
        help="download permits network access; verify uses the local cache only",
    )
    parser.add_argument(
        "--cache-dir",
        required=True,
        type=_cache_root,
        help="pre-existing absolute Hugging Face cache directory",
    )
    return parser


def _hub_cache_dir(cache_root: Path, *, allow_create: bool) -> Path:
    hub_cache = cache_root / "hub"
    try:
        if allow_create:
            hub_cache.mkdir(mode=0o700, exist_ok=True)
        if hub_cache.is_symlink() or not hub_cache.is_dir():
            raise RuntimeError("hub_cache_dir_rejected")
        return hub_cache.resolve(strict=True)
    except OSError:
        raise RuntimeError("hub_cache_dir_rejected") from None


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    from huggingface_hub import hf_hub_download

    try:
        lock = load_remote_model_lock()
        hub_cache = _hub_cache_dir(
            args.cache_dir,
            allow_create=args.mode == "download",
        )
        receipt = verify_remote_model_snapshot(
            lock,
            downloader=hf_hub_download,
            local_files_only=args.mode == "verify",
            cache_dir=hub_cache,
        )
    except VisionRemoteModelLockError as exc:
        print(
            json.dumps(
                {
                    "schema": "vision.remote-model-provision.v1",
                    "ok": False,
                    "failureCode": exc.code,
                    "contentFree": True,
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
            file=sys.stderr,
        )
        return 1
    except RuntimeError:
        print(
            '{"contentFree":true,"failureCode":"hub_cache_dir_rejected",'
            '"ok":false,"schema":"vision.remote-model-provision.v1"}',
            file=sys.stderr,
        )
        return 1
    print(
        json.dumps(
            {
                "schema": "vision.remote-model-provision.v1",
                "ok": True,
                "mode": args.mode,
                "revisionPinned": receipt["revisionPinned"],
                "fileCount": receipt["fileCount"],
                "verifiedBytes": receipt["verifiedBytes"],
                "contentFree": True,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
