from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = next(
    path for path in Path(__file__).resolve().parents if (path / "main.py").exists()
)
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools import voice_asr_benchmark as benchmark  # noqa: E402


SELECTION_SCHEMA = "evelyn.voice-asr-selection.v1"
RESULT_SCHEMA = "evelyn.voice-asr-assembly.v1"
SELECTION_ITEM_KEYS = {
    "source",
    "kind",
    "sourceClass",
    "reference",
    "entities",
}
_PARTIAL_NAME = ".voice_asr-assembling"
_ERROR_CODES = {
    "selection_invalid",
    "source_invalid",
    "output_invalid",
    "output_exists",
    "partial_exists",
    "corpus_invalid",
    "preflight_failed",
    "publish_failed",
    "cleanup_failed",
    "internal_failure",
}


class AssemblyError(RuntimeError):
    """A content-free corpus assembly failure."""


def _error(code: str) -> AssemblyError:
    return AssemblyError(code if code in _ERROR_CODES else "internal_failure")


def _exists_exact(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise _error("output_invalid") from exc
    return True


def _load_selection(path: Path) -> list[tuple[Path, dict[str, Any]]]:
    requested = Path(path).absolute()
    requested_root = requested.parent
    try:
        root_metadata = requested_root.lstat()
        if benchmark._is_reparse(root_metadata) or not stat.S_ISDIR(
            root_metadata.st_mode
        ):
            raise _error("selection_invalid")
        selection_metadata = benchmark._regular_metadata(requested)
        if selection_metadata.st_size > 512 * 1024:
            raise _error("selection_invalid")
        staging_root = requested_root.resolve(strict=True)
        selection_path = requested.resolve(strict=True)
        selection_path.relative_to(staging_root)
        benchmark._validate_component_tree(staging_root, selection_path)
        raw = selection_path.read_bytes()
        if benchmark._file_identity(
            benchmark._regular_metadata(selection_path)
        ) != benchmark._file_identity(selection_metadata):
            raise _error("selection_invalid")
        payload = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=benchmark._unique_json_object,
        )
    except AssemblyError:
        raise
    except Exception as exc:
        raise _error("selection_invalid") from exc

    if (
        not isinstance(payload, dict)
        or set(payload) != {"schema", "items"}
        or payload.get("schema") != SELECTION_SCHEMA
        or not isinstance(payload.get("items"), list)
        or len(payload["items"]) != 50
    ):
        raise _error("selection_invalid")

    selected: list[tuple[Path, dict[str, Any]]] = []
    for item in payload["items"]:
        if not isinstance(item, dict) or set(item) != SELECTION_ITEM_KEYS:
            raise _error("selection_invalid")
        source_value = item.get("source")
        if not isinstance(source_value, str) or not source_value:
            raise _error("selection_invalid")
        relative = Path(source_value)
        if relative.is_absolute() or any(
            part in {"", ".", ".."} for part in relative.parts
        ):
            raise _error("selection_invalid")
        try:
            source = (staging_root / relative).resolve(strict=True)
            source.relative_to(staging_root)
            benchmark._validate_component_tree(staging_root, source)
            benchmark._regular_metadata(source)
        except Exception as exc:
            raise _error("source_invalid") from exc
        selected.append(
            (
                source,
                {
                    "kind": item.get("kind"),
                    "sourceClass": item.get("sourceClass"),
                    "reference": item.get("reference"),
                    "entities": item.get("entities"),
                },
            )
        )
    return selected


def _copy_bound_source(source: Path, destination: Path) -> str:
    try:
        before = benchmark._regular_metadata(source)
        source_sha256 = benchmark._sha256_file(source)
        shutil.copyfile(source, destination, follow_symlinks=False)
        after = benchmark._regular_metadata(source)
        destination_sha256 = benchmark._sha256_file(destination)
        if (
            benchmark._file_identity(after) != benchmark._file_identity(before)
            or benchmark._sha256_file(source) != source_sha256
            or destination_sha256 != source_sha256
        ):
            raise _error("source_invalid")
        return destination_sha256
    except AssemblyError:
        raise
    except Exception as exc:
        raise _error("source_invalid") from exc


def _fresh_process_preflight(root: Path, manifest_sha256: str) -> None:
    code = (
        "import sys; from pathlib import Path; "
        "sys.path.insert(0, sys.argv[1]); "
        "from tools.voice_asr_benchmark import load_corpus_manifest; "
        "corpus=load_corpus_manifest(Path(sys.argv[2]), "
        "expected_manifest_sha256=sys.argv[3]); "
        "print(corpus.manifest_sha256)"
    )
    try:
        completed = subprocess.run(
            [
                sys.executable,
                "-I",
                "-B",
                "-c",
                code,
                str(REPO_ROOT),
                str(root),
                manifest_sha256,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            check=False,
        )
    except Exception as exc:
        raise _error("preflight_failed") from exc
    if completed.returncode != 0 or completed.stdout.strip() != manifest_sha256:
        raise _error("preflight_failed")


def _cleanup_partial(path: Path) -> None:
    try:
        if not _exists_exact(path):
            return
        metadata = path.lstat()
        if benchmark._is_reparse(metadata) or not stat.S_ISDIR(metadata.st_mode):
            raise _error("cleanup_failed")
        shutil.rmtree(path)
        if _exists_exact(path):
            raise _error("cleanup_failed")
    except Exception as exc:
        if isinstance(exc, AssemblyError) and str(exc) == "cleanup_failed":
            raise
        raise _error("cleanup_failed") from exc


def assemble_corpus(
    selection_path: Path,
    *,
    final_root: Path = benchmark.PRIVATE_ROOT,
) -> str:
    final = Path(final_root).absolute()
    if final.name != benchmark.PRIVATE_ROOT.name:
        raise _error("output_invalid")
    parent = final.parent
    try:
        parent.mkdir(parents=True, exist_ok=True)
        parent_metadata = parent.lstat()
        if benchmark._is_reparse(parent_metadata) or not stat.S_ISDIR(
            parent_metadata.st_mode
        ):
            raise _error("output_invalid")
    except AssemblyError:
        raise
    except Exception as exc:
        raise _error("output_invalid") from exc

    partial = parent / _PARTIAL_NAME
    if _exists_exact(final):
        raise _error("output_exists")
    if _exists_exact(partial):
        raise _error("partial_exists")
    selected = _load_selection(selection_path)

    try:
        partial.mkdir()
    except FileExistsError as exc:
        raise _error("partial_exists") from exc
    except OSError as exc:
        raise _error("output_invalid") from exc

    published = False
    try:
        manifest_items: list[dict[str, Any]] = []
        for index, (source, item) in enumerate(selected):
            audio_name = f"{index:03d}.wav"
            audio_sha256 = _copy_bound_source(source, partial / audio_name)
            manifest_items.append(
                {
                    **item,
                    "audio": audio_name,
                    "audioSha256": audio_sha256,
                }
            )

        manifest_bytes = (
            json.dumps(
                {"schema": benchmark.MANIFEST_SCHEMA, "items": manifest_items},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        manifest_path = partial / "manifest.json"
        with manifest_path.open("xb") as handle:
            handle.write(manifest_bytes)
            handle.flush()
            os.fsync(handle.fileno())
        manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()

        try:
            corpus = benchmark.load_corpus_manifest(
                partial,
                expected_manifest_sha256=manifest_sha256,
            )
        except Exception as exc:
            raise _error("corpus_invalid") from exc
        if len(corpus.items) != 50 or corpus.manifest_sha256 != manifest_sha256:
            raise _error("corpus_invalid")
        _fresh_process_preflight(partial, manifest_sha256)

        if _exists_exact(final):
            raise _error("output_exists")
        try:
            os.rename(partial, final)
        except OSError as exc:
            raise _error("publish_failed") from exc
        published = True
        try:
            published_corpus = benchmark.load_corpus_manifest(
                final,
                expected_manifest_sha256=manifest_sha256,
            )
        except Exception as exc:
            raise _error("publish_failed") from exc
        if len(published_corpus.items) != 50:
            raise _error("publish_failed")
        return manifest_sha256
    except AssemblyError:
        if not published:
            _cleanup_partial(partial)
        raise
    except Exception as exc:
        if not published:
            _cleanup_partial(partial)
        raise _error("internal_failure") from exc


def _safe_result(*, status: str, **values: Any) -> str:
    return json.dumps(
        {"schema": RESULT_SCHEMA, "status": status, **values},
        sort_keys=True,
        separators=(",", ":"),
    )


def main(
    argv: list[str] | None = None,
    *,
    final_root: Path = benchmark.PRIVATE_ROOT,
) -> int:
    parser = argparse.ArgumentParser(
        description="Assemble the fixed private ASR corpus from an explicit staging selection."
    )
    parser.add_argument(
        "selection",
        type=Path,
        help="private selection JSON; source paths must be relative to its directory",
    )
    args = parser.parse_args(argv)
    try:
        manifest_sha256 = assemble_corpus(args.selection, final_root=final_root)
    except AssemblyError as exc:
        print(_safe_result(status="fail", error=str(exc)), file=sys.stderr)
        return 2
    except Exception:
        print(_safe_result(status="fail", error="internal_failure"), file=sys.stderr)
        return 2
    print(
        _safe_result(
            status="pass",
            itemCount=50,
            manifestSha256=manifest_sha256,
            freshProcessPreflight=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
