from __future__ import annotations

from dataclasses import dataclass, field
import re
import time
from typing import Any, Callable

from .text import clean_text


UNBACKED_PROGRESS_FALLBACK = (
    "이 경로에서는 실제 작업이 시작되지 않았어. 실행 결과 없이 작업 중이라고 말하지 않을게."
)

MINECRAFT_RUNTIME_MARKERS = (
    "마인크래프트",
    "마크",
    "minecraft",
    "mindcraft",
    "보이저",
    "voyager",
)
MINECRAFT_STATUS_MARKERS = (
    "상태",
    "켜져",
    "꺼져",
    "돌아가",
    "실행 중",
    "접속돼",
    "접속해 있어",
)
MINECRAFT_STOP_MARKERS = (
    "꺼",
    "종료",
    "중지",
    "멈춰",
    "나가",
    "접속 끊",
    "disconnect",
    "stop",
    "off",
)
MINECRAFT_START_MARKERS = (
    "시작",
    "켜",
    "실행",
    "접속",
    "들어가",
    "연결",
    "start",
    "connect",
    "run",
    "launch",
)
MINECRAFT_GOAL_MARKERS = (
    "목표",
)
MINECRAFT_INFORMATION_MARKERS = (
    "어떻게",
    "방법",
    "하는 법",
    "설명",
    "알려줘",
    "알려 줘",
    "뭐야",
    "무엇",
)
_MINECRAFT_GOAL_COMMAND_PATTERN = re.compile(
    r"(?:캐|모아|채집|만들|제작|건설|탐험|찾아|사냥|잡아|가져|옮겨|심어|수확|도와|지어)"
    r"(?:\s*(?:줘|해|해줘|해 줘|보자|라|어|아))(?=$|[\s.!?。！？])",
    re.IGNORECASE,
)
_MINECRAFT_GENERIC_START_PATTERN = re.compile(
    r"(?:마인크래프트|마크|minecraft)\s*(?:좀\s*)?(?:해|해줘|해 줘)(?=$|[\s.!?。！？])",
    re.IGNORECASE,
)
_MINECRAFT_EXPLICIT_START_PATTERN = re.compile(
    r"(?:시작|켜|실행|접속|들어가|연결)\s*(?:해|해줘|해 줘|줘)(?=$|[\s.!?。！？])",
    re.IGNORECASE,
)
_MINECRAFT_POLITE_START_PATTERN = re.compile(
    r"(?:시작|켜|실행|접속|들어가|연결)\s*(?:해줘|해 줘|줘)(?=$|[\s.!?。！？])",
    re.IGNORECASE,
)

_UNBACKED_PROGRESS_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"(?:확인|점검|검사|테스트|살펴|알아|찾아|고쳐|수정|바꿔|변경|옮겨|처리|진행|작업|시도)해?\s*볼게",
        r"(?:확인|점검|검사|테스트|살펴|알아|찾아|고쳐|수정|바꿔|변경|옮겨|처리|진행|작업|시도)할게",
        r"(?:시작|착수)할게",
        r"(?:알려|말해)\s*줄게",
        r"(?:해|해\s*보|진행하|처리하|시작하)겠다",
        r"잠시\s*만(?:\s*기다려(?:\s*줘)?)?",
        r"조금\s*만\s*기다려(?:\s*줘)?",
        r"기다려\s*줘",
        r"\b(?:i(?:'ll| will)|let me|give me a moment|please wait)\b",
    )
)

_SENTENCE_PATTERN = re.compile(r".+?(?:[.!?。！？]+(?=\s|$)|$)", re.DOTALL)
_STREAM_TOKEN_END_PATTERN = re.compile(r"[\s.!?。！？]+$")
_STREAM_SUSPICIOUS_LAST_WORDS = {
    "확인",
    "확인해",
    "점검",
    "점검해",
    "검사",
    "검사해",
    "테스트",
    "테스트해",
    "살펴",
    "알아",
    "찾아",
    "고쳐",
    "수정",
    "수정해",
    "바꿔",
    "변경",
    "변경해",
    "옮겨",
    "처리",
    "처리해",
    "진행",
    "진행해",
    "작업",
    "작업해",
    "시도",
    "시도해",
    "시작",
    "착수",
    "알려",
    "말해",
    "잠시",
    "조금",
    "기다려",
    "i",
    "i'll",
    "let",
    "give",
    "please",
}


def has_unbacked_progress_claim(text: str) -> bool:
    normalized = clean_text(text)
    return bool(normalized and any(pattern.search(normalized) for pattern in _UNBACKED_PROGRESS_PATTERNS))


class SafeIncrementalSpeechFilter:
    """Release ordinary words quickly while holding/dropping future-work claims."""

    def __init__(self) -> None:
        self._token = ""
        self._held = ""
        self._blocked_until_sentence_end = False

    @staticmethod
    def _is_sentence_end(value: str) -> bool:
        return bool(re.search(r"[.!?。！？]", value))

    @staticmethod
    def _looks_suspicious(value: str) -> bool:
        normalized = clean_text(value).lower()
        if not normalized:
            return False
        last_word = re.sub(r"[.!?。！？]+$", "", normalized.split()[-1])
        return last_word in _STREAM_SUSPICIOUS_LAST_WORDS

    def _commit_token(self, token: str) -> list[str]:
        sentence_end = self._is_sentence_end(token)
        if self._blocked_until_sentence_end:
            if sentence_end:
                self._blocked_until_sentence_end = False
            return []
        if not clean_text(self._held + token):
            return []

        candidate = self._held + token
        if has_unbacked_progress_claim(candidate):
            self._held = ""
            self._blocked_until_sentence_end = not sentence_end
            return []
        if self._looks_suspicious(candidate) and not sentence_end:
            self._held = candidate
            return []

        self._held = ""
        return [candidate]

    def push(self, fragment: str) -> list[str]:
        output: list[str] = []
        for char in str(fragment or ""):
            self._token += char
            if _STREAM_TOKEN_END_PATTERN.search(self._token):
                output.extend(self._commit_token(self._token))
                self._token = ""
        return output

    def finish(self) -> list[str]:
        output: list[str] = []
        if self._token:
            output.extend(self._commit_token(self._token))
            self._token = ""
        if self._held:
            if not has_unbacked_progress_claim(self._held):
                output.append(self._held)
            self._held = ""
        self._blocked_until_sentence_end = False
        return output


def enforce_action_reply_contract(reply: str, *, active_task_id: str | None = None) -> str:
    normalized = clean_text(reply)
    if not normalized or clean_text(active_task_id):
        return normalized
    if not has_unbacked_progress_claim(normalized):
        return normalized

    kept: list[str] = []
    for match in _SENTENCE_PATTERN.finditer(normalized):
        sentence = clean_text(match.group(0))
        if sentence and not has_unbacked_progress_claim(sentence):
            kept.append(sentence)
    return clean_text(" ".join(kept)) or UNBACKED_PROGRESS_FALLBACK


def is_local_mic_status_request(text: str) -> bool:
    normalized = clean_text(text).lower()
    if not normalized:
        return False
    mentions_mic = any(marker in normalized for marker in ("마이크", "mic", "목소리 입력"))
    mentions_voice_input = "목소리" in normalized and any(
        marker in normalized for marker in ("듣고", "들어", "입력", "인식")
    )
    asks_status = any(
        marker in normalized
        for marker in (
            "상태",
            "확인",
            "켜져",
            "꺼져",
            "되고",
            "돼",
            "작동",
            "활성",
            "입력",
            "캡처",
            "듣고",
            "들어",
            "인식",
            "받고",
        )
    )
    return bool((mentions_mic or mentions_voice_input) and asks_status)


def detect_local_mic_command(text: str) -> str | None:
    normalized = clean_text(text).lower()
    if not normalized:
        return None
    slash_commands = {
        "/mic": "status",
        "/mic status": "status",
        "/mic on": "on",
        "/mic off": "off",
    }
    if normalized in slash_commands:
        return slash_commands[normalized]
    if "마이크" not in normalized:
        return None

    command_text = normalized.rstrip(" .!?。！？")
    if re.search(
        r"마이크(?:\s*입력)?(?:\s*(?:좀|다시))?\s*(?:켜|켜줘|활성화|활성화해|활성화해줘|시작해|시작해줘)$",
        command_text,
    ):
        return "on"
    if re.search(
        r"마이크(?:\s*입력)?(?:\s*(?:좀|이제))?\s*(?:꺼|꺼줘|끄기|비활성화|비활성화해|비활성화해줘|중지해|중지해줘)$",
        command_text,
    ):
        return "off"
    if is_local_mic_status_request(normalized):
        return "status"
    return None


def detect_local_runtime_command(text: str) -> str | None:
    """Detect an explicit Evelyn-wide restart or shutdown command."""

    normalized = clean_text(text).lower().strip()
    if not normalized:
        return None
    compact = re.sub(r"\s+", "", normalized).rstrip(".!?。！？")
    if "?" in normalized or normalized.startswith(
        (
            "왜",
            "어떻게",
            "언제",
            "뭐",
            "무엇",
            "혹시",
            "종료하면",
            "종료해도",
            "재시작하면",
            "재시작해야",
        )
    ):
        return None

    restart_commands = {
        "/restart",
        "/재시작",
        "restart",
        "restartnow",
        "재시작",
        "재시작해",
        "재시작해줘",
        "다시시작",
        "다시시작해",
        "다시시작해줘",
        "재기동",
        "재기동해",
        "재기동해줘",
        "이블린재시작",
        "이블린재시작해",
        "이블린재시작해줘",
        "프로젝트재시작",
        "프로젝트재시작해",
        "프로젝트재시작해줘",
        "이블린다시켜",
        "이블린다시켜줘",
        "프로젝트다시켜",
        "프로젝트다시켜줘",
    }
    if compact in restart_commands:
        return "restart"

    shutdown_commands = {
        "/shutdown",
        "/quit",
        "/exit",
        "/종료",
        "/셧다운",
        "shutdown",
        "quit",
        "exit",
        "셧다운",
        "셧다운해",
        "셧다운해줘",
        "이블린셧다운",
        "이블린셧다운해",
        "이블린셧다운해줘",
        "프로젝트셧다운",
        "프로젝트셧다운해",
        "프로젝트셧다운해줘",
        "종료",
        "종료해",
        "종료해줘",
        "전체종료",
        "전체종료해",
        "전체종료해줘",
        "프로젝트종료",
        "프로젝트종료해",
        "프로젝트종료해줘",
        "프로젝트꺼",
        "프로젝트꺼줘",
        "프로젝트를꺼",
        "프로젝트를꺼줘",
        "이블린종료",
        "이블린종료해",
        "이블린종료해줘",
        "이블린꺼",
        "이블린꺼줘",
        "이블린을꺼",
        "이블린을꺼줘",
        "전체꺼",
        "전체꺼줘",
    }
    if compact in shutdown_commands:
        return "shutdown"
    return None


def detect_minecraft_control_command(text: str) -> str | None:
    """Detect non-starting Minecraft status, inventory, and stop commands."""

    normalized = clean_text(text).lower().strip()
    if not normalized:
        return None
    slash_commands = {
        "/minecraft status": "status",
        "/mc-status": "status",
        "/voyager stats": "stats",
        "/inventory": "inventory",
        "/minecraft inventory": "inventory",
        "/minecraft disconnect": "disconnect",
        "/mc-disconnect": "disconnect",
        "/minecraft stop": "disconnect",
        "/autonomy status": "autonomy_status",
    }
    if normalized in slash_commands:
        return slash_commands[normalized]

    has_minecraft_reference = any(marker in normalized for marker in MINECRAFT_RUNTIME_MARKERS)
    if not has_minecraft_reference:
        if "인벤토리" in normalized or "인벤" in normalized:
            has_minecraft_reference = True
        elif "자율" in normalized and any(marker in normalized for marker in ("상태", "확인", "보여")):
            return "autonomy_status"
    if not has_minecraft_reference:
        return None

    if any(marker in normalized for marker in ("인벤토리", "인벤", "inventory")):
        return "inventory"
    explicit_stop = any(
        marker in normalized
        for marker in (
            "종료해",
            "종료해줘",
            "중지해",
            "중지해줘",
            "멈춰",
            "나가",
            "접속 끊",
            "disconnect",
            "stop",
        )
    ) or bool(re.search(r"(?:꺼|꺼줘|꺼 줘|끄|끄기|꺼라)(?=$|[\s.!?。！？])", normalized))
    if explicit_stop:
        return "disconnect"
    if "stats" in normalized or "통계" in normalized or "진행 상황" in normalized:
        return "stats"
    if any(marker in normalized for marker in MINECRAFT_STATUS_MARKERS) or any(
        marker in normalized for marker in ("상태", "확인", "보여", "어때")
    ):
        return "status"
    return None


def detect_minecraft_runtime_command(text: str) -> str | None:
    """Return the lazy-start action for explicit Minecraft execution commands."""

    normalized = clean_text(text).lower()
    if not normalized:
        return None
    slash_commands = {
        "/minecraft start": "start",
        "/minecraft connect": "start",
        "/mc start": "start",
        "/mc connect": "start",
        "/마크접속": "start",
        "/마크시작": "start",
    }
    if normalized in slash_commands:
        return slash_commands[normalized]
    if not any(marker in normalized for marker in MINECRAFT_RUNTIME_MARKERS):
        return None
    if any(marker in normalized for marker in MINECRAFT_STOP_MARKERS):
        return None
    asks_status = any(marker in normalized for marker in MINECRAFT_STATUS_MARKERS)
    has_explicit_start = bool(_MINECRAFT_EXPLICIT_START_PATTERN.search(normalized))
    asks_information = any(marker in normalized for marker in MINECRAFT_INFORMATION_MARKERS)
    if asks_information and not _MINECRAFT_POLITE_START_PATTERN.search(normalized):
        return None
    has_start = has_explicit_start or bool(_MINECRAFT_GENERIC_START_PATTERN.search(normalized))
    if not has_start:
        has_start = any(marker in normalized for marker in ("start", "connect", "run", "launch"))
    has_goal = any(marker in normalized for marker in MINECRAFT_GOAL_MARKERS) or bool(
        _MINECRAFT_GOAL_COMMAND_PATTERN.search(normalized)
    )
    if asks_status and not has_start and not has_goal:
        return None
    if has_goal:
        return "goal"
    if has_start:
        return "start"
    return None


def compact_local_bridge_context(snapshot: dict[str, Any] | None) -> str:
    bridge = dict(snapshot or {})
    mic = dict(bridge.get("mic") or {})
    mic_enabled = bool(bridge.get("micEnabled", mic.get("enabled", False)))
    capture_active = bool(mic.get("captureActive", False))

    def safe_count(value: Any) -> int:
        try:
            return max(0, int(value or 0))
        except (TypeError, ValueError):
            return 0

    fields = (
        ("local_bridge_enabled", bool(bridge.get("enabled"))),
        ("local_bridge_ready", bool(bridge.get("ready"))),
        ("local_bridge_stale", bool(bridge.get("stale"))),
        ("mic_enabled", mic_enabled),
        ("mic_capture_active", capture_active),
        ("mic_segment_count", safe_count(bridge.get("segmentCount"))),
        ("mic_transcript_count", safe_count(bridge.get("transcriptCount"))),
        ("speaker_active", bool(bridge.get("speaking"))),
        ("local_bridge_last_error", clean_text(bridge.get("lastError")) or "none"),
    )
    return "\n".join(f"{key}={str(value).lower() if isinstance(value, bool) else value}" for key, value in fields)


def render_local_mic_status(snapshot: dict[str, Any] | None) -> str:
    bridge = dict(snapshot or {})
    mic = dict(bridge.get("mic") or {})
    error = clean_text(bridge.get("lastError"))
    if bridge.get("stale"):
        age = bridge.get("ageSec")
        detail = f" 마지막 상태는 {age}초 전이야." if isinstance(age, (int, float)) else ""
        if error:
            detail += f" 오류: {error}"
        return clean_text(f"마이크 상태 정보가 오래돼서 지금 상태를 확정할 수 없어.{detail}")

    mic_enabled = bool(bridge.get("micEnabled", mic.get("enabled", False)))
    if not bridge.get("enabled") or not bridge.get("ready"):
        detail = f" 오류: {error}" if error else ""
        return clean_text(f"로컬 음성 브리지가 준비되지 않아서 마이크 입력은 꺼져 있어.{detail}")
    if not mic_enabled:
        return "마이크 입력은 꺼져 있어."
    if mic.get("captureActive"):
        return "마이크 입력은 켜져 있고 지금 캡처 중이야."
    return "마이크 입력은 켜져 있어. 지금은 말하는 구간을 캡처 중이지 않아."


@dataclass(slots=True)
class FastActionTask:
    task_id: str
    kind: str
    source: str
    user_text: str
    start_reply: str
    status: str = "running"
    created_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    final_reply: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.task_id,
            "kind": self.kind,
            "source": self.source,
            "userText": self.user_text,
            "startReply": self.start_reply,
            "status": self.status,
            "createdAt": self.created_at,
            "finishedAt": self.finished_at,
            "finalReply": self.final_reply,
            "error": self.error,
        }


class FastActionExecutionError(RuntimeError):
    def __init__(self, error: str, *, reply: str) -> None:
        super().__init__(clean_text(error) or "background_action_failed")
        self.reply = clean_text(reply) or "작업 실행 중 오류가 나서 완료하지 못했어."


class FastActionCoordinator:
    def __init__(
        self,
        *,
        history_limit: int = 40,
        time_fn: Callable[[], float] = time.time,
    ) -> None:
        self.history_limit = max(4, int(history_limit))
        self.time_fn = time_fn
        self._task_sequence = 0
        self._event_sequence = 0
        self._tasks: dict[str, FastActionTask] = {}
        self._events: list[dict[str, Any]] = []

    def clear(self) -> None:
        self._task_sequence = 0
        self._event_sequence = 0
        self._tasks.clear()
        self._events.clear()

    def start(
        self,
        *,
        kind: str,
        source: str,
        user_text: str,
        start_reply: str,
    ) -> FastActionTask:
        self._task_sequence += 1
        task_id = f"fast-action-{self._task_sequence}"
        task = FastActionTask(
            task_id=task_id,
            kind=clean_text(kind) or "background",
            source=clean_text(source) or "control_page",
            user_text=clean_text(user_text),
            start_reply=clean_text(start_reply),
            created_at=self.time_fn(),
        )
        self._tasks[task_id] = task
        self._trim_tasks()
        self._append_event(task, event_type="started", reply=task.start_reply)
        return task

    def complete(self, task_id: str, reply: str) -> FastActionTask:
        task = self._require_task(task_id)
        task.status = "completed"
        task.finished_at = self.time_fn()
        task.final_reply = clean_text(reply)
        task.error = ""
        self._append_event(task, event_type="completed", reply=task.final_reply)
        return task

    def fail(self, task_id: str, error: str, *, reply: str = "") -> FastActionTask:
        task = self._require_task(task_id)
        task.status = "failed"
        task.finished_at = self.time_fn()
        task.error = clean_text(error)
        task.final_reply = clean_text(reply)
        self._append_event(task, event_type="failed", reply=task.final_reply, error=task.error)
        return task

    def get(self, task_id: str) -> FastActionTask | None:
        return self._tasks.get(clean_text(task_id))

    def events_after(self, event_id: int = 0) -> list[dict[str, Any]]:
        try:
            cursor = max(0, int(event_id))
        except (TypeError, ValueError):
            cursor = 0
        return [dict(event) for event in self._events if int(event.get("id") or 0) > cursor]

    def snapshot(self) -> dict[str, Any]:
        tasks = [task.to_dict() for task in self._tasks.values()]
        return {
            "activeCount": sum(1 for task in tasks if task["status"] == "running"),
            "lastEventId": self._event_sequence,
            "tasks": tasks,
            "events": [dict(event) for event in self._events],
        }

    def _append_event(
        self,
        task: FastActionTask,
        *,
        event_type: str,
        reply: str = "",
        error: str = "",
    ) -> None:
        self._event_sequence += 1
        event = {
            "id": self._event_sequence,
            "type": clean_text(event_type),
            "taskId": task.task_id,
            "kind": task.kind,
            "status": task.status,
            "reply": clean_text(reply),
            "error": clean_text(error),
            "at": self.time_fn(),
        }
        self._events.append(event)
        del self._events[:-self.history_limit]

    def _trim_tasks(self) -> None:
        while len(self._tasks) > self.history_limit:
            oldest_task_id = next(iter(self._tasks))
            del self._tasks[oldest_task_id]

    def _require_task(self, task_id: str) -> FastActionTask:
        task = self.get(task_id)
        if task is None:
            raise KeyError(f"unknown fast action task: {task_id}")
        return task
