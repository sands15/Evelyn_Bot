from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .text import clean_text


@dataclass(frozen=True)
class EvelynPageUrlRuntimeDeps:
    project_root: Path
    configured_page_url: str | None
    run_git_config: Callable[..., Any]


def build_evelyn_page_url_runtime_deps(
    *,
    project_root: Path,
    configured_page_url: str | None,
    run_git_config: Callable[..., Any],
) -> EvelynPageUrlRuntimeDeps:
    return EvelynPageUrlRuntimeDeps(
        project_root=project_root,
        configured_page_url=configured_page_url,
        run_git_config=run_git_config,
    )


def derive_github_pages_url_from_remote(origin_url: str | None) -> str | None:
    source = clean_text(origin_url)
    if not source:
        return None
    patterns = (
        r"^git@github\.com:(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?$",
        r"^https?://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$",
    )
    for pattern in patterns:
        match = re.match(pattern, source)
        if not match:
            continue
        owner = clean_text(match.group("owner"))
        repo = clean_text(match.group("repo"))
        if not owner or not repo:
            return None
        return f"https://{owner}.github.io/{repo}/"
    return None


def resolve_public_page_url(*, configured_url: str | None, remote_origin_url: str | None) -> str | None:
    configured = clean_text(configured_url)
    if configured:
        return configured
    return derive_github_pages_url_from_remote(remote_origin_url)


def _subprocess_cwd_from_project_root(project_root: Path) -> str:
    root_text = str(project_root)
    if re.match(r"^[A-Za-z]:/", root_text):
        return root_text.replace("/", "\\")
    return root_text


def resolve_evelyn_page_url_from_runtime(*, deps: EvelynPageUrlRuntimeDeps) -> str | None:
    try:
        completed = deps.run_git_config(
            ["git", "config", "--get", "remote.origin.url"],
            cwd=_subprocess_cwd_from_project_root(deps.project_root),
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except Exception:
        return None
    return resolve_public_page_url(
        configured_url=deps.configured_page_url,
        remote_origin_url=completed.stdout,
    )
