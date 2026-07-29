from __future__ import annotations

import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urlparse


RUNTIME_CONFIG_SCHEMA = "runtime_config.owner.v1"
_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off"})


@dataclass(frozen=True)
class SettingSpec:
    name: str
    kind: str = "text"
    default: Any = ""
    minimum: float | None = None
    maximum: float | None = None
    choices: tuple[str, ...] = ()
    secret: bool = False
    allow_empty: bool = False
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class RuntimeSettings:
    owner: str
    values: Mapping[str, Any]
    overrides: tuple[str, ...]
    warnings: tuple[dict[str, str], ...]
    secret_fields: tuple[str, ...]

    def __getitem__(self, name: str) -> Any:
        return self.values[name]

    def get(self, name: str, default: Any = None) -> Any:
        return self.values.get(name, default)

    def public_summary(self) -> dict[str, Any]:
        return {
            "schema": RUNTIME_CONFIG_SCHEMA,
            "owner": self.owner,
            "fieldCount": len(self.values),
            "overrideCount": len(self.overrides),
            "overrides": list(self.overrides),
            "warnings": [dict(item) for item in self.warnings],
            "secrets": {
                name: bool(name in self.overrides)
                for name in self.secret_fields
            },
        }


def _parse_bool(raw: str) -> bool:
    normalized = raw.strip().lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    raise ValueError("invalid_boolean")


def _bounded_number(value: int | float, spec: SettingSpec) -> int | float:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("not_finite")
    if spec.minimum is not None and value < spec.minimum:
        raise ValueError("below_minimum")
    if spec.maximum is not None and value > spec.maximum:
        raise ValueError("above_maximum")
    return value


def _parse_value(raw: str, spec: SettingSpec) -> Any:
    value = raw if spec.allow_empty else raw.strip()
    if not value and not spec.allow_empty:
        return spec.default
    if spec.kind == "text":
        parsed: Any = value
    elif spec.kind == "bool":
        parsed = _parse_bool(value)
    elif spec.kind == "int":
        parsed = _bounded_number(int(value), spec)
    elif spec.kind == "float":
        parsed = _bounded_number(float(value), spec)
    elif spec.kind == "path":
        parsed = Path(value).expanduser()
    elif spec.kind == "url":
        candidate = urlparse(value)
        if candidate.scheme not in {"http", "https"} or not candidate.netloc:
            raise ValueError("invalid_url")
        parsed = value.rstrip("/")
    else:
        raise ValueError("unsupported_kind")
    if spec.choices:
        normalized_choices = {choice.casefold(): choice for choice in spec.choices}
        selected = normalized_choices.get(str(parsed).casefold())
        if selected is None:
            raise ValueError("invalid_choice")
        parsed = selected
    return parsed


def load_runtime_settings(
    owner: str,
    specs: Sequence[SettingSpec],
    *,
    environ: Mapping[str, str] | None = None,
) -> RuntimeSettings:
    source = os.environ if environ is None else environ
    values: dict[str, Any] = {}
    overrides: list[str] = []
    warnings: list[dict[str, str]] = []
    secret_fields: list[str] = []
    seen: set[str] = set()
    for spec in specs:
        if spec.name in seen:
            raise ValueError(f"duplicate setting spec: {spec.name}")
        seen.add(spec.name)
        if spec.secret:
            secret_fields.append(spec.name)
        selected_name = ""
        raw_value: str | None = None
        for candidate in (spec.name, *spec.aliases):
            if candidate in source:
                selected_name = candidate
                raw_value = str(source[candidate])
                break
        if raw_value is None:
            values[spec.name] = spec.default
            continue
        overrides.append(spec.name)
        try:
            values[spec.name] = _parse_value(raw_value, spec)
        except (TypeError, ValueError):
            values[spec.name] = spec.default
            warnings.append(
                {
                    "field": spec.name,
                    "code": "invalid_value_defaulted",
                }
            )
        if selected_name and selected_name != spec.name:
            warnings.append(
                {
                    "field": spec.name,
                    "code": "deprecated_alias",
                }
            )
    return RuntimeSettings(
        owner=str(owner),
        values=values,
        overrides=tuple(sorted(set(overrides))),
        warnings=tuple(warnings),
        secret_fields=tuple(sorted(secret_fields)),
    )


STT_SERVICE_SETTINGS = (
    SettingSpec("STT_MODEL_NAME", default="Qwen/Qwen3-ASR-1.7B"),
    SettingSpec("STT_LANGUAGE", default="ko"),
    SettingSpec("STT_FORCE_LANGUAGE", kind="bool", default=True),
    SettingSpec(
        "STT_COMPUTE_TYPE",
        default="float16",
        choices=("float16", "bfloat16", "float32"),
    ),
    SettingSpec("STT_HOST", default="127.0.0.1"),
    SettingSpec("STT_PORT", kind="int", default=8892, minimum=1, maximum=65535),
    SettingSpec("STT_LOAD_ON_START", kind="bool", default=True),
    SettingSpec(
        "STT_MAX_AUDIO_SEC",
        kind="float",
        default=30.0,
        minimum=1.0,
        maximum=300.0,
    ),
    SettingSpec("HF_TOKEN", default="", secret=True, allow_empty=True),
)


VISION_SERVICE_SETTINGS = (
    SettingSpec(
        "VISION_SMOL_MODEL",
        default="HuggingFaceTB/SmolVLM2-500M-Video-Instruct",
    ),
    SettingSpec("VISION_OCR_MODEL", default="tiiuae/Falcon-OCR"),
    SettingSpec("VISION_DEVICE", default="auto"),
    SettingSpec(
        "VISION_DTYPE",
        default="float16",
        choices=("float16", "bfloat16", "float32"),
    ),
    SettingSpec(
        "VISION_OCR_DTYPE",
        default="auto",
        choices=("auto", "float16", "bfloat16", "float32"),
    ),
    SettingSpec(
        "VISION_MAX_NEW_TOKENS",
        kind="int",
        default=96,
        minimum=1,
        maximum=4096,
    ),
    SettingSpec("VISION_TRUST_REMOTE_CODE", kind="bool", default=False),
    SettingSpec("VISION_LOAD_SMOL", kind="bool", default=True),
    SettingSpec("VISION_LOAD_OCR", kind="bool", default=True),
    SettingSpec("VISION_OCR_LAZY_LOAD", kind="bool", default=False),
    SettingSpec(
        "VISION_OCR_IDLE_UNLOAD_SEC",
        kind="float",
        default=600.0,
        minimum=0.0,
        maximum=86400.0,
    ),
    SettingSpec("VISION_OCR_UNLOAD_AFTER_REQUEST", kind="bool", default=False),
    SettingSpec("VISION_OCR_EMPTY_CACHE_ON_UNLOAD", kind="bool", default=True),
    SettingSpec("VISION_OCR_COMPILE", kind="bool", default=False),
    SettingSpec("EVELYN_HOST_PROJECT_ROOT", kind="path", default=None),
    SettingSpec("EVELYN_CONTAINER_PROJECT_ROOT", kind="path", default=None),
    SettingSpec("VISION_HOST", default="127.0.0.1"),
    SettingSpec("VISION_PORT", kind="int", default=8891, minimum=1, maximum=65535),
)


CODEX_GATEWAY_SETTINGS = (
    SettingSpec("VOYAGER_CODEX_GATEWAY_HOST", default="127.0.0.1"),
    SettingSpec(
        "VOYAGER_CODEX_GATEWAY_PORT",
        kind="int",
        default=8787,
        minimum=1,
        maximum=65535,
    ),
    SettingSpec("VOYAGER_CODEX_MODEL", default="gpt-5.5"),
    SettingSpec(
        "VOYAGER_CODEX_GATEWAY_TIMEOUT_SEC",
        kind="float",
        default=260.0,
        minimum=1.0,
        maximum=1800.0,
    ),
    SettingSpec(
        "VOYAGER_CODEX_GATEWAY_BACKEND",
        default="codex-exec",
        choices=("codex-exec",),
    ),
    SettingSpec("VOYAGER_CODEX_GATEWAY_WORKDIR", kind="path", default=None),
    SettingSpec("VOYAGER_CODEX_CLI", kind="path", default=None),
    SettingSpec(
        "VOYAGER_CODEX_GATEWAY_COMMAND",
        default="",
        secret=True,
        allow_empty=True,
    ),
    SettingSpec(
        "EVELYN_ALLOW_CUSTOM_GATEWAY_COMMAND",
        kind="bool",
        default=False,
    ),
    SettingSpec("EVELYN_CODEX_CREDENTIALS_DIR", kind="path", default=None),
    SettingSpec("CODEX_HOME", kind="path", default=None),
)


MINDCRAFT_SERVICE_SETTINGS = (
    SettingSpec("MINDCRAFT_STATUS_PATH", kind="path", default=None),
    SettingSpec("MINDCRAFT_ROOT", kind="path", default=Path("/app/mindcraft")),
    SettingSpec("MINDCRAFT_AGENT_PROFILE", kind="path", default=None),
    SettingSpec("MINDCRAFT_ALLOWED_PLAYERS", default="", allow_empty=True),
    SettingSpec("MINDCRAFT_AUTO_RESTART", kind="bool", default=True),
    SettingSpec(
        "MINDCRAFT_AUTO_RESTART_COOLDOWN_SEC",
        kind="float",
        default=5.0,
        minimum=1.0,
        maximum=300.0,
    ),
    SettingSpec("MINECRAFT_VERSION", default="1.21.11"),
    SettingSpec("MINEFLAYER_HOST", default="host.docker.internal"),
    SettingSpec("MINEFLAYER_PORT", kind="int", default=25565, minimum=1, maximum=65535),
    SettingSpec(
        "MINEFLAYER_AUTH",
        default="microsoft",
        choices=("microsoft", "offline"),
    ),
    SettingSpec("MINDSERVER_PORT", kind="int", default=8080, minimum=1, maximum=65535),
    SettingSpec("MINEFLAYER_USERNAME", default="Evelyn_0428"),
    SettingSpec(
        "MINEFLAYER_PROFILES_FOLDER",
        kind="path",
        default=Path("/app/bot_profiles"),
    ),
    SettingSpec("MINDCRAFT_LOCAL_MODEL", default="Qwen3-14B-Q4_K_M.gguf"),
    SettingSpec("MINDCRAFT_ROUTER_MODEL", default="gemma-4-E2B-it-Q4_K_M.gguf"),
    SettingSpec("MINDCRAFT_CODEX_MODEL", default="gpt-5.5"),
    SettingSpec(
        "MINDCRAFT_CODEX_GATEWAY_URL",
        kind="url",
        default="http://codex_gateway:8787/codex/action",
    ),
)


__all__ = [
    "CODEX_GATEWAY_SETTINGS",
    "MINDCRAFT_SERVICE_SETTINGS",
    "RUNTIME_CONFIG_SCHEMA",
    "RuntimeSettings",
    "STT_SERVICE_SETTINGS",
    "SettingSpec",
    "VISION_SERVICE_SETTINGS",
    "load_runtime_settings",
]
