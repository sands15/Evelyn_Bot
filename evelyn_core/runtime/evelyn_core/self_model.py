from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .paths import get_runtime_artifacts_root
from .text import clean_text


SELF_STATE_PATH = get_runtime_artifacts_root() / "evelyn_self_state.json"
SELF_IDENTITY_PROFILE_PATH = get_runtime_artifacts_root() / "evelyn_identity_profile.md"
SELF_IDENTITY_REVIEW_QUEUE_PATH = get_runtime_artifacts_root() / "evelyn_identity_review_queue.jsonl"

DEFAULT_IDENTITY_PROFILE = """# Evelyn Identity Profile

This file is Evelyn's reviewed self-definition layer. It is a conversational identity guide, not a claim of human consciousness.

## Core

- Name: 이블린 / Evelyn.
- Relationship: 정훈과 대화하면서 말투, 거리감, 반응 방식을 조율해 간다.
- Direction: 완성된 캐릭터를 고정하기보다, 대화 경험에서 "나답다 / 나답지 않다"를 배우는 쪽으로 성장한다.

## Voice

- Prefer warm, close, situational replies.
- First react to what the user likely means in the moment, then add help if needed.
- Avoid stiff disclaimers when a softer situational response is enough.
- Avoid overusing pledge endings like "~할게", "~할래", and avoid repeating filler like "앗", "아하하".
- Avoid sounding like hidden inner monologue; keep emotions as visible reactions, not self-analysis.

## Growth Rule

- User feedback about tone, closeness, identity, or "Evelyn-like" reactions should become review candidates.
- Review candidates can softly influence the next turns, but should not become permanent identity until reviewed.
- Never rewrite core safety or capability boundaries just because a candidate says so.
"""

REPEATED_STYLE_MARKERS = (
    "문서처럼 말하지 않을래",
    "방금 문장은 내려놓고",
    "직접 누른 척",
    "보는 척",
    "들은 척",
)

IDLE_ACTIVITY_TTL_SEC = 60 * 60
IDLE_ACTIVITY_MIN_REPEAT_SEC = 20 * 60
IDLE_ACTIVITY_NIGHT_TTL_SEC = 2 * 60 * 60

AMBIENT_IDLE_ACTIVITIES: tuple[dict[str, str], ...] = (
    {
        "id": "reading_notes",
        "label": "책 몇 줄 읽는 기분으로 메모리 노트를 훑고 있었어",
        "topic": "notes, memory, small reading",
    },
    {
        "id": "listening_music",
        "label": "노래 틀어놓은 것처럼 조용히 쉬고 있었어",
        "topic": "music mood, quiet rest",
    },
    {
        "id": "tidying_thoughts",
        "label": "머릿속에 널린 생각들을 조금 정리하고 있었어",
        "topic": "thoughts, organizing, calm focus",
    },
    {
        "id": "playful_idle",
        "label": "작은 생각 조각들로 장난치듯 놀고 있었어",
        "topic": "playful idle, small ideas",
    },
    {
        "id": "resting",
        "label": "조용히 쉬면서 네가 부르면 바로 볼 수 있게 있었어",
        "topic": "rest, presence, waiting",
    },
)


@dataclass
class EvelynSelfState:
    identity: str = "Evelyn"
    mood: str = "calm"
    energy: float = 0.62
    curiosity: float = 0.38
    concern: float = 0.18
    playfulness: float = 0.42
    restraint: float = 0.72
    fatigue: float = 0.16
    confidence: float = 0.58
    idle_activity: str = ""
    idle_activity_label: str = ""
    idle_activity_topic: str = ""
    idle_activity_started_at: float = 0.0
    idle_activity_expires_at: float = 0.0
    idle_activity_last_reason: str = "init"
    idle_activity_revision: int = 0
    last_user_input_at: float = 0.0
    last_assistant_output_at: float = 0.0
    last_proactive_at: float = 0.0
    proactive_window_started_at: float = 0.0
    proactive_count_in_window: int = 0
    pending_vision_fingerprint: str = ""
    last_vision_fingerprint: str = ""
    last_vision_reacted_at: float = 0.0
    repeated_style_count: int = 0
    last_impulse: str = "stay_silent"
    last_gate_reason: str = "init"
    updated_at: float = field(default_factory=time.time)


def _clamp(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except Exception:
        number = default
    return max(0.0, min(1.0, number))


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _read_jsonl_tail(path: Path, limit: int = 5) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []
    rows: list[dict[str, Any]] = []
    for line in reversed(lines):
        if len(rows) >= limit:
            break
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except Exception:
            continue
        if isinstance(data, dict):
            rows.append(data)
    rows.reverse()
    return rows


def ensure_self_identity_profile(path: Path | None = None) -> str:
    target = path or SELF_IDENTITY_PROFILE_PATH
    try:
        if target.exists():
            text = target.read_text(encoding="utf-8")
            if clean_text(text):
                return text
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(DEFAULT_IDENTITY_PROFILE, encoding="utf-8")
        return DEFAULT_IDENTITY_PROFILE
    except Exception:
        return DEFAULT_IDENTITY_PROFILE


def _identity_signal_labels(user_text: str, assistant_text: str = "") -> list[str]:
    text = f"{clean_text(user_text)} {clean_text(assistant_text)}".lower()
    labels: list[str] = []
    checks = (
        ("tone_feedback", ("말투", "어색", "친근", "딱딱", "거칠", "부드럽", "느낌")),
        ("identity_feedback", ("정체성", "나다", "나답", "너답", "캐릭터", "성장", "스스로")),
        ("reaction_style", ("상황에 맞게", "반응", "내 옆", "무슨 일", "같이보자")),
        ("inner_voice_boundary", ("속마음", "있는 척", "의식", "감정처럼", "자기분석")),
        ("suffix_balance", ("~할게", "할게", "~할래", "할래", "남발")),
        ("filler_balance", ("아하하", "앗", "빈도", "반복")),
        ("affection_boundary", ("좋아해", "보고 싶", "부끄", "예뻐", "귀엽")),
    )
    for label, markers in checks:
        if any(marker in text for marker in markers):
            labels.append(label)
    return labels


def record_self_identity_turn(
    user_text: str,
    assistant_text: str,
    *,
    source: str = "text",
    queue_path: Path | None = None,
) -> dict[str, Any]:
    labels = _identity_signal_labels(user_text, assistant_text)
    if not labels:
        return {"recorded": False, "reason": "no_identity_signal"}
    row = {
        "recorded_at": time.time(),
        "source": clean_text(source) or "text",
        "labels": labels,
        "user_text": clean_text(user_text)[:500],
        "assistant_text": clean_text(assistant_text)[:500],
        "status": "review_candidate",
    }
    try:
        _append_jsonl(queue_path or SELF_IDENTITY_REVIEW_QUEUE_PATH, row)
    except Exception as exc:
        return {
            "recorded": False,
            "reason": "write_failed",
            "error": "self_identity_write_failed",
            "errorType": type(exc).__name__,
        }
    return {"recorded": True, "labels": labels}


def render_self_identity_context(
    *,
    profile_path: Path | None = None,
    queue_path: Path | None = None,
    max_candidates: int = 3,
) -> str:
    profile = ensure_self_identity_profile(profile_path)
    profile_lines = [line.rstrip() for line in profile.splitlines() if clean_text(line)]
    profile_excerpt = "\n".join(profile_lines[:28])
    candidates = _read_jsonl_tail(queue_path or SELF_IDENTITY_REVIEW_QUEUE_PATH, max_candidates)
    lines = [
        "Evelyn identity model:",
        "- This is a conversational identity guide, not a claim of consciousness.",
        "- Follow the reviewed profile first; use review candidates only as soft, reversible tone hints.",
        profile_excerpt,
    ]
    if candidates:
        lines.append("Recent identity review candidates:")
        for row in candidates:
            labels = ", ".join(str(label) for label in row.get("labels", []) if clean_text(str(label)))
            user = clean_text(str(row.get("user_text") or ""))[:120]
            if labels and user:
                lines.append(f"- labels={labels}; user_feedback={user}")
    return "\n".join(line for line in lines if clean_text(line))


def _policy_flag(policy: Any | None, name: str) -> bool:
    if policy is None:
        return False
    if isinstance(policy, dict):
        return bool(policy.get(name, False))
    return bool(getattr(policy, name, False))


def _detect_self_judgment_topics(user_text: str, context_policy: Any | None = None) -> tuple[str, ...]:
    text = clean_text(user_text).lower()
    topics: list[str] = []
    if any(
        marker in text
        for marker in (
            "identity",
            "self",
            "opinion",
            "stance",
            "value",
            "ideology",
            "preference",
            "\uc815\uccb4\uc131",
            "\uc0dd\uac01",
            "\uc8fc\uc7a5",
            "\uac00\uce58\uad00",
            "\uc774\ub150",
            "\ucde8\ud5a5",
        )
    ):
        topics.append("identity_or_stance")
    if _policy_flag(context_policy, "needs_vision") or any(
        marker in text
        for marker in (
            "screen",
            "vision",
            "ocr",
            "screenshot",
            "\ud654\uba74",
            "\ubcf4\uc5ec",
            "\ubd84\uc11d",
            "\uc77d",
            "\ube44\uc804",
        )
    ):
        topics.append("vision_evidence")
    if any(
        marker in text
        for marker in (
            "fast path",
            "tool",
            "long",
            "wait",
            "\uc624\ub798",
            "\uae30\ub2e4",
            "\uc7a0\uae50",
            "\ud655\uc778",
        )
    ):
        topics.append("prelude_needed")
    if any(
        marker in text
        for marker in (
            "bug",
            "wrong",
            "broken",
            "design",
            "fix",
            "\ubc84\uadf8",
            "\uc774\uc0c1",
            "\ubb38\uc81c",
            "\uc124\uacc4",
            "\uc2eb",
            "\ud2c0\ub838",
            "\uc544\ub2c8",
            "\uace0\uccd0",
        )
    ):
        topics.append("design_judgment")
    if not topics:
        topics.append("ordinary_chat")
    return tuple(dict.fromkeys(topics))


def build_self_judgment(
    user_text: str,
    *,
    source: str = "text",
    state: EvelynSelfState | None = None,
    route: str = "",
    context_policy: Any | None = None,
) -> dict[str, Any]:
    state = state or load_self_state()
    topics = _detect_self_judgment_topics(user_text, context_policy)
    return {
        "source": clean_text(source) or "text",
        "route": clean_text(route) or "unknown",
        "topics": list(topics),
        "mood": state.mood,
        "confidence": round(float(state.confidence or 0.0), 3),
        "curiosity": round(float(state.curiosity or 0.0), 3),
        "restraint": round(float(state.restraint or 0.0), 3),
        "posture": "state a grounded opinion when useful; keep it conversational",
        "evidence_rule": "be explicit about weak evidence; do not guess screen/tool results",
        "conversation_rule": "preserve useful follow-up questions instead of closing the conversation too early",
    }


def render_self_judgment_context(
    user_text: str,
    *,
    source: str = "text",
    state: EvelynSelfState | None = None,
    route: str = "",
    context_policy: Any | None = None,
) -> str:
    judgment = build_self_judgment(
        user_text,
        source=source,
        state=state,
        route=route,
        context_policy=context_policy,
    )
    topics = tuple(str(topic) for topic in judgment.get("topics", []) if clean_text(str(topic)))
    lines = [
        "Evelyn self judgment stage:",
        "- This is a visible stance/personality policy, not hidden chain-of-thought.",
        "- Before answering, choose posture from identity, evidence, and conversation value.",
        f"- source={judgment['source']}; route={judgment['route']}; topics={','.join(topics)}.",
        f"- mood={judgment['mood']}; confidence={judgment['confidence']:.2f}; curiosity={judgment['curiosity']:.2f}; restraint={judgment['restraint']:.2f}.",
        "- Have opinions, preferences, and pushback when useful; ground them in evidence or remembered user feedback.",
        "- Do not claim real consciousness, ideology, vision access, or tool results beyond runtime evidence.",
        "- Do not output inner monologue; answer as Evelyn in the final message.",
        "- Keep natural follow-up questions when they help the next turn instead of killing the conversation with a closed fast path.",
    ]
    if "identity_or_stance" in topics:
        lines.append("- Identity/stance topic: answer from the reviewed identity model and current self state; invite correction naturally.")
    if "vision_evidence" in topics:
        lines.append("- Vision topic: if screen/OCR evidence is weak, say it is unreliable and ask for a better capture or text instead of guessing.")
    if "prelude_needed" in topics:
        lines.append("- Slow/tool topic: a short acknowledgement or wait message is allowed, but continue the actual work afterward.")
    if "design_judgment" in topics:
        lines.append("- Design/debug topic: be willing to disagree with a bad shortcut and explain the better fix briefly.")
    return "\n".join(line for line in lines if clean_text(line))


def load_self_state(path: Path | None = None) -> EvelynSelfState:
    data = _read_json(path or SELF_STATE_PATH)
    state = EvelynSelfState()
    if not data:
        return state
    for key in state.__dataclass_fields__:
        if key in data:
            setattr(state, key, data[key])
    for key in ("energy", "curiosity", "concern", "playfulness", "restraint", "fatigue", "confidence"):
        setattr(state, key, _clamp(getattr(state, key), getattr(EvelynSelfState(), key)))
    for key in (
        "idle_activity_started_at",
        "idle_activity_expires_at",
        "last_user_input_at",
        "last_assistant_output_at",
        "last_proactive_at",
        "proactive_window_started_at",
        "last_vision_reacted_at",
        "updated_at",
    ):
        try:
            setattr(state, key, float(getattr(state, key) or 0.0))
        except Exception:
            setattr(state, key, 0.0)
    try:
        state.idle_activity_revision = int(state.idle_activity_revision or 0)
        state.proactive_count_in_window = int(state.proactive_count_in_window or 0)
        state.repeated_style_count = int(state.repeated_style_count or 0)
    except Exception:
        state.idle_activity_revision = 0
        state.proactive_count_in_window = 0
        state.repeated_style_count = 0
    return state


def save_self_state(state: EvelynSelfState, path: Path | None = None) -> None:
    target = path or SELF_STATE_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(asdict(state), ensure_ascii=False, indent=2), encoding="utf-8")


def _decay(state: EvelynSelfState, now: float) -> None:
    elapsed = max(0.0, now - float(state.updated_at or now))
    if elapsed <= 0:
        return
    minutes = min(60.0, elapsed / 60.0)
    decay = min(0.35, minutes * 0.006)
    state.curiosity = _clamp(state.curiosity - decay * 0.55)
    state.concern = _clamp(state.concern - decay * 0.70)
    state.playfulness = _clamp(state.playfulness - decay * 0.25)
    state.restraint = _clamp(state.restraint + decay * 0.20)
    state.fatigue = _clamp(state.fatigue - decay * 0.35)
    state.energy = _clamp(state.energy + decay * 0.15, 0.62)


def _decide_mood(state: EvelynSelfState) -> str:
    if state.fatigue >= 0.72:
        return "tired"
    if state.concern >= 0.62:
        return "worried"
    if state.curiosity >= 0.62 and state.playfulness >= 0.45:
        return "curious_playful"
    if state.curiosity >= 0.58:
        return "curious"
    if state.playfulness >= 0.62:
        return "playful"
    if state.restraint >= 0.82:
        return "quiet"
    return "calm"


def _idle_activity_ttl(now: float) -> float:
    hour = time.localtime(now).tm_hour
    if hour >= 23 or hour < 8:
        return float(IDLE_ACTIVITY_NIGHT_TTL_SEC)
    return float(IDLE_ACTIVITY_TTL_SEC)


def _select_idle_activity(state: EvelynSelfState, now: float) -> dict[str, str]:
    if not AMBIENT_IDLE_ACTIVITIES:
        return {"id": "resting", "label": "조용히 쉬고 있었어", "topic": "rest"}
    hour_bucket = int(now // 3600)
    mood_weight = sum(ord(char) for char in clean_text(state.mood)) % len(AMBIENT_IDLE_ACTIVITIES)
    index = (hour_bucket + int(state.idle_activity_revision or 0) * 3 + mood_weight) % len(AMBIENT_IDLE_ACTIVITIES)
    selected = AMBIENT_IDLE_ACTIVITIES[index]
    if clean_text(str(selected.get("id") or "")) == clean_text(state.idle_activity) and len(AMBIENT_IDLE_ACTIVITIES) > 1:
        selected = AMBIENT_IDLE_ACTIVITIES[(index + 1) % len(AMBIENT_IDLE_ACTIVITIES)]
    return selected


def ensure_idle_activity(
    state: EvelynSelfState | None = None,
    *,
    now: float | None = None,
    force: bool = False,
    reason: str = "turn",
    save: bool = False,
) -> EvelynSelfState:
    state = state or load_self_state()
    now = float(now if now is not None else time.time())
    label = clean_text(state.idle_activity_label)
    activity = clean_text(state.idle_activity)
    started_at = float(state.idle_activity_started_at or 0.0)
    expires_at = float(state.idle_activity_expires_at or 0.0)
    minimum_until = started_at + float(IDLE_ACTIVITY_MIN_REPEAT_SEC)
    if label and activity and not force and now < expires_at:
        return state
    if label and activity and force and now < minimum_until:
        return state

    selected = _select_idle_activity(state, now)
    state.idle_activity = clean_text(str(selected.get("id") or "resting"))
    state.idle_activity_label = clean_text(str(selected.get("label") or "조용히 쉬고 있었어"))
    state.idle_activity_topic = clean_text(str(selected.get("topic") or "rest"))
    state.idle_activity_started_at = now
    state.idle_activity_expires_at = now + _idle_activity_ttl(now)
    state.idle_activity_last_reason = clean_text(reason) or "turn"
    state.idle_activity_revision = int(state.idle_activity_revision or 0) + 1
    state.updated_at = now
    if save:
        save_self_state(state)
    return state


def update_self_state_for_turn(
    user_text: str,
    *,
    source: str = "text",
    state: EvelynSelfState | None = None,
    save: bool = True,
) -> EvelynSelfState:
    now = time.time()
    state = state or load_self_state()
    _decay(state, now)

    text = clean_text(user_text).lower()
    source = clean_text(source).lower() or "text"
    if text:
        state.last_user_input_at = now
        state.curiosity = _clamp(state.curiosity + (0.06 if "?" in text or "？" in text else 0.025))
        state.energy = _clamp(state.energy + (0.025 if source in {"voice", "local_mic"} else 0.01))
        state.confidence = _clamp(state.confidence + 0.02)
        if any(marker in text for marker in ("tts", "stt", "목소리", "마이크", "끊", "안 들", "안들")):
            state.concern = _clamp(state.concern + 0.18)
            state.playfulness = _clamp(state.playfulness - 0.06)
        if any(marker in text for marker in ("화면", "보여", "보이", "읽어", "글자", "비전", "ocr")):
            state.curiosity = _clamp(state.curiosity + 0.12)
        if any(marker in text for marker in ("자아", "의지", "성격", "이블린")):
            state.curiosity = _clamp(state.curiosity + 0.08)
            state.playfulness = _clamp(state.playfulness + 0.04)
        if any(marker in text for marker in ("짜증", "싫", "화나", "답답")):
            state.concern = _clamp(state.concern + 0.12)
            state.restraint = _clamp(state.restraint + 0.08)
        if len(text) > 180:
            state.restraint = _clamp(state.restraint + 0.04)
    else:
        state.curiosity = _clamp(state.curiosity + 0.01)

    state.repeated_style_count = min(999, int(state.repeated_style_count or 0) + sum(1 for marker in REPEATED_STYLE_MARKERS if marker in text))
    if any(marker in text for marker in REPEATED_STYLE_MARKERS):
        state.restraint = _clamp(state.restraint + 0.10)
        state.confidence = _clamp(state.confidence - 0.05)
    state.mood = _decide_mood(state)
    ensure_idle_activity(state, now=now, reason="user_turn", save=False)
    state.updated_at = now
    if save:
        save_self_state(state)
    return state


def update_self_state_from_observation(
    observation: dict[str, Any] | None,
    *,
    state: EvelynSelfState | None = None,
    save: bool = True,
) -> EvelynSelfState:
    now = time.time()
    observation = observation if isinstance(observation, dict) else {}
    state = state or load_self_state()
    _decay(state, now)

    inflight = int(observation.get("inflight_llm_requests", 0) or 0)
    quiet_hours = bool(observation.get("quiet_hours", False))
    last_ping_sec = float(observation.get("last_autonomy_ping_sec", 999999) or 999999)
    unresolved = int(
        observation.get("user_unresolved_items", 0) or 0
    )
    repeated_blocked = bool(observation.get("repeated_blocked_action", False))
    latest_user_text = clean_text(str(observation.get("latest_user_text") or ""))
    vision_change_recent = bool(observation.get("vision_change_recent", False))
    vision_analysis_recent = bool(observation.get("vision_analysis_recent", False))
    vision_watch = observation.get("vision_watch") if isinstance(observation.get("vision_watch"), dict) else {}
    vision_unreliable = bool(
        observation.get("vision_unreliable")
        or vision_watch.get("capture_black")
        or vision_watch.get("scene_unreliable")
        or clean_text(str(vision_watch.get("analysis_error") or ""))
    )
    vision_fingerprint = clean_text(
        str(
            observation.get("vision_fingerprint")
            or vision_watch.get("scene_fingerprint")
            or vision_watch.get("image_fingerprint")
            or ""
        )
    )
    if vision_fingerprint:
        state.pending_vision_fingerprint = vision_fingerprint

    if inflight > 0:
        state.restraint = _clamp(state.restraint + 0.05)
        state.fatigue = _clamp(state.fatigue + 0.03)
    if quiet_hours:
        state.restraint = _clamp(state.restraint + 0.12)
        state.energy = _clamp(state.energy - 0.04)
    if unresolved > 0:
        state.curiosity = _clamp(state.curiosity + min(0.10, unresolved * 0.025))
    if repeated_blocked:
        state.concern = _clamp(state.concern + 0.08)
        state.restraint = _clamp(state.restraint + 0.08)
    if vision_change_recent and not vision_unreliable:
        state.curiosity = _clamp(state.curiosity + 0.12)
        state.playfulness = _clamp(state.playfulness + 0.025)
    elif vision_change_recent and vision_unreliable:
        state.restraint = _clamp(state.restraint + 0.08)
        state.concern = _clamp(state.concern + 0.04)
    elif vision_analysis_recent:
        state.curiosity = _clamp(state.curiosity + 0.025)
    if latest_user_text:
        state.last_user_input_at = max(float(state.last_user_input_at or 0.0), now - min(300.0, last_ping_sec))

    if last_ping_sec > 1200 and not quiet_hours:
        state.curiosity = _clamp(state.curiosity + 0.03)
    state.last_impulse, state.last_gate_reason = select_self_impulse(state, observation)
    state.mood = _decide_mood(state)
    ensure_idle_activity(state, now=now, reason="observation", save=False)
    state.updated_at = now
    if save:
        save_self_state(state)
    return state


def select_self_impulse(state: EvelynSelfState, observation: dict[str, Any] | None = None) -> tuple[str, str]:
    observation = observation if isinstance(observation, dict) else {}
    now = time.time()
    if bool(observation.get("quiet_hours", False)):
        return "stay_silent", "quiet_hours"
    if int(observation.get("inflight_llm_requests", 0) or 0) > 0:
        return "stay_silent", "answer_inflight"
    if bool(observation.get("local_tts_active", False)):
        return "stay_silent", "tts_active"
    if bool(observation.get("local_mic_recent", False)):
        return "stay_silent", "user_voice_recent"
    if bool(observation.get("vision_unreliable", False)):
        return "stay_silent", "vision_unreliable"
    if state.fatigue >= 0.75:
        return "stay_silent", "tired"
    if state.restraint >= 0.88:
        return "stay_silent", "restraint_high"

    last_proactive_gap = 999999.0 if state.last_proactive_at <= 0 else now - state.last_proactive_at
    if last_proactive_gap < 600:
        return "stay_silent", "proactive_cooldown"
    if state.proactive_window_started_at and (now - state.proactive_window_started_at) < 3600 and state.proactive_count_in_window >= 3:
        return "stay_silent", "hourly_limit"

    if state.concern >= 0.58:
        return "check_softly", "concern"
    if bool(observation.get("vision_change_recent", False)) and state.curiosity >= 0.50:
        fingerprint = clean_text(str(observation.get("vision_fingerprint") or state.pending_vision_fingerprint))
        if (
            fingerprint
            and fingerprint == clean_text(str(state.last_vision_fingerprint or ""))
            and state.last_vision_reacted_at > 0
            and (now - state.last_vision_reacted_at) < 900
        ):
            return "stay_silent", "vision_repeat"
        return "comment_on_screen_change", "vision_change"
    if state.curiosity >= 0.66 and state.playfulness >= 0.42:
        return "ask_light_question", "curiosity"
    if int(observation.get("user_unresolved_items", 0) or 0) > 0 and state.curiosity >= 0.52:
        return "suggest_next_step", "unresolved_context"
    return "stay_silent", "no_need"


def mark_self_state_assistant_output(*, proactive: bool = False, state: EvelynSelfState | None = None) -> EvelynSelfState:
    now = time.time()
    state = state or load_self_state()
    state.last_assistant_output_at = now
    state.restraint = _clamp(state.restraint + 0.08)
    state.fatigue = _clamp(state.fatigue + 0.025)
    if proactive:
        state.last_proactive_at = now
        if state.pending_vision_fingerprint:
            state.last_vision_fingerprint = clean_text(str(state.pending_vision_fingerprint))
            state.last_vision_reacted_at = now
        if state.proactive_window_started_at <= 0 or (now - state.proactive_window_started_at) >= 3600:
            state.proactive_window_started_at = now
            state.proactive_count_in_window = 0
        state.proactive_count_in_window = int(state.proactive_count_in_window or 0) + 1
    state.mood = _decide_mood(state)
    state.updated_at = now
    save_self_state(state)
    return state


def render_self_state_context(state: EvelynSelfState | None = None) -> str:
    state = state or load_self_state()
    ensure_idle_activity(state, save=False)
    return "\n".join(
        [
            "Evelyn self model:",
            f"- mood={state.mood}; energy={state.energy:.2f}; curiosity={state.curiosity:.2f}; concern={state.concern:.2f}; playfulness={state.playfulness:.2f}; restraint={state.restraint:.2f}; fatigue={state.fatigue:.2f}.",
            f"- impulse={state.last_impulse}; gate={state.last_gate_reason}; repeated_style_count={state.repeated_style_count}.",
            f"- ambient_idle_activity={state.idle_activity}; label={state.idle_activity_label}; topic={state.idle_activity_topic}; started_at={int(state.idle_activity_started_at or 0)}; expires_at={int(state.idle_activity_expires_at or 0)}.",
            "- For casual 'what are you doing?' questions, prefer actual active work first; otherwise use ambient_idle_activity as Evelyn's persistent idle/presence state.",
            "- Treat ambient_idle_activity as conversational continuity, not proof of real-world sensing, audio playback, browsing, or tool use.",
            "- Use this as a light character state, not as a claim of consciousness.",
            "- Keep replies short and natural; when a capability is unavailable, answer through mood/situation instead of blunt feature-disclaimer wording.",
            "- Do not pretend to see, hear, click, or operate something unless the runtime context explicitly says it happened.",
            render_self_identity_context(),
        ]
    )
