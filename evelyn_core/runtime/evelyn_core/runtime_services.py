from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal


RUNTIME_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST_PATH = RUNTIME_ROOT / "service_manifest.json"

ProbeKind = Literal["tcp", "http", "artifact_json"]


@dataclass(frozen=True)
class HealthProbeSpec:
    kind: ProbeKind
    host: str
    port: int
    timeout_ms: int
    path: str = ""
    method: str = "GET"
    expect_status: int | None = None
    expect_json: dict[str, Any] | None = None
    stale_after_sec: float | None = None


@dataclass(frozen=True)
class RepairSpec:
    allowed: bool
    strategy: str = "none"
    requires_confirm: bool = True
    cooldown_sec: int = 60


@dataclass(frozen=True)
class ServiceSpec:
    id: str
    label: str
    kind: str
    required: bool
    host: str
    port: int
    checks: tuple[HealthProbeSpec, ...]
    launcher: str | None = None
    repair: RepairSpec | None = None
    aliases: tuple[str, ...] = ()
    default_host: str | None = None
    host_env: str | None = None
    default_port: int | None = None
    port_env: str | None = None


@dataclass(frozen=True)
class ServiceManifest:
    schema_version: str
    runtime_name: str
    services: tuple[ServiceSpec, ...]
    path: Path
    loaded_at: float


@dataclass(frozen=True)
class ManifestIssue:
    code: str
    message: str
    service_id: str | None = None


_MANIFEST_CACHE: ServiceManifest | None = None
_MANIFEST_CACHE_MTIME: float | None = None


def _effective_port(raw: dict[str, Any]) -> tuple[int, int, str | None]:
    default_port = int(raw.get("port") or 0)
    env_name = (raw.get("env") or {}).get("port") if isinstance(raw.get("env"), dict) else None
    if env_name and os.getenv(str(env_name)):
        try:
            return int(str(os.getenv(str(env_name)))), default_port, str(env_name)
        except Exception:
            return default_port, default_port, str(env_name)
    return default_port, default_port, str(env_name) if env_name else None


def _effective_host(raw: dict[str, Any]) -> tuple[str, str, str | None]:
    default_host = str(raw.get("host") or "127.0.0.1")
    env_name = (raw.get("env") or {}).get("host") if isinstance(raw.get("env"), dict) else None
    if env_name and os.getenv(str(env_name)):
        return str(os.getenv(str(env_name))), default_host, str(env_name)
    return default_host, default_host, str(env_name) if env_name else None


def _parse_probe(raw: dict[str, Any], *, service_host: str, service_port: int) -> HealthProbeSpec:
    kind = str(raw.get("kind") or "").lower()
    if kind not in {"tcp", "http", "artifact_json"}:
        raise ValueError(f"unknown probe kind: {kind}")
    return HealthProbeSpec(
        kind=kind,  # type: ignore[arg-type]
        host=str(raw.get("host") or service_host),
        port=int(raw.get("port") or service_port),
        timeout_ms=int(raw.get("timeout_ms") or 500),
        path=str(raw.get("path") or ""),
        method=str(raw.get("method") or "GET").upper(),
        expect_status=int(raw["expect_status"]) if raw.get("expect_status") is not None else None,
        expect_json=dict(raw.get("expect_json") or {}) if isinstance(raw.get("expect_json"), dict) else None,
        stale_after_sec=float(raw["stale_after_sec"]) if raw.get("stale_after_sec") is not None else None,
    )


def _parse_service(raw: dict[str, Any]) -> ServiceSpec:
    service_id = str(raw.get("id") or "").strip()
    if not service_id:
        raise ValueError("service id is required")
    port, default_port, port_env = _effective_port(raw)
    host, default_host, host_env = _effective_host(raw)
    checks = tuple(_parse_probe(dict(item), service_host=host, service_port=port) for item in (raw.get("checks") or []))
    repair_raw = raw.get("repair") if isinstance(raw.get("repair"), dict) else {}
    return ServiceSpec(
        id=service_id,
        label=str(raw.get("label") or raw.get("display_name") or service_id),
        kind=str(raw.get("kind") or "process"),
        required=bool(raw.get("required")),
        host=host,
        port=port,
        checks=checks,
        launcher=str(raw.get("launcher")) if raw.get("launcher") else None,
        repair=RepairSpec(
            allowed=bool(repair_raw.get("allowed")),
            strategy=str(repair_raw.get("strategy") or "none"),
            requires_confirm=bool(repair_raw.get("requires_confirm", True)),
            cooldown_sec=int(repair_raw.get("cooldown_sec") or 60),
        ),
        aliases=tuple(str(item) for item in (raw.get("aliases") or [])),
        default_host=default_host,
        host_env=host_env,
        default_port=default_port,
        port_env=port_env,
    )


def load_service_manifest(*, path: Path | None = None, force: bool = False) -> ServiceManifest:
    global _MANIFEST_CACHE
    global _MANIFEST_CACHE_MTIME

    manifest_path = path or DEFAULT_MANIFEST_PATH
    mtime = manifest_path.stat().st_mtime
    if (
        not force
        and _MANIFEST_CACHE is not None
        and _MANIFEST_CACHE.path == manifest_path
        and _MANIFEST_CACHE_MTIME == mtime
    ):
        return _MANIFEST_CACHE

    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    services = tuple(_parse_service(dict(item)) for item in raw.get("services") or [])
    manifest = ServiceManifest(
        schema_version=str(raw.get("schema_version") or "1.0"),
        runtime_name=str(raw.get("runtime_name") or "evelyn-local"),
        services=services,
        path=manifest_path,
        loaded_at=time.time(),
    )
    issues = validate_service_manifest(manifest)
    errors = [issue for issue in issues if issue.code.startswith("error.")]
    if errors:
        raise ValueError("; ".join(issue.message for issue in errors))
    _MANIFEST_CACHE = manifest
    _MANIFEST_CACHE_MTIME = mtime
    return manifest


def validate_service_manifest(manifest: ServiceManifest) -> list[ManifestIssue]:
    issues: list[ManifestIssue] = []
    ids: set[str] = set()
    required_ports: dict[tuple[str, int], str] = {}
    for service in manifest.services:
        if service.id in ids:
            issues.append(ManifestIssue("error.duplicate_service_id", f"duplicate service id: {service.id}", service.id))
        ids.add(service.id)
        if not service.checks:
            issues.append(ManifestIssue("error.no_checks", f"service has no health checks: {service.id}", service.id))
        if service.required and service.port > 0:
            key = (service.host, service.port)
            other = required_ports.get(key)
            if other:
                issues.append(
                    ManifestIssue(
                        "error.required_port_collision",
                        f"required services share {service.host}:{service.port}: {other}, {service.id}",
                        service.id,
                    )
                )
            required_ports[key] = service.id
        if (
            service.repair
            and service.repair.allowed
            and not service.launcher
            and service.repair.strategy != "host_supervisor"
        ):
            issues.append(ManifestIssue("error.repair_without_launcher", f"repair allowed without launcher: {service.id}", service.id))

    ports = {service.id: service.port for service in manifest.services}
    if ports.get("control_page") == ports.get("bot_api"):
        issues.append(ManifestIssue("error.control_page_bot_api_port_collision", "control_page and bot_api must not share a port"))
    return issues


def get_service(manifest: ServiceManifest, service_id: str) -> ServiceSpec | None:
    return next((service for service in manifest.services if service.id == service_id), None)


def service_port_map(manifest: ServiceManifest) -> dict[str, int]:
    return {service.id: service.port for service in manifest.services}


def manifest_to_dict(manifest: ServiceManifest) -> dict[str, Any]:
    return {
        "schemaVersion": manifest.schema_version,
        "runtimeName": manifest.runtime_name,
        "path": str(manifest.path),
        "loadedAt": manifest.loaded_at,
        "services": [
            {
                "id": service.id,
                "label": service.label,
                "kind": service.kind,
                "required": service.required,
                "host": service.host,
                "port": service.port,
                "defaultHost": service.default_host,
                "hostEnv": service.host_env,
                "defaultPort": service.default_port,
                "portEnv": service.port_env,
                "launcher": service.launcher,
                "repair": {
                    "allowed": bool(service.repair and service.repair.allowed),
                    "strategy": service.repair.strategy if service.repair else "none",
                    "requiresConfirm": service.repair.requires_confirm if service.repair else True,
                    "cooldownSec": service.repair.cooldown_sec if service.repair else 60,
                },
                "checks": [
                    {
                        "kind": check.kind,
                        "host": check.host,
                        "port": check.port,
                        "path": check.path,
                        "method": check.method,
                        "timeoutMs": check.timeout_ms,
                        "expectStatus": check.expect_status,
                        "expectJson": check.expect_json,
                        "staleAfterSec": check.stale_after_sec,
                    }
                    for check in service.checks
                ],
            }
            for service in manifest.services
        ],
    }
