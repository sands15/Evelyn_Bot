from __future__ import annotations

import contextlib
import io
import unittest
from unittest.mock import AsyncMock, Mock, patch

import discord

from tools.discord_capture_status_notify import (
    RESULT_MESSAGES,
    NotificationFailure,
    build_parser,
    main,
)


class DiscordCaptureStatusNotifyTests(unittest.TestCase):
    def test_parser_exposes_only_fixed_result_and_stdin_token(self) -> None:
        parser = build_parser()
        options = {
            option
            for action in parser._actions
            for option in action.option_strings
        }
        self.assertEqual(set(RESULT_MESSAGES), {"pass", "fail"})
        self.assertIn("--token-stdin", options)
        self.assertNotIn("--token", options)
        with self.assertRaisesRegex(NotificationFailure, "invalid_arguments"):
            parser.parse_args(
                [
                    "--channel-id",
                    "12345678901234567",
                    "--result",
                    "arbitrary-message",
                    "--token-stdin",
                ]
            )

    def test_token_and_runner_output_are_suppressed(self) -> None:
        secret = "DISCORD_PRIVATE_TOKEN_SENTINEL"
        seen: list[tuple[int, str, str]] = []

        async def runner(channel_id: int, result: str, token: str) -> None:
            seen.append((channel_id, result, token))
            print(secret)

        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            exit_code = main(
                [
                    "--channel-id",
                    "12345678901234567",
                    "--result",
                    "pass",
                    "--token-stdin",
                ],
                stdin=io.StringIO(secret + "\n"),
                runner=runner,
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(seen, [(12345678901234567, "pass", secret)])
        self.assertEqual(stdout.getvalue(), "notification_sent result=pass\n")
        self.assertEqual(stderr.getvalue(), "")
        self.assertNotIn(secret, stdout.getvalue() + stderr.getvalue())

    def test_messages_are_fixed_bounded_and_disable_user_content(self) -> None:
        rendered = "\n".join(RESULT_MESSAGES.values())
        self.assertTrue(all(0 < len(value) <= 2_000 for value in RESULT_MESSAGES.values()))
        for forbidden in (
            "12345678901234567",
            "DISCORD_PRIVATE_TOKEN_SENTINEL",
            "runtime_artifacts",
            "transcript",
        ):
            self.assertNotIn(forbidden, rendered)

    def test_missing_or_oversized_token_has_one_fixed_error(self) -> None:
        for token in ("", "x" * 514):
            stdout = io.StringIO()
            stderr = io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                exit_code = main(
                    [
                        "--channel-id",
                        "12345678901234567",
                        "--result",
                        "fail",
                        "--token-stdin",
                    ],
                    stdin=io.StringIO(token + "\n"),
                )
            self.assertEqual(exit_code, 1)
            self.assertEqual(stdout.getvalue(), "")
            self.assertEqual(
                stderr.getvalue(),
                "notification_failed code=discord_token_invalid\n",
            )


class ResultNotificationClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_on_ready_is_one_shot_and_uses_no_mentions(self) -> None:
        from tools.discord_capture_status_notify import ResultNotificationClient

        client = ResultNotificationClient(channel_id=123, result="pass")
        channel = Mock(spec=discord.TextChannel)
        channel.send = AsyncMock()
        with patch.object(client, "get_channel", return_value=channel), patch.object(
            client, "close", new=AsyncMock()
        ):
            await client.on_ready()
            await client.on_ready()

        channel.send.assert_awaited_once()
        self.assertIs(channel.send.await_args.kwargs["allowed_mentions"].everyone, False)
        self.assertTrue(client.sent)
        self.assertIsNone(client.error_code)
        self.assertTrue(client.done.is_set())

    async def test_channel_resolution_failure_is_preserved(self) -> None:
        from tools.discord_capture_status_notify import ResultNotificationClient

        client = ResultNotificationClient(channel_id=123, result="fail")
        with patch.object(client, "get_channel", return_value=None), patch.object(
            client,
            "fetch_channel",
            new=AsyncMock(side_effect=RuntimeError("private failure")),
        ), patch.object(client, "close", new=AsyncMock()):
            await client.on_ready()

        self.assertFalse(client.sent)
        self.assertEqual(client.error_code, "status_channel_unavailable")
        self.assertTrue(client.done.is_set())


if __name__ == "__main__":
    unittest.main()
