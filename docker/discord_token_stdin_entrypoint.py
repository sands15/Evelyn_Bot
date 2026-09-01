from __future__ import annotations

import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, BinaryIO


TOKEN_ENV = "DISCORD_BOT_TOKEN"
MAX_TOKEN_BYTES = 512
MAIN_PATH = Path("/app/main.py")


class TokenBootstrapError(RuntimeError):
    pass


def execute_path(
    path: str,
    *,
    run_name: str,
    globals_dict: dict[str, Any],
) -> dict[str, Any]:
    globals_dict.update(
        {
            "__name__": run_name,
            "__file__": path,
            "__package__": None,
            "__loader__": None,
            "__spec__": None,
            "__cached__": None,
        }
    )
    exec(compile(Path(path).read_bytes(), path, "exec"), globals_dict)
    return globals_dict


def read_token(stream: BinaryIO) -> str:
    raw = stream.read(MAX_TOKEN_BYTES + 2)
    if (
        not raw.endswith(b"\n")
        or len(raw) > MAX_TOKEN_BYTES + 1
        or b"\n" in raw[:-1]
        or b"\r" in raw
    ):
        raise TokenBootstrapError("discord_token_stdin_invalid")
    payload = raw[:-1]
    if not payload or any(byte < 0x21 or byte > 0x7E for byte in payload):
        raise TokenBootstrapError("discord_token_stdin_invalid")
    try:
        return payload.decode("ascii")
    except UnicodeDecodeError as exc:
        raise TokenBootstrapError("discord_token_stdin_invalid") from exc


def _clear_imported_token(caller_globals: dict[str, Any]) -> None:
    caller_globals[TOKEN_ENV] = None
    for module_name in (
        "evelyn_core.config",
        "evelyn_core.main_runtime_config",
    ):
        module = sys.modules.get(module_name)
        if module is not None and hasattr(module, TOKEN_ENV):
            setattr(module, TOKEN_ENV, None)


def run_main(
    token: str,
    *,
    bot_class: type[Any] | None = None,
    path_runner: Callable[..., dict[str, Any]] = execute_path,
    main_path: Path = MAIN_PATH,
) -> None:
    if TOKEN_ENV in os.environ:
        raise TokenBootstrapError("discord_token_transport_invalid")
    if bot_class is None:
        from discord.ext.commands import Bot

        bot_class = Bot

    original_getenv = os.getenv
    original_run = bot_class.run
    caller_globals: dict[str, Any] = {}

    def stdin_getenv(key: str, default: Any = None) -> Any:
        if key == TOKEN_ENV:
            return token
        return original_getenv(key, default)

    def scrubbed_run(
        instance: Any,
        supplied_token: str,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        os.getenv = original_getenv
        _clear_imported_token(sys._getframe(1).f_globals)
        if supplied_token != token:
            raise TokenBootstrapError("discord_token_binding_invalid")
        return original_run(instance, supplied_token, *args, **kwargs)

    os.getenv = stdin_getenv
    bot_class.run = scrubbed_run
    try:
        path_runner(
            str(main_path),
            run_name="__main__",
            globals_dict=caller_globals,
        )
    finally:
        os.getenv = original_getenv
        bot_class.run = original_run
        os.environ.pop(TOKEN_ENV, None)
        _clear_imported_token(caller_globals)


def main() -> int:
    try:
        if TOKEN_ENV in os.environ:
            raise TokenBootstrapError("discord_token_transport_invalid")
        token = read_token(sys.stdin.buffer)
        run_main(token)
        return 0
    except TokenBootstrapError as exc:
        print(str(exc), file=sys.stderr)
        return 64
    except KeyboardInterrupt:
        return 130
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 1
    except BaseException:
        print("discord_runtime_failed", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
