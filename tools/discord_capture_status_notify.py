from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
import sys
from collections.abc import Awaitable, Callable, Sequence
from typing import TextIO

import discord


MAX_TOKEN_BYTES = 512
NOTIFY_TIMEOUT_SEC = 30.0
SEND_TIMEOUT_SEC = 10.0
RESULT_MESSAGES = {
    "pass": (
        "[Evelyn Discord 음성 검증] 성공: 10/10 guided capture와 사후 STT "
        "model diagnostic을 마쳤고 runtime 복구를 확인했습니다. corpus 자동 "
        "승격은 하지 않았습니다."
    ),
    "fail": (
        "[Evelyn Discord 음성 검증] 실패: 이번 결과는 corpus에 승격하지 "
        "않았습니다. 세부 원인은 현재 Codex 작업에서 확인해 주세요."
    ),
    "accepted": (
        "[Evelyn Discord 음성 검증] 수동 승인 성공: guided capture 10/10을 "
        "domain-discord-pcm 후보로 수락했습니다. 자동 STT model diagnostic "
        "FAIL 기록은 보존합니다. 현재 v1 진단에는 capture와의 same-run 암호 "
        "결박이 없으며 전체 corpus/production 승격은 아직 하지 않았습니다."
    ),
}


class NotificationFailure(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        del message
        raise NotificationFailure("invalid_arguments")


def _positive_id(value: str) -> int:
    try:
        parsed = int(value, 10)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError("invalid") from None
    if parsed <= 0 or parsed >= 2**64:
        raise argparse.ArgumentTypeError("invalid")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = SafeArgumentParser(description="Send one fixed Discord validation result.")
    parser.add_argument("--channel-id", required=True, type=_positive_id)
    parser.add_argument("--result", required=True, choices=tuple(RESULT_MESSAGES))
    parser.add_argument("--token-stdin", required=True, action="store_true")
    return parser


def read_token(stdin: TextIO) -> str:
    raw = stdin.readline(MAX_TOKEN_BYTES + 2)
    if len(raw.encode("utf-8", errors="ignore")) > MAX_TOKEN_BYTES + 1:
        raw = ""
        raise NotificationFailure("discord_token_invalid")
    token = raw.rstrip("\r\n")
    raw = ""
    if (
        not token
        or len(token.encode("utf-8")) > MAX_TOKEN_BYTES
        or any(character.isspace() for character in token)
    ):
        token = ""
        raise NotificationFailure("discord_token_invalid")
    return token


class ResultNotificationClient(discord.Client):
    def __init__(self, *, channel_id: int, result: str) -> None:
        intents = discord.Intents.none()
        intents.guilds = True
        super().__init__(intents=intents)
        self.channel_id = int(channel_id)
        self.message = RESULT_MESSAGES[result]
        self.sent = False
        self.error_code: str | None = None
        self.done = asyncio.Event()
        self._started = False

    async def on_ready(self) -> None:
        if self._started:
            return
        self._started = True
        try:
            try:
                channel = self.get_channel(self.channel_id)
                if channel is None:
                    channel = await self.fetch_channel(self.channel_id)
            except Exception:
                self.error_code = "status_channel_unavailable"
                return
            if not isinstance(channel, discord.abc.Messageable):
                self.error_code = "status_channel_unavailable"
                return
            await asyncio.wait_for(
                channel.send(
                    self.message,
                    allowed_mentions=discord.AllowedMentions.none(),
                ),
                timeout=SEND_TIMEOUT_SEC,
            )
            self.sent = True
        except Exception:
            self.error_code = "status_send_failed"
        finally:
            self.done.set()
            with contextlib.suppress(Exception):
                await self.close()


async def notify(channel_id: int, result: str, token: str) -> None:
    client = ResultNotificationClient(channel_id=channel_id, result=result)
    gateway: asyncio.Task[None] | None = None
    completed: asyncio.Task[bool] | None = None
    try:
        try:
            await client.login(token)
        except discord.LoginFailure as exc:
            raise NotificationFailure("discord_auth_failed") from exc
        token = ""
        gateway = asyncio.create_task(client.connect(reconnect=False))
        completed = asyncio.create_task(client.done.wait())
        finished, _ = await asyncio.wait(
            {gateway, completed},
            timeout=NOTIFY_TIMEOUT_SEC,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if not finished:
            raise NotificationFailure("status_send_failed")
        if completed not in finished:
            with contextlib.suppress(Exception):
                await gateway
            raise NotificationFailure("status_send_failed")
        if client.error_code is not None:
            raise NotificationFailure(client.error_code)
        if not client.sent:
            raise NotificationFailure("status_send_failed")
    except TimeoutError as exc:
        raise NotificationFailure("status_send_failed") from exc
    finally:
        token = ""
        for task in (completed, gateway):
            if task is not None and not task.done():
                task.cancel()
        if not client.is_closed():
            with contextlib.suppress(Exception):
                await client.close()
        for task in (completed, gateway):
            if task is not None:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task


def main(
    argv: Sequence[str] | None = None,
    *,
    stdin: TextIO | None = None,
    runner: Callable[[int, str, str], Awaitable[None]] = notify,
) -> int:
    token = ""
    try:
        args = build_parser().parse_args(argv)
        token = read_token(stdin or sys.stdin)
        with open(os.devnull, "w", encoding="utf-8") as discard:
            with contextlib.redirect_stdout(discard), contextlib.redirect_stderr(discard):
                asyncio.run(runner(int(args.channel_id), str(args.result), token))
        token = ""
    except NotificationFailure as exc:
        print(f"notification_failed code={exc.code}", file=sys.stderr)
        return 1
    except Exception:
        print("notification_failed code=notification_failed", file=sys.stderr)
        return 1
    finally:
        token = ""
    print(f"notification_sent result={args.result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
