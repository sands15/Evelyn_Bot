from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from difflib import SequenceMatcher
import hashlib
import re
from typing import Any


_STOP_WORDS = {
    "a",
    "an",
    "and",
    "for",
    "how",
    "in",
    "of",
    "the",
    "to",
    "with",
}


def _normalize_text(value: Any) -> str:
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", str(value or ""))
    return " ".join(re.findall(r"[a-z0-9]+", text.lower().replace("_", " ")))


def _tokens(value: Any) -> Counter[str]:
    return Counter(
        token
        for token in _normalize_text(value).split()
        if token not in _STOP_WORDS and not token.isdigit()
    )


def _shared_token_count(query: Any, text: Any) -> int:
    return sum((_tokens(query) & _tokens(text)).values())


def _distance(query: str, text: str) -> float:
    normalized_query = _normalize_text(query)
    normalized_text = _normalize_text(text)
    if not normalized_query or not normalized_text:
        return 1.0
    if normalized_query == normalized_text:
        return 0.0

    query_tokens = _tokens(query)
    text_tokens = _tokens(text)
    overlap = sum((query_tokens & text_tokens).values())
    query_total = max(1, sum(query_tokens.values()))
    union = max(1, sum((query_tokens | text_tokens).values()))
    coverage = overlap / query_total
    jaccard = overlap / union
    token_similarity = (coverage * 0.85) + (jaccard * 0.15)
    character_similarity = SequenceMatcher(None, normalized_query, normalized_text).ratio()
    return max(0.0, 1.0 - max(token_similarity, character_similarity))


@dataclass(frozen=True)
class LocalTextDocument:
    page_content: str
    metadata: dict[str, Any]


class _LocalCollection:
    def __init__(self, index: "LocalTextIndex") -> None:
        self._index = index

    def count(self) -> int:
        return len(self._index._entries)

    def get(self) -> dict[str, list[str]]:
        return {"ids": list(self._index._entries)}

    def delete(self, ids: list[str]) -> None:
        for document_id in ids:
            self._index._entries.pop(str(document_id), None)


class LocalTextIndex:
    """Small deterministic lexical index with the Chroma methods Voyager uses."""

    def __init__(self, *, collection_name: str, persist_directory: str | None = None) -> None:
        self.collection_name = str(collection_name)
        self.persist_directory = persist_directory
        self._entries: dict[str, LocalTextDocument] = {}
        self._collection = _LocalCollection(self)

    def add_texts(
        self,
        *,
        texts: list[str],
        ids: list[str] | None = None,
        metadatas: list[dict[str, Any]] | None = None,
    ) -> list[str]:
        stored_ids: list[str] = []
        for index, raw_text in enumerate(texts):
            text = str(raw_text or "")
            document_id = (
                str(ids[index])
                if ids is not None and index < len(ids)
                else hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]
            )
            metadata = dict(metadatas[index]) if metadatas is not None and index < len(metadatas) else {}
            self._entries[document_id] = LocalTextDocument(page_content=text, metadata=metadata)
            stored_ids.append(document_id)
        return stored_ids

    def similarity_search_with_score(self, query: str, *, k: int = 4):
        ranked = []
        skill_index = "skill" in self.collection_name.lower()
        for document_id, document in self._entries.items():
            metadata_name = str(document.metadata.get("name") or "")
            searchable_text = f"{metadata_name} {document.page_content}".strip()
            score = _distance(query, searchable_text)
            if skill_index and _shared_token_count(query, searchable_text) == 0:
                score = 1.0
            ranked.append((document, score, document_id))
        ranked.sort(key=lambda item: (item[1], item[2]))
        return [(document, score) for document, score, _ in ranked[: max(0, int(k))]]

    def persist(self) -> None:
        return None
