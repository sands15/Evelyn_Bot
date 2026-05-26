from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ControlPageWindowSpec:
    key: str
    title: str
    port: int
    aliases: tuple[str, ...]


CONTROL_PAGE_WINDOW_SPECS: tuple[ControlPageWindowSpec, ...] = (
    ControlPageWindowSpec(
        key="main-llm",
        title="Main-LLM",
        port=9820,
        aliases=("main-llm", "main_llm", "main"),
    ),
    ControlPageWindowSpec(
        key="router-llm",
        title="Router-LLM",
        port=9822,
        aliases=("router-llm", "router_llm", "router"),
    ),
    ControlPageWindowSpec(
        key="sub-llm",
        title="Sub-LLM",
        port=9821,
        aliases=("sub-llm", "sub_llm", "sub"),
    ),
    ControlPageWindowSpec(
        key="tts",
        title="TTS",
        port=8880,
        aliases=("tts", "voice"),
    ),
    ControlPageWindowSpec(
        key="control-page",
        title="Control-Page",
        port=8799,
        aliases=("control-page", "control_page", "page", "docs"),
    ),
    ControlPageWindowSpec(
        key="bot",
        title="Bot",
        port=8798,
        aliases=("bot", "evelyn"),
    ),
)

CONTROL_PAGE_WINDOW_ALIAS_MAP: dict[str, str] = {}
for _spec in CONTROL_PAGE_WINDOW_SPECS:
    CONTROL_PAGE_WINDOW_ALIAS_MAP[_spec.key] = _spec.key
    for _alias in _spec.aliases:
        CONTROL_PAGE_WINDOW_ALIAS_MAP[_alias] = _spec.key


def resolve_control_page_window_key(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip().lower().replace(" ", "-")
    if not normalized:
        return None
    return CONTROL_PAGE_WINDOW_ALIAS_MAP.get(normalized)


def control_page_window_choices_text() -> str:
    return ", ".join(spec.key for spec in CONTROL_PAGE_WINDOW_SPECS)
