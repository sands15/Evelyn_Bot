from __future__ import annotations

import ssl
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = next(
    path for path in Path(__file__).resolve().parents if (path / "main.py").exists()
)
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.control_page_server import (  # noqa: E402
    build_control_page_ssl_context,
)


class _FakeSslContext:
    def __init__(self) -> None:
        self.minimum_version = None
        self.loaded: tuple[str, str] | None = None

    def load_cert_chain(self, cert_file: str, key_file: str) -> None:
        self.loaded = (cert_file, key_file)


class ControlPageTlsTests(unittest.TestCase):
    def test_existing_non_archive_control_page_can_remain_http(self) -> None:
        self.assertIsNone(
            build_control_page_ssl_context(
                archive_enabled=False,
                cert_file="",
                key_file="",
            )
        )

    def test_archive_mode_fails_closed_without_complete_tls_material(self) -> None:
        with self.assertRaisesRegex(
            RuntimeError,
            "conversation_archive_loopback_https_required",
        ):
            build_control_page_ssl_context(
                archive_enabled=True,
                cert_file="",
                key_file="",
            )
        with tempfile.TemporaryDirectory() as temporary:
            cert = Path(temporary) / "cert.pem"
            cert.write_text("certificate", encoding="utf-8")
            with self.assertRaisesRegex(
                RuntimeError,
                "control_page_tls_material_unavailable",
            ):
                build_control_page_ssl_context(
                    archive_enabled=True,
                    cert_file=str(cert),
                    key_file="",
                )

    def test_archive_mode_loads_tls_12_or_newer_context(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            cert = Path(temporary) / "cert.pem"
            key = Path(temporary) / "key.pem"
            cert.write_text("certificate", encoding="utf-8")
            key.write_text("private key", encoding="utf-8")
            fake = _FakeSslContext()

            result = build_control_page_ssl_context(
                archive_enabled=True,
                cert_file=str(cert),
                key_file=str(key),
                context_factory=lambda: fake,
            )

        self.assertIs(result, fake)
        self.assertEqual(fake.minimum_version, ssl.TLSVersion.TLSv1_2)
        self.assertEqual(fake.loaded, (str(cert), str(key)))


if __name__ == "__main__":
    unittest.main()
