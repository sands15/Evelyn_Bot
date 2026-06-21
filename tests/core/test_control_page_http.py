from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path

from aiohttp import web


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.control_page_http import (  # noqa: E402
    CONTROL_PAGE_NO_STORE_HEADERS,
    add_control_page_cors_headers,
    add_control_page_no_store_headers,
    build_control_page_health_payload,
    control_page_api_cors_applies,
    control_page_cors_middleware,
    control_page_file_response,
    control_page_json_response,
    resolve_control_page_asset_path,
)


class ControlPageHttpTests(unittest.TestCase):
    def test_json_response_preserves_utf8_payload_contract(self) -> None:
        response = control_page_json_response({"ok": True, "message": "안녕"}, status=202)

        self.assertEqual(response.status, 202)
        self.assertEqual(response.content_type, "application/json")
        self.assertEqual(json.loads(response.text), {"ok": True, "message": "안녕"})
        self.assertIn("안녕", response.text)

    def test_no_store_headers_are_shared_by_file_and_binary_responses(self) -> None:
        response = add_control_page_no_store_headers(web.Response(body=b"x"))

        for key, value in CONTROL_PAGE_NO_STORE_HEADERS.items():
            self.assertEqual(response.headers[key], value)

    def test_file_response_rejects_missing_paths_and_sets_no_store_headers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "index.html"
            path.write_text("ok", encoding="utf-8")

            response = control_page_file_response(path, not_found_text="missing")

            self.assertEqual(response.status, 200)
            self.assertEqual(response.headers["Cache-Control"], CONTROL_PAGE_NO_STORE_HEADERS["Cache-Control"])
            with self.assertRaises(web.HTTPNotFound):
                control_page_file_response(path.with_name("missing.html"), not_found_text="missing")

    def test_asset_path_resolution_stays_inside_assets_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            asset = root / "assets" / "app.js"
            asset.parent.mkdir()
            asset.write_text("console.log('ok')", encoding="utf-8")

            self.assertEqual(resolve_control_page_asset_path(asset.parent, "app.js"), asset.resolve())
            with self.assertRaises(web.HTTPForbidden):
                resolve_control_page_asset_path(asset.parent, "../outside.js")
            with self.assertRaises(web.HTTPNotFound):
                resolve_control_page_asset_path(asset.parent, "missing.js")

    def test_cors_helpers_apply_only_to_control_page_api_paths(self) -> None:
        self.assertTrue(control_page_api_cors_applies("/api/control-page/state"))
        self.assertFalse(control_page_api_cors_applies("/health"))

        api_response = add_control_page_cors_headers(web.Response(status=200), path="/api/control-page/state")
        health_response = add_control_page_cors_headers(web.Response(status=200), path="/health")

        self.assertEqual(api_response.headers["Access-Control-Allow-Origin"], "*")
        self.assertNotIn("Access-Control-Allow-Origin", health_response.headers)

    def test_cors_middleware_short_circuits_options_for_control_page_api(self) -> None:
        class Request:
            method = "OPTIONS"
            path = "/api/control-page/state"

        async def handler(_request):
            return web.Response(status=500)

        response = asyncio.run(control_page_cors_middleware(Request(), handler))

        self.assertEqual(response.status, 204)
        self.assertEqual(response.headers["Access-Control-Allow-Methods"], "GET,POST,OPTIONS")

    def test_health_payload_matches_bot_api_contract(self) -> None:
        self.assertEqual(
            build_control_page_health_payload(local_only_mode=True, discord_enabled=False, port=8799),
            {
                "ok": True,
                "role": "bot-api",
                "controlPage": True,
                "localOnly": True,
                "discordEnabled": False,
                "port": 8799,
            },
        )


if __name__ == "__main__":
    unittest.main()
