from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.page_urls import (  # noqa: E402
    EvelynPageUrlRuntimeDeps,
    derive_github_pages_url_from_remote,
    build_evelyn_page_url_runtime_deps,
    resolve_evelyn_page_url_from_runtime,
    resolve_public_page_url,
)


class PageUrlTests(unittest.TestCase):
    def test_derive_from_ssh_remote(self) -> None:
        self.assertEqual(
            derive_github_pages_url_from_remote("git@github.com:sands15/Evelyn_Bot.git"),
            "https://sands15.github.io/Evelyn_Bot/",
        )

    def test_derive_from_https_remote(self) -> None:
        self.assertEqual(
            derive_github_pages_url_from_remote("https://github.com/sands15/Evelyn_Bot.git"),
            "https://sands15.github.io/Evelyn_Bot/",
        )

    def test_configured_url_wins(self) -> None:
        self.assertEqual(
            resolve_public_page_url(
                configured_url="https://custom.example/evelyn/",
                remote_origin_url="git@github.com:sands15/Evelyn_Bot.git",
            ),
            "https://custom.example/evelyn/",
        )

    def test_invalid_remote_returns_none(self) -> None:
        self.assertIsNone(derive_github_pages_url_from_remote("git@gitlab.com:team/project.git"))

    def test_build_evelyn_page_url_runtime_deps(self) -> None:
        def run_git_config(*_args, **_kwargs):
            return None

        deps = build_evelyn_page_url_runtime_deps(
            project_root=Path("C:/Evelyn"),
            configured_page_url="https://custom.example/evelyn/",
            run_git_config=run_git_config,
        )
        self.assertEqual(deps.project_root, Path("C:/Evelyn"))
        self.assertEqual(deps.configured_page_url, "https://custom.example/evelyn/")
        self.assertIs(deps.run_git_config, run_git_config)

    def test_resolve_evelyn_page_url_uses_configured_url_without_requiring_valid_remote(self) -> None:
        calls: list[dict] = []

        def run_git_config(*args, **kwargs):
            calls.append({"args": args, "kwargs": kwargs})
            return SimpleNamespace(stdout="not-a-github-remote")

        self.assertEqual(
            resolve_evelyn_page_url_from_runtime(
                deps=EvelynPageUrlRuntimeDeps(
                    project_root=Path("C:/Evelyn"),
                    configured_page_url="https://custom.example/evelyn/",
                    run_git_config=run_git_config,
                )
            ),
            "https://custom.example/evelyn/",
        )
        self.assertEqual(calls[0]["args"][0], ["git", "config", "--get", "remote.origin.url"])
        self.assertEqual(calls[0]["kwargs"]["cwd"], "C:\\Evelyn")

    def test_resolve_evelyn_page_url_returns_none_when_git_config_fails(self) -> None:
        def run_git_config(*_args, **_kwargs):
            raise RuntimeError("git unavailable")

        self.assertIsNone(
            resolve_evelyn_page_url_from_runtime(
                deps=EvelynPageUrlRuntimeDeps(
                    project_root=Path("C:/Evelyn"),
                    configured_page_url="",
                    run_git_config=run_git_config,
                )
            )
        )


if __name__ == "__main__":
    unittest.main()
