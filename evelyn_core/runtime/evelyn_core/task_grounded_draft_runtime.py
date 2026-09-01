from __future__ import annotations

import hashlib
import ipaddress
import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping
from urllib.parse import parse_qsl, urlsplit, urlunsplit

from .text import clean_text


GROUNDED_DRAFT_SCHEMA = "evelyn.task-grounded-draft.v1"
GROUNDED_EVIDENCE_MANIFEST_SCHEMA = "evelyn.task-grounded-evidence-manifest.v1"
GROUNDED_DRAFT_STATUS = "grounded_draft_ready"
GROUNDED_DRAFT_CODE = "grounded_draft_ready"
GROUNDED_DRAFT_KINDS = frozenset({"review", "summarize", "explain", "compare"})
GROUNDED_DRAFT_TTS_TEXT = "근거가 연결된 검토용 초안을 화면에 준비했어."
MAX_GROUNDED_SECTIONS = 4
MAX_GROUNDED_CLAIMS_PER_SECTION = 4
MAX_GROUNDED_CLAIMS = 8
MAX_GROUNDED_TITLE_CHARS = 80
MAX_GROUNDED_CLAIM_CHARS = 500
MAX_GROUNDED_FRAGMENTS = 8
MAX_GROUNDED_FRAGMENT_CHARS = 800
MAX_GROUNDED_CONTEXT_BYTES = 6 * 1024

_TASK_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_EVIDENCE_REF_RE = re.compile(r"evref-[0-9a-f]{64}\Z")
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_DOMAIN_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
_URL_IN_TEXT_RE = re.compile(
    r"(?i)(?:https?://|ftp://|file:|mailto:|data:|javascript:|www\.|"
    r"\]\s*\(|<\s*a\b|(?:href|src)\s*=|(?<!:)//[a-z0-9])"
)
_MARKDOWN_ESCAPE_CHARS = frozenset("\\`*{}[]<>()#+!_|")
_REDIRECT_QUERY_KEYS = frozenset(
    {
        "continue",
        "dest",
        "destination",
        "next",
        "redirect",
        "redirect_to",
        "redirect_uri",
        "return",
        "return_to",
        "target",
        "url",
    }
)
_CREDENTIAL_QUERY_KEYS = frozenset(
    {
        "access_token",
        "api_key",
        "apikey",
        "auth",
        "code",
        "credential",
        "key",
        "password",
        "secret",
        "sig",
        "signature",
        "token",
    }
)
_BLOCKED_HOST_SUFFIXES = (
    ".home",
    ".internal",
    ".invalid",
    ".lan",
    ".local",
    ".localhost",
    ".onion",
    ".test",
)
_BLOCKED_REDIRECT_HOSTS = frozenset(
    {
        "bit.ly",
        "goo.gl",
        "is.gd",
        "t.co",
        "tinyurl.com",
    }
)
_INTERNAL_OBSERVATION_FIELDS = frozenset(
    {
        "schema",
        "step",
        "tool",
        "attempted",
        "executed",
        "observed",
        "verified",
        "outcome",
        "code",
        "summary",
        "evidence",
    }
)
_WORKER_OBSERVATION_FIELDS = _INTERNAL_OBSERVATION_FIELDS | {"successCriteria"}
_SERIALIZED_OBSERVATION_FIELDS = frozenset(
    {"step", "tool", "verified", "outcome", "code", "summary", "evidence"}
)
_GROUNDED_TASK_PAYLOAD_FIELDS = frozenset(
    {
        "schema",
        "taskId",
        "status",
        "code",
        "summary",
        "stepCount",
        "modelCallCount",
        "approvalTool",
        "observations",
        "groundedDraft",
    }
)


class GroundedDraftError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class GroundedEvidenceFragment:
    task_id: str
    step_id: int
    tool: str
    evidence_ref: str
    source_label: str
    content: str = field(repr=False)
    verified_url: str = field(default="", repr=False)

    def worker_record(self) -> dict[str, Any]:
        return {
            "stepId": self.step_id,
            "evidenceRef": self.evidence_ref,
            "sourceLabel": self.source_label,
            "content": self.content,
        }


@dataclass(frozen=True, slots=True)
class GroundedClaim:
    text: str
    step_id: int
    evidence_ref: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "stepId": self.step_id,
            "evidenceRef": self.evidence_ref,
        }


@dataclass(frozen=True, slots=True)
class GroundedSection:
    title: str
    claims: tuple[GroundedClaim, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "claims": [claim.to_dict() for claim in self.claims],
        }


@dataclass(frozen=True, slots=True)
class GroundedDraft:
    task_id: str
    kind: str
    sections: tuple[GroundedSection, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": GROUNDED_DRAFT_SCHEMA,
            "taskId": self.task_id,
            "kind": self.kind,
            "sections": [section.to_dict() for section in self.sections],
            "semanticVerified": False,
            "humanReviewRequired": True,
        }


_KIND_PATTERNS = {
    "review": re.compile(
        r"(?:검토(?:해|하고|하여|해주세요|하세요|하십시오|하라)|\b(?:review|critique|assess)\b)",
        re.IGNORECASE,
    ),
    "summarize": re.compile(
        r"(?:요약(?:해|하고|하여|해주세요|하세요|하십시오|하라)|\bsummari[sz]e\b)",
        re.IGNORECASE,
    ),
    "explain": re.compile(
        r"(?:설명(?:해|하고|하여|해주세요|하세요|하십시오|하라)|\bexplain\b)",
        re.IGNORECASE,
    ),
    "compare": re.compile(
        r"(?:비교(?:해|하고|하여|해주세요|하세요|하십시오|하라)|\bcompare\b)",
        re.IGNORECASE,
    ),
}
_NEGATED_KIND_PATTERNS = {
    "review": re.compile(r"(?:검토하지\s*마|검토\s*말고|(?:do\s+not|don't|never)\s+(?:review|critique|assess))", re.IGNORECASE),
    "summarize": re.compile(r"(?:요약하지\s*마|요약\s*말고|(?:do\s+not|don't|never)\s+summari[sz]e)", re.IGNORECASE),
    "explain": re.compile(r"(?:설명하지\s*마|설명\s*말고|(?:do\s+not|don't|never)\s+explain)", re.IGNORECASE),
    "compare": re.compile(r"(?:비교하지\s*마|비교\s*말고|(?:do\s+not|don't|never)\s+compare)", re.IGNORECASE),
}


def grounded_draft_kind(goal: Any) -> str | None:
    text = clean_text(str(goal or ""))
    matches = [
        kind
        for kind, pattern in _KIND_PATTERNS.items()
        if pattern.search(text) is not None
        and _NEGATED_KIND_PATTERNS[kind].search(text) is None
    ]
    return matches[0] if len(matches) == 1 else None


def explicit_link_requested(goal: Any) -> bool:
    text = clean_text(str(goal or ""))
    if re.search(
        r"(?:링크|URL|웹\s*주소|출처\s*주소).{0,12}"
        r"(?:없이|빼|제외|말고|필요\s*없|넣지|포함하지|표시하지)|"
        r"(?:없이|빼|제외|말고).{0,12}(?:링크|URL|웹\s*주소)|"
        r"\b(?:without|no)\s+(?:source\s+)?(?:links?|urls?)\b|"
        r"\bdo\s+not\s+(?:include|show)\s+(?:source\s+)?(?:links?|urls?)\b",
        text,
        re.IGNORECASE,
    ):
        return False
    return re.search(
        r"(?:링크|URL|웹\s*주소|출처\s*주소)|"
        r"\b(?:source\s+)?(?:links?|urls?)\b|\bwebsite\s+address\b",
        text,
        re.IGNORECASE,
    ) is not None


def safe_verified_https_url(value: Any, *, redirected: bool = False) -> str:
    raw = str(value or "").strip()
    lowered = raw.casefold()
    if (
        redirected
        or not raw
        or len(raw) > 300
        or any(ord(char) < 33 or ord(char) == 127 for char in raw)
        or any(token in lowered for token in ("%00", "%0a", "%0d"))
    ):
        return ""
    try:
        parsed = urlsplit(raw)
        host = (parsed.hostname or "").lower().rstrip(".")
        port = parsed.port
    except ValueError:
        return ""
    if (
        parsed.scheme.lower() != "https"
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or host in _BLOCKED_REDIRECT_HOSTS
        or host == "localhost"
        or host.endswith(_BLOCKED_HOST_SUFFIXES)
    ):
        return ""
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        labels = host.split(".")
        if (
            len(labels) < 2
            or any(_DOMAIN_RE.fullmatch(label) is None for label in labels)
        ):
            return ""
    else:
        if not address.is_global:
            return ""
    try:
        query = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=False)
    except ValueError:
        return ""
    query_keys = {key.casefold() for key, _value in query}
    if query_keys & (_REDIRECT_QUERY_KEYS | _CREDENTIAL_QUERY_KEYS) or any(
        "://" in query_value or query_value.startswith("//")
        for _query_key, query_value in query
    ):
        return ""
    normalized_host = f"[{host}]" if ":" in host else host
    netloc = normalized_host if port is None else f"{normalized_host}:{port}"
    return urlunsplit(("https", netloc, parsed.path or "/", parsed.query, ""))


def _visible_text(value: Any, *, maximum: int) -> str:
    if not isinstance(value, str):
        return ""
    text = clean_text(value)
    if (
        not text
        or len(text) > maximum
        or "\x00" in text
        or any(ord(character) < 32 and character not in "\t\n\r" for character in text)
    ):
        return ""
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return ""
    return text


def _markdown_text(value: str) -> str:
    return "".join(
        f"\\{character}" if character in _MARKDOWN_ESCAPE_CHARS else character
        for character in value
    )


def _normalized_path(value: Any) -> str:
    path = str(value or "").strip().replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    return path.rstrip("/")


def _safe_workspace_path(value: Any) -> str:
    path = _normalized_path(value)
    if (
        not path
        or path.startswith(("/", "~"))
        or re.match(r"^[a-zA-Z]:/", path) is not None
        or ".." in path.split("/")
    ):
        return ""
    return path


def _path_within(path: Any, base: Any) -> bool:
    child = _safe_workspace_path(path).casefold()
    parent = _safe_workspace_path(base).casefold()
    return bool(
        child
        and parent
        and (parent == "." or child == parent or child.startswith(f"{parent}/"))
    )


def _observation_evidence(observation: Mapping[str, Any]) -> dict[str, Any] | None:
    raw = observation.get("evidence")
    limit = {
        "workspace_read": 4_000,
        "web_search": 5_000,
    }.get(str(observation.get("tool") or ""), 1_000)
    if not isinstance(raw, str) or not raw or len(raw) > limit:
        return None
    try:
        value = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return dict(value) if isinstance(value, dict) else None


def _observation_is_verified(observation: Mapping[str, Any]) -> bool:
    fields = set(observation)
    if fields in (_INTERNAL_OBSERVATION_FIELDS, _WORKER_OBSERVATION_FIELDS):
        if not all(
            observation.get(name) is True
            for name in ("attempted", "executed", "observed", "verified")
        ):
            return False
        if observation.get("schema") != "evelyn.task-observation.v1":
            return False
        if (
            fields == _WORKER_OBSERVATION_FIELDS
            and (
                not isinstance(observation.get("successCriteria"), str)
                or len(observation["successCriteria"]) > 500
            )
        ):
            return False
    elif fields == _SERIALIZED_OBSERVATION_FIELDS:
        if observation.get("verified") is not True:
            return False
    else:
        return False
    return bool(
        type(observation.get("step")) is int
        and 1 <= observation["step"] <= 10
        and observation.get("outcome") == "success"
        and isinstance(observation.get("tool"), str)
        and isinstance(observation.get("code"), str)
        and isinstance(observation.get("summary"), str)
        and len(observation["summary"]) <= 240
        and isinstance(observation.get("evidence"), str)
    )


def _fragment(
    *,
    task_id: str,
    step_id: int,
    tool: str,
    index: int,
    source_label: str,
    content: str,
    verified_url: str = "",
) -> GroundedEvidenceFragment | None:
    label = _visible_text(source_label, maximum=160)
    body = _visible_text(content, maximum=MAX_GROUNDED_FRAGMENT_CHARS)
    if not label or not body or _URL_IN_TEXT_RE.search(label) is not None:
        return None
    digest = hashlib.sha256(
        json.dumps(
            {
                "taskId": task_id,
                "stepId": step_id,
                "tool": tool,
                "index": index,
                "sourceLabel": label,
                "contentSha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
                "verifiedUrl": verified_url,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return GroundedEvidenceFragment(
        task_id=task_id,
        step_id=step_id,
        tool=tool,
        evidence_ref=f"evref-{digest}",
        source_label=label,
        content=body,
        verified_url=verified_url,
    )


def _read_fragments(
    task_id: str,
    step: int,
    code: str,
    evidence: Mapping[str, Any],
) -> list[GroundedEvidenceFragment]:
    fields = {"path", "sha256", "bytes", "offset", "length", "nextOffset", "eof", "content", "truncated"}
    content = evidence.get("content")
    try:
        content_bytes = content.encode("utf-8") if isinstance(content, str) else b""
    except UnicodeEncodeError:
        return []
    if (
        code != "workspace_read_completed"
        or set(evidence) != fields
        or not _safe_workspace_path(evidence.get("path"))
        or _SHA256_RE.fullmatch(str(evidence.get("sha256") or "")) is None
        or type(evidence.get("bytes")) is not int
        or evidence["bytes"] < 0
        or type(evidence.get("offset")) is not int
        or evidence["offset"] < 0
        or type(evidence.get("length")) is not int
        or evidence["length"] < 0
        or type(evidence.get("nextOffset")) is not int
        or evidence["nextOffset"] != evidence["offset"] + evidence["length"]
        or evidence["nextOffset"] > evidence["bytes"]
        or type(evidence.get("eof")) is not bool
        or type(evidence.get("truncated")) is not bool
        or evidence["eof"] is not (evidence["nextOffset"] == evidence["bytes"])
        or evidence["truncated"] is not (not evidence["eof"])
        or len(content_bytes) != evidence["length"]
    ):
        return []
    item = _fragment(
        task_id=task_id,
        step_id=step,
        tool="workspace_read",
        index=0,
        source_label=(
            f"{_safe_workspace_path(evidence['path'])}:"
            f"{evidence['offset']}-{evidence['nextOffset']}"
        ),
        content=content[:MAX_GROUNDED_FRAGMENT_CHARS],
    )
    return [item] if item is not None else []


def _diff_fragments(
    task_id: str,
    step: int,
    code: str,
    evidence: Mapping[str, Any],
) -> list[GroundedEvidenceFragment]:
    paths = evidence.get("paths")
    diff = evidence.get("diff")
    try:
        diff_bytes = len(diff.encode("utf-8")) if isinstance(diff, str) else -1
    except UnicodeEncodeError:
        return []
    if (
        code != "workspace_diff_completed"
        or set(evidence) != {"diff", "stderr", "exitCode", "truncated", "paths"}
        or not isinstance(paths, list)
        or not 1 <= len(paths) <= 16
        or not all(isinstance(path, str) and _safe_workspace_path(path) for path in paths)
        or type(evidence.get("exitCode")) is not int
        or evidence["exitCode"] != 0
        or evidence.get("truncated") is not False
        or not isinstance(diff, str)
        or not isinstance(evidence.get("stderr"), str)
        or diff_bytes > 8 * 1024
    ):
        return []
    item = _fragment(
        task_id=task_id,
        step_id=step,
        tool="workspace_diff",
        index=0,
        source_label=", ".join(_safe_workspace_path(path) for path in paths)[:160],
        content=diff[:MAX_GROUNDED_FRAGMENT_CHARS],
    )
    return [item] if item is not None else []


def _workspace_search_fragments(
    task_id: str,
    step: int,
    code: str,
    evidence: Mapping[str, Any],
) -> list[GroundedEvidenceFragment]:
    path = evidence.get("path")
    query = evidence.get("query")
    matches = evidence.get("matches")
    if (
        code != "workspace_search_completed"
        or set(evidence) != {"path", "query", "matches", "truncated"}
        or not _safe_workspace_path(path)
        or not isinstance(query, str)
        or not query
        or len(query) > 256
        or evidence.get("truncated") is not False
        or not isinstance(matches, list)
        or len(matches) >= 32
    ):
        return []
    fragments: list[GroundedEvidenceFragment] = []
    for index, match in enumerate(matches):
        if (
            not isinstance(match, Mapping)
            or set(match) != {"path", "line", "text"}
            or not _path_within(match.get("path"), path)
            or type(match.get("line")) is not int
            or match["line"] < 1
            or not isinstance(match.get("text"), str)
            or query.casefold() not in match["text"].casefold()
        ):
            return []
        item = _fragment(
            task_id=task_id,
            step_id=step,
            tool="workspace_search",
            index=index,
            source_label=f"{_safe_workspace_path(match['path'])}:{match['line']}",
            content=match["text"][:MAX_GROUNDED_FRAGMENT_CHARS],
        )
        if item is not None:
            fragments.append(item)
    return fragments


def _web_search_fragments(
    task_id: str,
    step: int,
    code: str,
    evidence: Mapping[str, Any],
) -> list[GroundedEvidenceFragment]:
    query = evidence.get("query")
    results = evidence.get("results")
    if (
        code != "web_search_completed"
        or set(evidence) != {"query", "results"}
        or not isinstance(query, str)
        or not query.strip()
        or len(query) > 500
        or not isinstance(results, list)
        or not 1 <= len(results) <= 2
    ):
        return []
    fragments: list[GroundedEvidenceFragment] = []
    for index, result in enumerate(results):
        if not isinstance(result, Mapping) or set(result) != {"title", "snippet", "url"}:
            return []
        title = _visible_text(result.get("title"), maximum=160)
        snippet = _visible_text(result.get("snippet"), maximum=400)
        safe_url = safe_verified_https_url(result.get("url"))
        if not title or not snippet:
            return []
        host = urlsplit(safe_url).hostname if safe_url else ""
        source_label = (
            f"web:{str(host).replace('.', '[.]')}"
            if host
            else "verified public web result"
        )
        item = _fragment(
            task_id=task_id,
            step_id=step,
            tool="web_search",
            index=index,
            source_label=source_label,
            content=f"{title}\n{snippet}",
            verified_url=safe_url,
        )
        if item is not None:
            fragments.append(item)
    return fragments


_FRAGMENT_BUILDERS = {
    "workspace_read": _read_fragments,
    "workspace_diff": _diff_fragments,
    "workspace_search": _workspace_search_fragments,
    "web_search": _web_search_fragments,
}


def grounded_evidence_fragments(
    task_id: Any,
    observations: Iterable[Mapping[str, Any]],
) -> tuple[GroundedEvidenceFragment, ...]:
    if not isinstance(task_id, str):
        return ()
    normalized_task_id = task_id
    if _TASK_ID_RE.fullmatch(normalized_task_id) is None:
        return ()
    try:
        rows = list(observations)
    except TypeError:
        return ()
    if not 1 <= len(rows) <= 10 or any(not isinstance(row, Mapping) for row in rows):
        return ()
    step_ids = [row.get("step") for row in rows if isinstance(row, Mapping)]
    if any(type(step) is not int for step in step_ids) or len(step_ids) != len(set(step_ids)):
        return ()
    fragments: list[GroundedEvidenceFragment] = []
    context_bytes = 0
    for observation in sorted(rows, key=lambda item: int(item.get("step") or 0)):
        if not _observation_is_verified(observation):
            continue
        tool = str(observation["tool"])
        builder = _FRAGMENT_BUILDERS.get(tool)
        evidence = _observation_evidence(observation)
        if builder is None or evidence is None:
            continue
        for item in builder(
            normalized_task_id,
            int(observation["step"]),
            str(observation["code"]),
            evidence,
        ):
            item_bytes = len(item.content.encode("utf-8"))
            if (
                len(fragments) >= MAX_GROUNDED_FRAGMENTS
                or context_bytes + item_bytes > MAX_GROUNDED_CONTEXT_BYTES
            ):
                return tuple(fragments)
            fragments.append(item)
            context_bytes += item_bytes
    return tuple(fragments)


def grounded_evidence_manifest(
    *,
    task_id: str,
    kind: str,
    fragments: Iterable[GroundedEvidenceFragment],
) -> dict[str, Any]:
    rows = tuple(fragments)
    if (
        not isinstance(task_id, str)
        or _TASK_ID_RE.fullmatch(task_id) is None
        or kind not in GROUNDED_DRAFT_KINDS
        or not 1 <= len(rows) <= MAX_GROUNDED_FRAGMENTS
        or any(
            not isinstance(row, GroundedEvidenceFragment)
            or row.task_id != task_id
            for row in rows
        )
    ):
        raise GroundedDraftError("grounded_evidence_manifest_invalid")
    return {
        "schema": GROUNDED_EVIDENCE_MANIFEST_SCHEMA,
        "taskId": task_id,
        "kind": kind,
        "semanticVerified": False,
        "fragments": [row.worker_record() for row in rows],
    }


def validate_grounded_draft(
    value: Any,
    *,
    task_id: str,
    expected_kind: str,
    fragments: Iterable[GroundedEvidenceFragment],
) -> GroundedDraft:
    if not isinstance(value, Mapping) or set(value) != {
        "schema",
        "taskId",
        "kind",
        "sections",
        "semanticVerified",
        "humanReviewRequired",
    }:
        raise GroundedDraftError("grounded_draft_schema_invalid")
    if (
        not isinstance(task_id, str)
        or _TASK_ID_RE.fullmatch(task_id) is None
        or value.get("schema") != GROUNDED_DRAFT_SCHEMA
        or value.get("taskId") != task_id
        or value.get("kind") != expected_kind
        or expected_kind not in GROUNDED_DRAFT_KINDS
        or value.get("semanticVerified") is not False
        or value.get("humanReviewRequired") is not True
    ):
        raise GroundedDraftError("grounded_draft_binding_invalid")
    fragment_rows = tuple(fragments)
    if not 1 <= len(fragment_rows) <= MAX_GROUNDED_FRAGMENTS or any(
        not isinstance(fragment, GroundedEvidenceFragment)
        for fragment in fragment_rows
    ):
        raise GroundedDraftError("grounded_draft_evidence_invalid")
    registry = {
        (fragment.step_id, fragment.evidence_ref): fragment
        for fragment in fragment_rows
        if fragment.task_id == task_id
    }
    raw_sections = value.get("sections")
    if not registry or not isinstance(raw_sections, list) or not 1 <= len(raw_sections) <= MAX_GROUNDED_SECTIONS:
        raise GroundedDraftError("grounded_draft_sections_invalid")
    sections: list[GroundedSection] = []
    claim_count = 0
    for raw_section in raw_sections:
        if not isinstance(raw_section, Mapping) or set(raw_section) != {"title", "claims"}:
            raise GroundedDraftError("grounded_draft_section_schema_invalid")
        title = _visible_text(raw_section.get("title"), maximum=MAX_GROUNDED_TITLE_CHARS)
        raw_claims = raw_section.get("claims")
        if (
            not title
            or _URL_IN_TEXT_RE.search(title) is not None
            or not isinstance(raw_claims, list)
            or not 1 <= len(raw_claims) <= MAX_GROUNDED_CLAIMS_PER_SECTION
        ):
            raise GroundedDraftError("grounded_draft_section_invalid")
        claims: list[GroundedClaim] = []
        for raw_claim in raw_claims:
            if not isinstance(raw_claim, Mapping) or set(raw_claim) != {"text", "stepId", "evidenceRef"}:
                raise GroundedDraftError("grounded_draft_claim_schema_invalid")
            text = _visible_text(raw_claim.get("text"), maximum=MAX_GROUNDED_CLAIM_CHARS)
            step_id = raw_claim.get("stepId")
            evidence_ref = raw_claim.get("evidenceRef")
            if (
                not text
                or _URL_IN_TEXT_RE.search(text) is not None
                or type(step_id) is not int
                or step_id <= 0
                or not isinstance(evidence_ref, str)
                or _EVIDENCE_REF_RE.fullmatch(evidence_ref) is None
                or (step_id, evidence_ref) not in registry
            ):
                raise GroundedDraftError("grounded_draft_reference_invalid")
            claims.append(GroundedClaim(text, step_id, evidence_ref))
            claim_count += 1
        sections.append(GroundedSection(title, tuple(claims)))
    if not 1 <= claim_count <= MAX_GROUNDED_CLAIMS:
        raise GroundedDraftError("grounded_draft_claim_count_invalid")
    return GroundedDraft(task_id, expected_kind, tuple(sections))


def grounded_draft_from_task_payload(
    payload: Any,
    *,
    goal: str,
) -> tuple[GroundedDraft, tuple[GroundedEvidenceFragment, ...]]:
    if not isinstance(payload, Mapping):
        raise GroundedDraftError("grounded_task_payload_invalid")
    task_id = payload.get("taskId")
    kind = grounded_draft_kind(goal)
    observations = payload.get("observations")
    step_count = payload.get("stepCount")
    model_call_count = payload.get("modelCallCount")
    if (
        set(payload) != _GROUNDED_TASK_PAYLOAD_FIELDS
        or not isinstance(task_id, str)
        or _TASK_ID_RE.fullmatch(task_id) is None
        or payload.get("schema") != "evelyn.task-loop.v1"
        or payload.get("status") != GROUNDED_DRAFT_STATUS
        or payload.get("code") != GROUNDED_DRAFT_CODE
        or kind is None
        or not isinstance(observations, list)
        or not 1 <= len(observations) <= 10
        or type(step_count) is not int
        or not 1 <= step_count <= 10
        or type(model_call_count) is not int
        or not 1 <= model_call_count <= step_count + 1
        or payload.get("approvalTool") != ""
        or any(
            type(observation.get("step")) is not int
            or not 1 <= observation["step"] <= step_count
            for observation in observations
            if isinstance(observation, Mapping)
        )
    ):
        raise GroundedDraftError("grounded_task_payload_invalid")
    fragments = grounded_evidence_fragments(task_id, observations)
    draft = validate_grounded_draft(
        payload.get("groundedDraft"),
        task_id=task_id,
        expected_kind=kind,
        fragments=fragments,
    )
    return draft, fragments


def render_grounded_draft(
    draft: GroundedDraft,
    fragments: Iterable[GroundedEvidenceFragment],
    *,
    include_links: bool,
) -> str:
    registry = {
        (fragment.step_id, fragment.evidence_ref): fragment
        for fragment in fragments
        if fragment.task_id == draft.task_id
    }
    lines = ["근거가 연결된 검토용 초안이야. 의미상 정확성은 아직 사람의 검토가 필요해."]
    for section in draft.sections:
        lines.extend(("", _markdown_text(section.title)))
        for claim in section.claims:
            fragment = registry.get((claim.step_id, claim.evidence_ref))
            if fragment is None:
                raise GroundedDraftError("grounded_draft_reference_stale")
            source = f"근거: {_markdown_text(fragment.source_label)}"
            if include_links and fragment.verified_url:
                source = f"{source} — {fragment.verified_url}"
            lines.append(f"- {_markdown_text(claim.text)} ({source})")
    lines.extend(("", "구조와 근거 연결만 확인됐으며, 의미 검증 완료나 작업 성공을 뜻하지 않아."))
    return "\n".join(lines)


__all__ = [
    "GROUNDED_DRAFT_CODE",
    "GROUNDED_DRAFT_KINDS",
    "GROUNDED_DRAFT_SCHEMA",
    "GROUNDED_DRAFT_STATUS",
    "GROUNDED_DRAFT_TTS_TEXT",
    "GROUNDED_EVIDENCE_MANIFEST_SCHEMA",
    "GroundedClaim",
    "GroundedDraft",
    "GroundedDraftError",
    "GroundedEvidenceFragment",
    "GroundedSection",
    "explicit_link_requested",
    "grounded_draft_from_task_payload",
    "grounded_draft_kind",
    "grounded_evidence_fragments",
    "grounded_evidence_manifest",
    "render_grounded_draft",
    "safe_verified_https_url",
    "validate_grounded_draft",
]
