from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path


CONTAINER_RUNTIME_ROLE = "discord_bot"


def runtime_uses_container_restart() -> bool:
    return (
        str(os.environ.get("EVELYN_RUNTIME_ROLE") or "").strip()
        == CONTAINER_RUNTIME_ROLE
    )


def runtime_prefers_local_restart(*, local_only_mode: bool, discord_enabled: bool) -> bool:
    return bool(local_only_mode or not discord_enabled)


def resolve_restart_launcher(
    project_dir: Path,
    *,
    local_restart: bool,
    control_page_port: int,
) -> tuple[Path, dict[str, str], str]:
    env_overrides = {"STT_USE_RAW_48K": "false"}
    if local_restart:
        env_overrides.update(
            {
                "DISCORD_ENABLED": "false",
                "LOCAL_ONLY": "true",
                "LOCAL_MIC_ENABLED": os.getenv("LOCAL_MIC_ENABLED", "false"),
                "CONTROL_PAGE_PORT": str(control_page_port),
            }
        )
        return project_dir / "evelyn_core" / "start_local.bat", env_overrides, "local"

    env_overrides.update({"DISCORD_ENABLED": "true", "LOCAL_ONLY": "false"})
    return project_dir / "evelyn_core" / "start_bot.bat", env_overrides, "discord"


def launch_restart_process(
    restart_bat: Path,
    project_dir: Path,
    env_overrides: dict[str, str],
    *,
    fallback_target: Path,
) -> None:
    env = os.environ.copy()
    env.update(env_overrides)
    command: list[str]
    if restart_bat.is_file():
        command = ["cmd.exe", "/c", str(restart_bat)]
    elif not fallback_target.is_file():
        raise FileNotFoundError("Restart fallback target is missing")
    elif fallback_target.suffix.lower() in {".bat", ".cmd"}:
        command = ["cmd.exe", "/c", str(fallback_target)]
    elif fallback_target.suffix.lower() == ".py":
        command = [sys.executable, str(fallback_target)]
    else:
        raise ValueError("Unsupported restart fallback target type")
    subprocess.Popen(
        command,
        cwd=str(project_dir),
        env=env,
        close_fds=True,
        creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
    )


def schedule_runtime_shutdown_script(
    project_root: Path,
    *,
    target: str,
    delay_ms: int = 3000,
) -> bool:
    script_key = "stop_evelyn_stack.ps1" if target == "stack" else "stop_evelyn_local.ps1"
    stop_script = project_root / "evelyn_core" / "runtime" / "launchers" / script_key
    if not stop_script.exists():
        logging.error("Runtime shutdown helper is missing: %s", stop_script)
        return False
    try:
        subprocess.Popen(
            [
                "powershell.exe",
                "-NoLogo",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(stop_script),
                "-DelayMs",
                str(max(0, int(delay_ms))),
            ],
            cwd=str(project_root),
            close_fds=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except Exception:
        logging.exception("Failed to schedule runtime shutdown helper")
        return False


def launch_runtime_restart_sequence(
    project_root: Path,
    *,
    local_only_mode: bool,
    discord_enabled: bool,
    control_page_port: int,
    fallback_target: Path,
) -> str:
    if runtime_uses_container_restart():
        print("[RESTART] mode=container launcher=docker_restart_policy")
        return "container"
    restart_bat, env_overrides, restart_mode = resolve_restart_launcher(
        project_root,
        local_restart=runtime_prefers_local_restart(
            local_only_mode=local_only_mode,
            discord_enabled=discord_enabled,
        ),
        control_page_port=control_page_port,
    )
    print(f"[RESTART] mode={restart_mode} launcher={restart_bat}")
    launch_restart_process(
        restart_bat,
        project_root,
        env_overrides,
        fallback_target=fallback_target,
    )
    return restart_mode


def schedule_evelyn_stack_shutdown(project_root: Path, *, delay_ms: int = 3000) -> bool:
    return schedule_runtime_shutdown_script(project_root, target="stack", delay_ms=delay_ms)


def schedule_evelyn_local_shutdown(project_root: Path, *, delay_ms: int = 1500) -> bool:
    return schedule_runtime_shutdown_script(project_root, target="local", delay_ms=delay_ms)


__all__ = [
    "CONTAINER_RUNTIME_ROLE",
    "runtime_uses_container_restart",
    "runtime_prefers_local_restart",
    "resolve_restart_launcher",
    "launch_restart_process",
    "schedule_runtime_shutdown_script",
    "launch_runtime_restart_sequence",
    "schedule_evelyn_stack_shutdown",
    "schedule_evelyn_local_shutdown",
]
