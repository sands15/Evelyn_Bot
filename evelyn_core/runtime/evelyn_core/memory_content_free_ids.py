from __future__ import annotations

import hashlib
import re

from .text import clean_text


MEMORY_CONTENT_FREE_ID_NAMESPACES = frozenset(
    {"action", "evidence", "source-ref", "turn"}
)
_CANONICAL_CONTENT_FREE_ID = re.compile(
    r"opaque-(action|evidence|source-ref|turn)-[0-9a-f]{64}"
)
_CANONICAL_SOURCE_REF = re.compile(
    r"turn:(opaque-turn-[0-9a-f]{64}):user"
)
_CONTENT_FREE_ID_DOMAIN = b"evelyn.memory.content-free-id.v1\n"


def memory_content_free_id(
    value: object,
    *,
    namespace: str,
) -> str:
    if namespace not in MEMORY_CONTENT_FREE_ID_NAMESPACES:
        raise ValueError("memory_content_free_id_namespace_invalid")
    raw = clean_text(str(value or ""))
    if not raw:
        return ""
    canonical = _CANONICAL_CONTENT_FREE_ID.fullmatch(raw)
    if canonical is not None and canonical.group(1) == namespace:
        return raw
    digest = hashlib.sha256(_CONTENT_FREE_ID_DOMAIN)
    digest.update(namespace.encode("ascii"))
    digest.update(b"\n")
    digest.update(raw.encode("utf-8", errors="strict"))
    return f"opaque-{namespace}-{digest.hexdigest()}"


def memory_content_free_source_ref(value: object) -> str:
    raw = clean_text(str(value or ""))
    if not raw:
        return ""
    if _CANONICAL_SOURCE_REF.fullmatch(raw):
        return raw
    projected = memory_content_free_id(
        raw,
        namespace="turn",
    )
    return f"turn:{projected}:user"


def memory_content_free_source_ref_is_canonical(
    value: object,
) -> bool:
    return bool(
        isinstance(value, str)
        and _CANONICAL_SOURCE_REF.fullmatch(value)
    )


__all__ = [
    "MEMORY_CONTENT_FREE_ID_NAMESPACES",
    "memory_content_free_id",
    "memory_content_free_source_ref",
    "memory_content_free_source_ref_is_canonical",
]
