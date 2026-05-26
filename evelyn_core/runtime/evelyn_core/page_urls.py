from __future__ import annotations

import re

from .text import clean_text


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
