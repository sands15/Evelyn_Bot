from __future__ import annotations

from typing import Any

from .cognitive_policy_state import read_layered_cognitive_state
from .config import MEMORY_RAW_CONTEXT_LIMIT, MEMORY_RETRIEVE_LIMIT, MEMORY_VAULT_RAW_RETRIEVE_LIMIT
from .memory import merge_memory_rows, normalize_cognitive_state, select_relevant_memory_rows
from .memory_legacy_evidence import validate_legacy_memory_evidence
from .memory_layers import collect_memory_layers
from .memory_prompt_policy import MEMORY_CONTEXT_USE_POLICY
from .memory_vault import build_memory_vault_context
from .text import clean_text


_MEMORY_GROUNDING_KEY = "_memory_grounding"


def merge_recent_memory_rows(*row_groups: list[dict], limit: int) -> list[dict]:
    merged = merge_memory_rows(*row_groups)
    merged.sort(key=lambda row: int(row.get("saved_at", 0) or 0))
    return merged[-limit:]


def format_memory_row_lines(rows: list[dict]) -> str:
    return "\n".join(
        f"- {clean_text(str(row.get('speaker', row.get('role', 'unknown')))) or 'unknown'}"
        f" ({clean_text(str(row.get('source', 'unknown'))) or 'unknown'}): {clean_text(str(row.get('text', '')))}"
        for row in rows
        if clean_text(str(row.get("text", "")))
    )


def _split_memory_rows(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    attributed: list[dict] = []
    confirmation_only: list[dict] = []
    for row in rows:
        target = (
            attributed
            if row.get(_MEMORY_GROUNDING_KEY) == "attributed"
            else confirmation_only
        )
        target.append(row)
    return attributed, confirmation_only


def _append_memory_row_sections(
    attributed_parts: list[str],
    confirmation_parts: list[str],
    *,
    rows: list[dict],
    attributed_title: str,
    confirmation_title: str,
    plain_text: bool = False,
) -> None:
    attributed, confirmation_only = _split_memory_rows(rows)

    def render(selected: list[dict]) -> str:
        if not plain_text:
            return format_memory_row_lines(selected)
        return "\n".join(
            f"- {clean_text(str(row.get('text', '')))}"
            for row in selected
            if clean_text(str(row.get("text", "")))
        )

    if attributed:
        attributed_parts.append(attributed_title + ":\n" + render(attributed))
    if confirmation_only:
        confirmation_parts.append(
            confirmation_title + "(확인 전용):\n" + render(confirmation_only)
        )


def build_memory_context_payload(
    *,
    layers: dict[str, dict[str, Any]],
    state: dict[str, Any],
    session_state: dict[str, Any],
    vault_context: str,
    facts: list[dict],
    questions: list[dict],
    vault_raw_rows: list[dict],
    vault_grounding_state: str = "unattributed",
) -> str:
    attributed_parts: list[str] = []
    confirmation_parts: list[str] = []
    neutral_parts: list[str] = []
    summary_layers = [
        layer
        for layer in (
            layers.get("session"),
            layers.get("person"),
            layers.get("room"),
            layers.get("guild"),
        )
        if layer and layer.get("summary")
    ]
    attributed_summary_lines = [
        f"- {layer['label']}: {layer['summary']}"
        for layer in summary_layers
        if layer.get(_MEMORY_GROUNDING_KEY) == "attributed"
    ]
    confirmation_summary_lines = [
        f"- {layer['label']}: {layer['summary']}"
        for layer in summary_layers
        if layer.get(_MEMORY_GROUNDING_KEY) != "attributed"
    ]
    if attributed_summary_lines:
        attributed_parts.append(
            "근거 연결된 현재 작업 요약(내용 사실성은 별도 확인 필요):\n"
            + "\n".join(attributed_summary_lines)
        )
    if confirmation_summary_lines:
        confirmation_parts.append(
            "미확인 과거 작업 요약(확인 전용):\n"
            + "\n".join(confirmation_summary_lines)
        )

    session_rows = merge_recent_memory_rows(*(layer["raw"] for layer in (layers.get("session"),) if layer), limit=4)
    _append_memory_row_sections(
        attributed_parts,
        confirmation_parts,
        rows=session_rows,
        attributed_title="근거 연결된 현재 세션 최근 대화",
        confirmation_title="미확인 현재 세션 과거 대화",
    )

    person_rows = merge_recent_memory_rows(*(layer["raw"] for layer in (layers.get("person"),) if layer), limit=4)
    _append_memory_row_sections(
        attributed_parts,
        confirmation_parts,
        rows=person_rows,
        attributed_title="근거 연결된 이 사람과의 최근 대화",
        confirmation_title="미확인 이 사람과의 과거 대화",
    )

    room_rows = merge_recent_memory_rows(
        *(layer["raw"] for layer in (layers.get("room"), layers.get("guild")) if layer),
        limit=MEMORY_RAW_CONTEXT_LIMIT,
    )
    _append_memory_row_sections(
        attributed_parts,
        confirmation_parts,
        rows=room_rows,
        attributed_title="근거 연결된 방 최근 대화",
        confirmation_title="미확인 방 과거 대화",
    )

    if vault_raw_rows:
        _append_memory_row_sections(
            attributed_parts,
            confirmation_parts,
            rows=vault_raw_rows,
            attributed_title="근거 연결된 문서 보관함 관련 대화",
            confirmation_title="미확인 문서 보관함 과거 대화",
        )
    if session_state:
        action_label = {
            "answer": "답하기",
            "ask": "질문하기",
            "wait": "더 듣기",
        }.get(state.get("action", "answer"), "답하기")
        state_lines = [f"- 권장 행동: {action_label}"]
        if state.get("user_intent"):
            state_lines.append(f"- 사용자 의도: {state['user_intent']}")
        if state.get("retrieved_context_ids"):
            state_lines.append(f"- 참고 문맥 ID: {', '.join(state['retrieved_context_ids'][:4])}")
        if session_state.get("last_speaker"):
            state_lines.append(f"- 마지막 화자: {session_state['last_speaker']}")
        if session_state.get("awaiting_user_reply"):
            state_lines.append("- 사용자 후속 응답 대기 중")
        if session_state.get("topic_id"):
            state_lines.append(f"- 현재 topic_id: {session_state['topic_id']}")
        neutral_parts.append("현재 내부 상태(사용자 발화 아님):\n" + "\n".join(state_lines))
    if vault_context:
        target = (
            attributed_parts
            if vault_grounding_state == "attributed"
            else confirmation_parts
        )
        title = (
            "Structured memory vault recall"
            if vault_grounding_state == "attributed"
            else "미확인 Structured memory vault recall(확인 전용)"
        )
        target.append(title + ":\n" + vault_context)
    _append_memory_row_sections(
        attributed_parts,
        confirmation_parts,
        rows=facts,
        attributed_title="근거 연결된 장기 기억 후보(내용 사실성은 별도 확인 필요)",
        confirmation_title="미확인 장기 기억 후보",
        plain_text=True,
    )
    _append_memory_row_sections(
        attributed_parts,
        confirmation_parts,
        rows=questions,
        attributed_title="근거 연결된 열린 질문/가설",
        confirmation_title="미확인 열린 질문/가설",
        plain_text=True,
    )

    if not attributed_parts and not confirmation_parts and not neutral_parts:
        return ""

    parts: list[str] = []
    parts.extend(attributed_parts)
    parts.extend(neutral_parts)
    parts.extend(confirmation_parts)
    return "\n\n".join(parts)


def _annotate_memory_rows(
    rows: list[dict],
    *,
    expected_kind: str,
) -> list[dict]:
    return [
        {
            **row,
            _MEMORY_GROUNDING_KEY: (
                "attributed"
                if (
                    validate_legacy_memory_evidence(
                        row,
                        expected_kind=expected_kind,
                    )
                    is not None
                )
                else "unattributed"
            ),
        }
        for row in rows
    ]


def _annotate_memory_layers(
    layers: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    annotated: dict[str, dict[str, Any]] = {}
    for key, layer in layers.items():
        summary_provenance = layer.get("summary_provenance")
        summary_attributed = bool(
            layer.get("summary")
            and isinstance(summary_provenance, dict)
            and (
                validate_legacy_memory_evidence(
                    summary_provenance,
                    expected_kind="derived_summary",
                )
                is not None
            )
        )
        annotated[key] = {
            **layer,
            _MEMORY_GROUNDING_KEY: (
                "attributed" if summary_attributed else "unattributed"
            ),
            "raw": _annotate_memory_rows(
                list(layer.get("raw") or []),
                expected_kind="conversation_turn",
            ),
            "vault_raw": _annotate_memory_rows(
                list(layer.get("vault_raw") or []),
                expected_kind="conversation_turn",
            ),
        }
    return annotated


def build_memory_context(
    guild_id: int,
    user_text: str,
    cognitive_state: dict[str, Any] | None = None,
    *,
    session_key: str | None = None,
    session_state: dict[str, Any] | None = None,
    room_key: str | None = None,
    person_key: str | None = None,
    session_memory_key: str | None = None,
    receipt: dict[str, Any] | None = None,
) -> str:
    layers = collect_memory_layers(
        guild_id,
        room_key=room_key,
        person_key=person_key,
        session_memory_key=session_memory_key,
    )
    facts = select_relevant_memory_rows(
        user_text,
        merge_memory_rows(*(layer["facts"] for layer in layers.values())),
        MEMORY_RETRIEVE_LIMIT,
    )
    questions = select_relevant_memory_rows(
        user_text,
        merge_memory_rows(*(layer["questions"] for layer in layers.values())),
        4,
    )
    vault_raw_rows = select_relevant_memory_rows(
        user_text,
        merge_memory_rows(*(layer["vault_raw"] for layer in layers.values())),
        MEMORY_VAULT_RAW_RETRIEVE_LIMIT,
    )
    state = read_layered_cognitive_state(
        guild_id,
        room_key=room_key,
        person_key=person_key,
        session_memory_key=session_memory_key,
    )
    state = normalize_cognitive_state(state if cognitive_state is None else cognitive_state)
    active_session_state = dict(session_state or {})
    vault_receipt: dict[str, Any] = {}
    vault_context = build_memory_vault_context(
        guild_id,
        user_text,
        session_key=session_key,
        topic_id=clean_text(str(active_session_state.get("topic_id", ""))) or None,
        source="context_pipeline",
        context_focus=[
            "relevant_memory",
            clean_text(str(state.get("user_intent", ""))),
            clean_text(str(state.get("state_summary", ""))),
        ],
        max_items=5,
        receipt=vault_receipt,
    )
    supplied_note_ids = sorted(
        {
            clean_text(str(item))
            for item in (vault_receipt.get("suppliedNoteIds") or [])
            if clean_text(str(item))
        }
    )
    vault_grounding_state = (
        "attributed" if vault_context and supplied_note_ids else "unattributed"
    )
    render_layers = _annotate_memory_layers(layers)
    render_facts = _annotate_memory_rows(facts, expected_kind="derived_fact")
    render_questions = _annotate_memory_rows(
        questions,
        expected_kind="derived_question",
    )
    render_vault_raw_rows = _annotate_memory_rows(
        vault_raw_rows,
        expected_kind="conversation_turn",
    )

    context = build_memory_context_payload(
        layers=render_layers,
        state=state,
        session_state=active_session_state,
        vault_context=vault_context,
        facts=render_facts,
        questions=render_questions,
        vault_raw_rows=render_vault_raw_rows,
        vault_grounding_state=vault_grounding_state,
    )
    summary_count = sum(1 for layer in render_layers.values() if layer.get("summary"))
    session_rows = merge_recent_memory_rows(
        *(layer["raw"] for layer in (render_layers.get("session"),) if layer),
        limit=4,
    )
    person_rows = merge_recent_memory_rows(
        *(layer["raw"] for layer in (render_layers.get("person"),) if layer),
        limit=4,
    )
    room_rows = merge_recent_memory_rows(
        *(
            layer["raw"]
            for layer in (render_layers.get("room"), render_layers.get("guild"))
            if layer
        ),
        limit=MEMORY_RAW_CONTEXT_LIMIT,
    )
    legacy_counts = {
        "summaries": summary_count,
        "sessionRaw": len(session_rows),
        "personRaw": len(person_rows),
        "roomRaw": len(room_rows),
        "vaultRaw": len(render_vault_raw_rows),
        "facts": len(render_facts),
        "questions": len(render_questions),
    }
    legacy_item_count = sum(legacy_counts.values())
    summary_evidence_items = [
        layer.get("summary_provenance")
        for layer in render_layers.values()
        if layer.get("summary")
        and isinstance(layer.get("summary_provenance"), dict)
    ]
    legacy_evidence = [
        *(
            evidence
            for row in summary_evidence_items
            if (
                evidence := validate_legacy_memory_evidence(
                    row,
                    expected_kind="derived_summary",
                )
            )
            is not None
        ),
        *(
            evidence
            for row in [
                *session_rows,
                *person_rows,
                *room_rows,
                *render_vault_raw_rows,
            ]
            if (
                evidence := validate_legacy_memory_evidence(
                    row,
                    expected_kind="conversation_turn",
                )
            )
            is not None
        ),
        *(
            evidence
            for row in render_facts
            if (
                evidence := validate_legacy_memory_evidence(
                    row,
                    expected_kind="derived_fact",
                )
            )
            is not None
        ),
        *(
            evidence
            for row in render_questions
            if (
                evidence := validate_legacy_memory_evidence(
                    row,
                    expected_kind="derived_question",
                )
            )
            is not None
        ),
    ]
    legacy_attributed_item_count = len(legacy_evidence)
    legacy_unattributed_item_count = max(
        0,
        legacy_item_count - legacy_attributed_item_count,
    )
    legacy_evidence_ids = sorted({item[0] for item in legacy_evidence})
    legacy_source_turn_ids = sorted(
        {
            source_turn_id
            for item in legacy_evidence
            for source_turn_id in item[2]
        }
    )
    legacy_source_evidence_ids = sorted(
        {
            source_evidence_id
            for item in legacy_evidence
            for source_evidence_id in item[1]
        }
    )
    attributed_component_count = legacy_attributed_item_count
    unattributed_component_count = legacy_unattributed_item_count
    if vault_context:
        if supplied_note_ids:
            attributed_component_count += 1
        else:
            unattributed_component_count += 1
    if context and attributed_component_count == 0 and unattributed_component_count == 0:
        unattributed_component_count = 1
    vault_confirm_only = bool(vault_context and not supplied_note_ids)
    confirm_only_item_count = (
        legacy_unattributed_item_count + (1 if vault_confirm_only else 0)
    )
    if not context:
        grounding_state = "empty"
    elif attributed_component_count and unattributed_component_count:
        grounding_state = "partial"
    elif attributed_component_count:
        grounding_state = "attributed"
    else:
        grounding_state = "unattributed"
    if receipt is not None:
        receipt.clear()
        receipt.update(
            {
                "schema": "memory.context-receipt.v1",
                "state": "provided" if context else "empty",
                "groundingState": grounding_state,
                "usePolicy": MEMORY_CONTEXT_USE_POLICY,
                "confirmOnlyItemCount": confirm_only_item_count,
                "promptTruncated": False,
                "promptEvidenceDiscarded": False,
                "preTruncationLegacyItemCount": 0,
                "preTruncationNoteCount": 0,
                "opaqueConfirmOnlyComponentCount": 0,
                "vaultState": clean_text(str(vault_receipt.get("state") or "unknown")),
                "vaultConfirmOnly": vault_confirm_only,
                "memoryVersion": int(vault_receipt.get("memoryVersion") or 0),
                "retrievalMode": clean_text(str(vault_receipt.get("retrievalMode") or "unknown"))[:40],
                "cacheHit": bool(vault_receipt.get("cacheHit")),
                "hotContextState": clean_text(str(vault_receipt.get("hotContextState") or "unknown")),
                "suppliedNoteIds": supplied_note_ids,
                "suppliedNoteCount": len(supplied_note_ids),
                "sourceTypeCounts": dict(vault_receipt.get("sourceTypeCounts") or {}),
                "legacyItemCounts": legacy_counts,
                "legacyItemCount": legacy_item_count,
                "legacyAttributedItemCount": legacy_attributed_item_count,
                "legacyUnattributedItemCount": legacy_unattributed_item_count,
                "legacyConfirmOnlyItemCount": legacy_unattributed_item_count,
                "legacyEvidenceIds": legacy_evidence_ids,
                "legacySourceEvidenceIds": legacy_source_evidence_ids,
                "legacySourceTurnIds": legacy_source_turn_ids,
                "contentFree": True,
            }
        )
    return context


__all__ = [
    "build_memory_context",
    "build_memory_context_payload",
    "format_memory_row_lines",
    "merge_recent_memory_rows",
]
