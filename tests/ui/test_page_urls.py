from __future__ import annotations

import sys
import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.page_urls import derive_github_pages_url_from_remote, resolve_public_page_url  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
