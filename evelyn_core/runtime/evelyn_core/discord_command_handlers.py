from __future__ import annotations

import asyncio
import contextlib
import io
import inspect
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

try:
    import discord
    from discord.ext import commands
except Exception:  # pragma: no cover
    discord = None
    class _FallbackCheckFailure(Exception):
        """discord.py이 없는 환경에서 체크 실패 예외 대체."""

    class _FallbackUserInputError(Exception):
        """discord.py이 없는 환경에서 입력 실패 예외 대체."""

    class _FallbackCommandNotFound(Exception):
        """discord.py이 없는 환경에서 미등록 명령 예외 대체."""

    class _FallbackCommands:
        CheckFailure = _FallbackCheckFailure
        UserInputError = _FallbackUserInputError
        TooManyArguments = _FallbackUserInputError
        CommandNotFound = _FallbackCommandNotFound

    commands = _FallbackCommands()

from .text import clean_text
from .autonomy_authorization import ASSISTANT_AUTONOMY_ACTIONS
from .discord_commands import (
    control_command_check_failure_message,
    is_control_command_authorized_payload,
)
from .discord_conversation_archive_runtime import (
    DiscordInteractionContext,
    EPHEMERAL_DELETE_AFTER_SECONDS,
    RecordCommandRejected,
    attempt_ephemeral_response_delete,
    classify_discord_ephemeral_delete_error,
    build_record_command_policy,
)
from .guild_runtime_reset import (
    AUTONOMY_COGNITIVE_REFRESH_INFLIGHT,
    AUTONOMY_RUNTIME_ACTIVE,
    MEMORY_BACKGROUND_WORK_INFLIGHT,
    SEARCH_BACKGROUND_WORK_INFLIGHT,
)
from .minecraft_action_contract import MINECRAFT_ROUTE_ACTIONS
from .minecraft_mode_composition import (
    MINECRAFT_CONNECTED_OUTCOME,
    MINECRAFT_STOPPED_OUTCOME,
    minecraft_connection_confirmed,
    minecraft_stop_confirmed,
)
from .public_error_contract import public_failure_message


AUTONOMY_START_STALE_REPLY = (
    "길드 상태가 초기화되어 자율 행동 시작을 취소했어. 다시 요청해줘."
)
ARCHIVE_DELETE_CONFIRM_SECONDS = 60.0
ARCHIVE_COMMAND_PAGE_LIMIT = 25
DISCORD_SHARED_ARCHIVE_NOTICE = (
    "🔴 기록·전사 중: 이 Discord 공유 세션에서는 대화(채팅과 확정 음성 전사(final STT))와 "
    "Minecraft 명령·결과만 최대 30일 보관해. 원본 음성(raw audio)은 저장하지 않아.\n"
    "각 사용자는 본인이 작성하거나 말한 기록과 거기에 직접 연결된 이블린 답변만 "
    "`/기록열람`으로 볼 수 있고, `/기록삭제`로 직접 삭제를 요청할 수 있어.\n"
    "`/피드백제출`은 본인의 최신 채팅·음성 답변에 검토 대기 신호만 남기며, "
    "자동 개선·승인·활성화는 하지 않아.\n"
    "음성 참여는 `/기록동의`를 한 번 실행한 뒤 시작되고, `/기록철회`로 언제든 철회할 수 있어. "
    "동의하지 않았거나 음소거·청각 차단·Stage 억제 중이면 음성을 전사하지 않아."
)
_ARCHIVE_MESSAGE_LIMIT = 1900
_KST = timezone(timedelta(hours=9))
_MEMORY_GUILD_RESET_FAILURE_CODES = frozenset(
    {
        "memory_deletion_journal_busy",
        "memory_deletion_journal_integrity_failed",
        "memory_guild_reset_delete_failed",
        "memory_guild_reset_directory_delete_failed",
        "memory_guild_reset_durability_failed",
        "memory_guild_reset_legacy_scope_missing",
        "memory_guild_reset_scan_failed",
        "memory_guild_reset_scope_invalid",
        "memory_guild_reset_target_invalid",
        "memory_guild_reset_verification_failed",
        "search_followup_guild_reset_failed",
    }
)


@dataclass(frozen=True, slots=True)
class _DeleteConfirmationBinding:
    guild_id: int
    user_id: int
    expires_at: float


class RecordDeletionConfirmationGuard:
    """Bind an archive preview to one guild caller for one 60-second use."""

    def __init__(self, *, monotonic: Any = time.monotonic) -> None:
        self._monotonic = monotonic
        self._bindings: dict[str, _DeleteConfirmationBinding] = {}

    def remember(self, preview_id: str, *, guild_id: int, user_id: int) -> None:
        now = float(self._monotonic())
        self._prune(now)
        self._bindings[str(preview_id)] = _DeleteConfirmationBinding(
            guild_id=int(guild_id),
            user_id=int(user_id),
            expires_at=now + ARCHIVE_DELETE_CONFIRM_SECONDS,
        )

    def consume(self, preview_id: str, *, guild_id: int, user_id: int) -> bool:
        now = float(self._monotonic())
        self._prune(now)
        key = str(preview_id)
        binding = self._bindings.get(key)
        if (
            binding is None
            or binding.guild_id != int(guild_id)
            or binding.user_id != int(user_id)
            or now >= binding.expires_at
        ):
            return False
        self._bindings.pop(key, None)
        return True

    def _prune(self, now: float) -> None:
        for preview_id, binding in tuple(self._bindings.items()):
            if now >= binding.expires_at:
                self._bindings.pop(preview_id, None)


if discord is not None:

    class RecordPageView(discord.ui.View):
        """Invoker-only next-page button carrying one opaque page handle."""

        def __init__(
            self,
            *,
            page_handle: str,
            guild_id: int,
            user_id: int,
            read_self: Any,
        ) -> None:
            super().__init__(timeout=EPHEMERAL_DELETE_AFTER_SECONDS)
            self._page_handle = str(page_handle)
            self._guild_id = int(guild_id)
            self._user_id = int(user_id)
            self._read_self = read_self
            button = discord.ui.Button(
                label="다음 페이지",
                style=discord.ButtonStyle.secondary,
                custom_id=f"evelyn-archive-page:{self._page_handle}",
            )
            button.callback = self._next
            self.add_item(button)

        async def _next(self, interaction: Any) -> None:
            guild_id = getattr(interaction, "guild_id", None)
            if guild_id is None:
                guild_id = getattr(getattr(interaction, "guild", None), "id", None)
            user_id = getattr(getattr(interaction, "user", None), "id", None)
            if guild_id != self._guild_id or user_id != self._user_id:
                await _send_record_rejection(
                    interaction,
                    "이 페이지는 기록을 조회한 사용자만 열 수 있어.",
                )
                return
            try:
                interaction_id = _record_interaction_id(interaction)
            except ValueError:
                await _send_record_rejection(
                    interaction,
                    "현재 상호작용을 확인할 수 없어 페이지를 열지 않았어.",
                )
                return
            await interaction.response.defer(ephemeral=True, thinking=True)
            next_view: Any = None
            try:
                page = await _maybe_await(
                    self._read_self(
                        actor_external_id=str(self._user_id),
                        guild_id=str(self._guild_id),
                        interaction_id=str(interaction_id),
                        started_at=None,
                        ended_at=None,
                        page_handle=self._page_handle,
                    )
                )
                next_handle = str(
                    getattr(page, "next_page_handle", None) or ""
                )
                if next_handle:
                    next_view = RecordPageView(
                        page_handle=next_handle,
                        guild_id=self._guild_id,
                        user_id=self._user_id,
                        read_self=self._read_self,
                    )
                content, attachments = _render_record_page_response(
                    getattr(page, "records", ())
                )
            except Exception:
                content = "다음 기록 페이지를 안전하게 불러오지 못했어. 다시 조회해줘."
                attachments = []
            self.stop()
            await _edit_record_response(
                interaction,
                content,
                view=next_view,
                attachments=attachments,
            )

    class RecordDeletionConfirmationView(discord.ui.View):
        """One-use deletion button carrying only the opaque preview handle."""

        def __init__(
            self,
            *,
            preview_id: str,
            guild_id: int,
            user_id: int,
            apply_delete: Any,
            confirmation_guard: RecordDeletionConfirmationGuard,
            create_task: Any = asyncio.create_task,
            sleep_fn: Any = asyncio.sleep,
        ) -> None:
            super().__init__(timeout=ARCHIVE_DELETE_CONFIRM_SECONDS)
            self._preview_id = str(preview_id)
            self._guild_id = int(guild_id)
            self._user_id = int(user_id)
            self._apply_delete = apply_delete
            self._confirmation_guard = confirmation_guard
            self._create_task = create_task
            self._sleep_fn = sleep_fn
            button = discord.ui.Button(
                label="삭제 확인",
                style=discord.ButtonStyle.danger,
                custom_id=f"evelyn-archive-delete:{self._preview_id}",
            )
            button.callback = self._confirm
            self.add_item(button)

        async def _confirm(self, interaction: Any) -> None:
            guild_id = getattr(interaction, "guild_id", None)
            if guild_id is None:
                guild_id = getattr(getattr(interaction, "guild", None), "id", None)
            user_id = getattr(getattr(interaction, "user", None), "id", None)
            if guild_id != self._guild_id or user_id != self._user_id:
                await _send_record_rejection(
                    interaction,
                    "이 삭제 확인은 미리보기를 요청한 사용자만 사용할 수 있어.",
                )
                return
            try:
                interaction_id = _record_interaction_id(interaction)
            except ValueError:
                await _send_record_rejection(
                    interaction,
                    "현재 상호작용을 확인할 수 없어 삭제하지 않았어.",
                )
                return

            await interaction.response.defer()
            if not self._confirmation_guard.consume(
                self._preview_id,
                guild_id=self._guild_id,
                user_id=self._user_id,
            ):
                content = "삭제 확인이 만료됐거나 이미 사용됐어. 미리보기부터 다시 시작해줘."
            else:
                try:
                    result = await _maybe_await(
                        self._apply_delete(
                            preview_id=self._preview_id,
                            actor_external_id=str(self._user_id),
                            request_guild_id=str(self._guild_id),
                            interaction_id=str(interaction_id),
                        )
                    )
                    content = _render_deletion_result(result)
                except Exception as exc:
                    error_name = type(exc).__name__
                    if error_name in {"ArchivePreviewExpired", "ArchivePreviewConsumed"}:
                        content = "삭제 확인이 만료됐거나 이미 사용됐어. 미리보기부터 다시 시작해줘."
                    elif error_name == "ArchivePreviewConflict":
                        content = "삭제 대상이 바뀌었어. 안전을 위해 미리보기부터 다시 시작해줘."
                    else:
                        content = "삭제 요청을 안전하게 처리하지 못했어. 잠시 뒤 다시 시도해줘."
            self.stop()
            if await _edit_record_response(interaction, content, view=None):
                _schedule_ephemeral_cleanup(
                    interaction,
                    create_task=self._create_task,
                    sleep_fn=self._sleep_fn,
                )

else:  # pragma: no cover - archive Discord commands require discord.py

    class RecordPageView:
        def __init__(self, **_kwargs: Any) -> None:
            raise RuntimeError("discord_py_required")

    class RecordDeletionConfirmationView:
        def __init__(self, **_kwargs: Any) -> None:
            raise RuntimeError("discord_py_required")


async def handle_record_view_application_command(
    interaction: Any,
    *,
    feature_enabled: bool,
    read_self: Any,
    create_task: Any = asyncio.create_task,
    sleep_fn: Any = asyncio.sleep,
    started_at: str | None = None,
    ended_at: str | None = None,
) -> None:
    policy = await _begin_record_application_command(
        interaction,
        feature_enabled=feature_enabled,
    )
    if policy is None:
        return
    view: Any = None
    try:
        interaction_id = _record_interaction_id(interaction)
        start, end = _parse_record_period(started_at, ended_at)
        page = await _maybe_await(
            read_self(
                actor_external_id=str(policy.invoker_user_id),
                guild_id=str(policy.guild_id),
                interaction_id=str(interaction_id),
                started_at=start,
                ended_at=end,
                page_handle=None,
            )
        )
        records = getattr(page, "records", page)
        next_handle = str(getattr(page, "next_page_handle", None) or "")
        view = (
            RecordPageView(
                page_handle=next_handle,
                guild_id=policy.guild_id,
                user_id=policy.invoker_user_id,
                read_self=read_self,
            )
            if next_handle
            else None
        )
        content, attachments = _render_record_page_response(records)
    except ValueError:
        content = "기간은 시작과 끝을 함께 ISO 형식으로 입력해줘. 예: 2026-08-01T00:00+09:00"
        attachments = []
    except Exception:
        content = "기록을 안전하게 불러오지 못했어. 잠시 뒤 다시 시도해줘."
        view = None
        attachments = []
    if await _edit_record_response(
        interaction,
        content,
        view=view,
        attachments=attachments,
    ):
        _schedule_ephemeral_cleanup(
            interaction,
            create_task=create_task,
            sleep_fn=sleep_fn,
        )


async def handle_record_delete_application_command(
    interaction: Any,
    *,
    feature_enabled: bool,
    preview_delete: Any,
    apply_delete: Any,
    confirmation_guard: RecordDeletionConfirmationGuard,
    create_task: Any = asyncio.create_task,
    sleep_fn: Any = asyncio.sleep,
    started_at: str | None = None,
    ended_at: str | None = None,
) -> None:
    policy = await _begin_record_application_command(
        interaction,
        feature_enabled=feature_enabled,
    )
    if policy is None:
        return
    view: Any = None
    try:
        interaction_id = _record_interaction_id(interaction)
        start, end = _parse_record_period(started_at, ended_at)
        preview = await _maybe_await(
            preview_delete(
                actor_external_id=str(policy.invoker_user_id),
                request_guild_id=str(policy.guild_id),
                interaction_id=str(interaction_id),
                started_at=start,
                ended_at=end,
            )
        )
        preview_id = str(getattr(preview, "preview_id", "") or "")
        if not preview_id:
            raise RuntimeError("archive_preview_missing")
        confirmation_guard.remember(
            preview_id,
            guild_id=policy.guild_id,
            user_id=policy.invoker_user_id,
        )
        view = RecordDeletionConfirmationView(
            preview_id=preview_id,
            guild_id=policy.guild_id,
            user_id=policy.invoker_user_id,
            apply_delete=apply_delete,
            confirmation_guard=confirmation_guard,
            create_task=create_task,
            sleep_fn=sleep_fn,
        )
        content = _render_deletion_preview(preview)
    except ValueError:
        content = "기간은 시작과 끝을 함께 ISO 형식으로 입력해줘. 예: 2026-08-01T00:00+09:00"
    except Exception as exc:
        error_name = type(exc).__name__
        if error_name in {"ArchivePreviewExpired", "ArchivePreviewConsumed"}:
            content = "삭제 확인이 만료됐거나 이미 사용됐어. 미리보기부터 다시 시작해줘."
        elif error_name == "ArchivePreviewConflict":
            content = "삭제 대상이 바뀌었어. 안전을 위해 미리보기부터 다시 시작해줘."
        else:
            content = "삭제 요청을 안전하게 처리하지 못했어. 잠시 뒤 다시 시도해줘."
    if await _edit_record_response(interaction, content, view=view):
        _schedule_ephemeral_cleanup(
            interaction,
            create_task=create_task,
            sleep_fn=sleep_fn,
        )


async def handle_record_consent_application_command(
    interaction: Any,
    *,
    feature_enabled: bool,
    set_consent: Any,
    consented: bool,
    create_task: Any = asyncio.create_task,
    sleep_fn: Any = asyncio.sleep,
) -> None:
    policy = await _begin_record_application_command(
        interaction,
        feature_enabled=feature_enabled,
    )
    if policy is None:
        return
    voice_state = getattr(getattr(interaction, "user", None), "voice", None)
    channel = getattr(voice_state, "channel", None)
    channel_id = getattr(channel, "id", None)
    if channel_id is None and consented:
        content = "먼저 이 서버의 음성 채널에 들어가줘."
    else:
        try:
            await _maybe_await(
                set_consent(
                    guild_id=str(policy.guild_id),
                    actor_external_id=str(policy.invoker_user_id),
                    owner_name=str(
                        getattr(interaction.user, "display_name", None)
                        or getattr(interaction.user, "global_name", None)
                        or getattr(interaction.user, "name", None)
                        or policy.invoker_user_id
                    ),
                    channel_id=None if channel_id is None else str(channel_id),
                    consented=bool(consented),
                    self_mute=bool(getattr(voice_state, "self_mute", False)),
                    server_mute=bool(getattr(voice_state, "mute", False)),
                    stage_suppress=bool(getattr(voice_state, "suppress", False)),
                    self_deaf=bool(getattr(voice_state, "self_deaf", False)),
                    server_deaf=bool(getattr(voice_state, "deaf", False)),
                )
            )
            content = (
                "기록 안내에 동의했어. 음소거·청각 차단·Stage 억제 중에는 참여자로 인정하지 않아."
                if consented
                else "기록 동의를 철회했어. 지금부터 새 음성 참여 구간을 열지 않아."
            )
        except Exception:
            content = "기록 동의 상태를 안전하게 바꾸지 못했어. 잠시 뒤 다시 시도해줘."
    if await _edit_record_response(interaction, content):
        _schedule_ephemeral_cleanup(
            interaction,
            create_task=create_task,
            sleep_fn=sleep_fn,
        )


async def handle_feedback_application_command(
    interaction: Any,
    *,
    feature_enabled: bool,
    capture_feedback: Any,
    source_surface: str,
    category: str,
    correction: str,
    requested_change_scope: str,
    create_task: Any = asyncio.create_task,
    sleep_fn: Any = asyncio.sleep,
) -> None:
    """Capture only the caller's latest text/voice answer for local review."""

    policy = await _begin_record_application_command(
        interaction,
        feature_enabled=feature_enabled,
    )
    if policy is None:
        return
    channel_id = getattr(interaction, "channel_id", None)
    if channel_id is None:
        channel_id = getattr(getattr(interaction, "channel", None), "id", None)
    try:
        interaction_id = _record_interaction_id(interaction)
        result = await _maybe_await(
            capture_feedback(
                guild_id=policy.guild_id,
                channel_id=channel_id,
                user_id=policy.invoker_user_id,
                owner_name=str(
                    getattr(interaction.user, "display_name", None)
                    or getattr(interaction.user, "global_name", None)
                    or getattr(interaction.user, "name", None)
                    or policy.invoker_user_id
                ),
                source_surface=str(source_surface),
                category=str(category),
                correction=str(correction),
                requested_change_scope=str(requested_change_scope),
                feedback_nonce=str(interaction_id),
            )
        )
        route = str(getattr(result, "route", "") or "")
        if route == "identity_review":
            content = (
                "말투·정체성 검토 대기열에 저장했어. "
                "자동으로 성격이나 규칙을 바꾸지는 않아."
            )
        elif route == "human_engineering_required":
            content = (
                "사람의 설계·보안 검토가 필요한 요청으로 저장했어. "
                "도구·권한·평가·소스는 자동으로 바꾸지 않아."
            )
        elif route == "review_only":
            content = (
                "본인 답변에 연결된 검토 대기 피드백으로 저장했어. "
                "자동 개선 후보나 활성 규칙은 만들지 않아."
            )
        else:
            raise RuntimeError("archive_feedback_route_invalid")
    except Exception as exc:
        if getattr(exc, "code", "") == "archive_feedback_target_missing":
            content = (
                "현재 공유 세션에서 선택한 출처의 본인 답변을 찾지 못했어. "
                "먼저 이블린과 채팅하거나 음성으로 대화한 뒤 다시 제출해줘."
            )
        else:
            content = "피드백을 안전하게 저장하지 못했어. 잠시 뒤 다시 시도해줘."
    if await _edit_record_response(interaction, content):
        _schedule_ephemeral_cleanup(
            interaction,
            create_task=create_task,
            sleep_fn=sleep_fn,
        )


async def _begin_record_application_command(
    interaction: Any,
    *,
    feature_enabled: bool,
) -> Any | None:
    if feature_enabled is not True:
        await _send_record_rejection(interaction, "개인 기록 기능이 아직 활성화되지 않았어.")
        return None
    guild_id = getattr(interaction, "guild_id", None)
    if guild_id is None:
        guild_id = getattr(getattr(interaction, "guild", None), "id", None)
    user_id = getattr(getattr(interaction, "user", None), "id", None)
    try:
        _record_interaction_id(interaction)
        policy = build_record_command_policy(
            context=(
                DiscordInteractionContext.GUILD
                if guild_id is not None
                else DiscordInteractionContext.BOT_DM
            ),
            guild_id=guild_id,
            invoker_user_id=user_id,
        )
    except (RecordCommandRejected, TypeError, ValueError):
        await _send_record_rejection(
            interaction,
            "이 명령은 서버에서 실행한 본인 기록에만 사용할 수 있어.",
        )
        return None
    await interaction.response.defer(ephemeral=True, thinking=True)
    return policy


def _record_interaction_id(interaction: Any) -> int:
    value = getattr(interaction, "id", None)
    if isinstance(value, bool):
        raise ValueError("archive_interaction_id_invalid")
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        raise ValueError("archive_interaction_id_invalid") from None
    if normalized <= 0 or str(normalized) != str(value):
        raise ValueError("archive_interaction_id_invalid")
    return normalized


async def _send_record_rejection(interaction: Any, content: str) -> None:
    response = getattr(interaction, "response", None)
    sender = getattr(response, "send_message", None)
    if callable(sender):
        kwargs: dict[str, Any] = {"ephemeral": True}
        if discord is not None:
            kwargs["allowed_mentions"] = discord.AllowedMentions.none()
        await sender(_bounded_archive_message(content), **kwargs)


async def _edit_record_response(
    interaction: Any,
    content: str,
    *,
    view: Any = ...,
    attachments: Any = ...,
) -> bool:
    editor = getattr(interaction, "edit_original_response", None)
    if not callable(editor):
        return False
    kwargs: dict[str, Any] = {"content": _bounded_archive_message(content)}
    if discord is not None:
        kwargs["allowed_mentions"] = discord.AllowedMentions.none()
    if view is not ...:
        kwargs["view"] = view
    if attachments is not ...:
        kwargs["attachments"] = attachments
    await editor(**kwargs)
    return True


def _schedule_ephemeral_cleanup(
    interaction: Any,
    *,
    create_task: Any,
    sleep_fn: Any,
) -> None:
    delete_original_response = getattr(interaction, "delete_original_response", None)
    if not callable(delete_original_response):
        return
    sleeper = asyncio.sleep if sleep_fn is None else sleep_fn
    coroutine = attempt_ephemeral_response_delete(
        delete_original_response,
        sleep_fn=sleeper,
        classify_error=classify_discord_ephemeral_delete_error,
    )
    try:
        create_task(coroutine)
    except Exception:
        coroutine.close()


async def _maybe_await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


def _parse_record_period(
    started_at: str | None,
    ended_at: str | None,
) -> tuple[datetime | None, datetime | None]:
    start_text = clean_text(str(started_at or ""))
    end_text = clean_text(str(ended_at or ""))
    if not start_text and not end_text:
        return None, None
    if not start_text or not end_text:
        raise ValueError("archive_period_incomplete")

    def parse(value: str) -> datetime:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=_KST)
        return parsed.astimezone(timezone.utc)

    start = parse(start_text)
    end = parse(end_text)
    if end <= start:
        raise ValueError("archive_period_invalid")
    return start, end


def _render_record_page(records: Any) -> str:
    if isinstance(records, str):
        return _bounded_archive_message(records)
    rows = tuple(records or ())[:ARCHIVE_COMMAND_PAGE_LIMIT]
    if not rows:
        return "이 서버에서 볼 수 있는 본인 기록이 없어."
    lines = ["내 기록 (한 페이지)"]
    for row in rows:
        started_at = getattr(row, "started_at", None)
        if isinstance(started_at, datetime):
            stamp = started_at.astimezone(_KST).strftime("%Y-%m-%d %H:%M")
        else:
            stamp = "시간 미상"
        record_type = clean_text(str(getattr(row, "record_type", "record") or "record"))
        body = str(getattr(row, "body", "") or "")
        candidate = f"{stamp} [{record_type}] {body}"
        if len("\n".join((*lines, candidate))) > _ARCHIVE_MESSAGE_LIMIT - 60:
            lines.append("… 기간을 좁혀 다시 조회해줘.")
            break
        lines.append(candidate)
    return "\n".join(lines)


def _render_record_page_response(records: Any) -> tuple[str, list[Any]]:
    rows = tuple(records or ())[:ARCHIVE_COMMAND_PAGE_LIMIT]
    content = _render_record_page(rows)
    if not rows or "… 기간을 좁혀 다시 조회해줘." not in content:
        return content, []
    if discord is None:  # pragma: no cover - commands are unavailable without discord.py
        return content, []
    lines = ["내 기록 (한 페이지)"]
    for row in rows:
        started_at = getattr(row, "started_at", None)
        stamp = (
            started_at.astimezone(_KST).strftime("%Y-%m-%d %H:%M")
            if isinstance(started_at, datetime)
            else "시간 미상"
        )
        record_type = clean_text(
            str(getattr(row, "record_type", "record") or "record")
        )
        lines.append(
            f"{stamp} [{record_type}] {str(getattr(row, 'body', '') or '')}"
        )
    attachment = discord.File(
        io.BytesIO("\n".join(lines).encode("utf-8")),
        filename="evelyn-my-records.txt",
    )
    return (
        "이 페이지의 전체 기록은 호출자 전용 임시 첨부 파일에 담았어. "
        "이 응답과 함께 180초 뒤 삭제를 시도해.",
        [attachment],
    )


def _render_deletion_preview(preview: Any) -> str:
    counts = getattr(preview, "counts_by_guild", {}) or {}
    count_lines = [f"- 서버 {guild_id}: {int(count)}개" for guild_id, count in sorted(counts.items())]
    if not count_lines:
        count_lines = ["- 대상 기록: 0개"]
    scope = "모든 서버·전체 기간" if getattr(preview, "all_guilds", False) else "현재 서버의 지정 기간"
    return _bounded_archive_message(
        "삭제 미리보기\n"
        f"범위: {scope}\n"
        + "\n".join(count_lines)
        + f"\n파생 기록: {int(getattr(preview, 'dependent_record_count', 0))}개"
        + f"\n참여 구간: {int(getattr(preview, 'interval_count', 0))}개"
        + "\n60초 안에 아래 삭제 확인 버튼을 한 번만 눌러줘."
    )


def _render_deletion_result(result: Any) -> str:
    status = clean_text(str(getattr(result, "status", "") or ""))
    affected = int(getattr(result, "affected_records", 0) or 0)
    dependent = int(getattr(result, "dependent_records", 0) or 0)
    intervals = int(getattr(result, "affected_intervals", 0) or 0)
    if status == "local_fully_purged":
        return f"로컬 원본과 백업 삭제를 검증했어. 기록 {affected}개, 파생 {dependent}개, 참여 구간 {intervals}개."
    if status == "local_cleanup_pending":
        return "삭제는 접수했고 모든 열람에서 숨겼어. 백업 또는 파생 사본 정리가 끝날 때까지 완료로 표시하지 않아."
    return "삭제 요청 상태를 확인할 수 없어. 다시 실행하지 말고 잠시 뒤 상태를 확인해줘."


def _bounded_archive_message(content: str) -> str:
    text = str(content or "")
    return text if len(text) <= _ARCHIVE_MESSAGE_LIMIT else text[: _ARCHIVE_MESSAGE_LIMIT - 1] + "…"


async def handle_join_voice_command(
    ctx: Any,
    *,
    ensure_listening_voice_client: Any,
    archive_session_open: Any = None,
    log: Any = print,
) -> None:
    voice_state = getattr(ctx.author, "voice", None)
    if not voice_state or not getattr(voice_state, "channel", None):
        await ctx.send("먼저 음성 채널에 들어가줘.")
        return

    try:
        vc = await ensure_listening_voice_client(ctx.guild, voice_state.channel)
        if vc is None:
            await ctx.send("❌ 음성 연결에 실패했어.")
            return
        if archive_session_open is not None:
            await ctx.send(DISCORD_SHARED_ARCHIVE_NOTICE)
            await _maybe_await(archive_session_open(ctx, voice_state.channel))
        await ctx.send(f"🔊 {voice_state.channel.name}에 들어왔어. 이제 듣고 말할게.")
    except Exception as exc:
        log("음성 연결 오류 type=", type(exc).__name__)
        await ctx.send(
            public_failure_message("voice_connect_failed")
        )


async def handle_rejoin_voice_command(
    ctx: Any,
    *,
    ensure_listening_voice_client: Any,
    archive_session_open: Any = None,
    log: Any = print,
) -> None:
    channel = ctx.author.voice.channel if getattr(ctx.author, "voice", None) else None
    if channel is None:
        await ctx.send("먼저 음성 채널에 들어가줘.")
        return

    vc = getattr(ctx.guild, "voice_client", None)
    if vc is not None:
        try:
            if hasattr(vc, "stop_listening"):
                vc.stop_listening()
        except Exception:
            pass
        await vc.disconnect(force=True)

    try:
        new_vc = await ensure_listening_voice_client(ctx.guild, channel)
        if new_vc is None:
            await ctx.send("❌ 재연결 실패")
            return
        if archive_session_open is not None:
            await ctx.send(DISCORD_SHARED_ARCHIVE_NOTICE)
            await _maybe_await(archive_session_open(ctx, channel))
        await ctx.send("🔄 다시 붙었어. 이제 계속 들을게.")
    except Exception as exc:
        log("재연결 오류 type=", type(exc).__name__)
        await ctx.send(
            public_failure_message("voice_reconnect_failed")
        )


async def handle_leave_voice_command(
    ctx: Any,
    *,
    mark_manual_disconnect: Any,
) -> None:
    vc = getattr(ctx.guild, "voice_client", None)
    if vc is None:
        await ctx.send("이미 나와 있어.")
        return

    try:
        if hasattr(vc, "stop_listening"):
            vc.stop_listening()
    except Exception:
        pass

    mark_manual_disconnect(ctx.guild, reason="leave_command")
    await vc.disconnect()
    await ctx.send("👋 나갔어.")


async def handle_prefix_command(
    ctx: Any,
    new_prefix: str | None,
    *,
    default_prefix: str,
    get_guild_command_prefix: Any,
    save_guild_command_prefix: Any,
    build_current_reply: Any,
    build_reset_reply: Any,
    build_saved_reply: Any,
    guild_only_message: Any,
) -> None:
    if ctx.guild is None:
        await ctx.send(guild_only_message())
        return

    guild_id = ctx.guild.id
    current_prefix = get_guild_command_prefix(guild_id)

    if not new_prefix:
        await ctx.send(build_current_reply(current_prefix))
        return

    if new_prefix.lower() in {"기본", "default", "reset"}:
        saved_prefix = save_guild_command_prefix(guild_id, default_prefix)
        await ctx.send(build_reset_reply(saved_prefix))
        return

    try:
        saved_prefix = save_guild_command_prefix(guild_id, new_prefix)
    except ValueError as exc:
        await ctx.send(f"❌ {exc}")
        return

    await ctx.send(build_saved_reply(saved_prefix))


async def handle_autonomy_start_command(
    ctx: Any,
    *,
    autonomy_enabled: bool,
    get_or_create_autonomy_engine: Any,
    is_minecraft_autonomy_route_enabled: Any,
    enable_minecraft_autonomy_route: Any,
    grant_autonomy_authorization: Any,
    revoke_autonomy_authorization: Any,
    guild_only_message: Any,
    guild_mutation_is_current: Any = None,
    record_runtime_error: Any = None,
    archive_autonomy_grant: Any = None,
) -> None:
    if ctx.guild is None:
        await ctx.send(guild_only_message())
        return
    if not autonomy_enabled:
        await ctx.send("자율 행동 기능이 설정에서 비활성화되어 있어.")
        return
    guild_id = ctx.guild.id
    try:
        engine = get_or_create_autonomy_engine(guild_id)
    except Exception as exc:
        if record_runtime_error is not None:
            record_runtime_error("autonomy_start_failed", exc)
        await ctx.send("❌ 자율 행동 시작에 실패했어.")
        return

    try:
        minecraft_route_enabled = bool(
            is_minecraft_autonomy_route_enabled(guild_id)
        )
    except Exception:
        minecraft_route_enabled = False

    try:
        await engine.stop()
    except asyncio.CancelledError:
        revoke_autonomy_authorization(
            guild_id,
            reason_code="start_failed",
        )
        raise
    except Exception as exc:
        if record_runtime_error is not None:
            record_runtime_error("autonomy_start_failed", exc)
        revoke_autonomy_authorization(
            guild_id,
            reason_code="start_failed",
        )
        await ctx.send("❌ 자율 행동 시작에 실패했고 승인은 폐기했어.")
        return
    if minecraft_route_enabled:
        try:
            minecraft_route_enabled = bool(
                await enable_minecraft_autonomy_route(guild_id)
            )
        except asyncio.CancelledError:
            revoke_autonomy_authorization(
                guild_id,
                reason_code="start_failed",
            )
            raise
        except Exception:
            minecraft_route_enabled = False

    try:
        start_is_current = (
            guild_mutation_is_current is None
            or bool(guild_mutation_is_current())
        )
    except Exception:
        start_is_current = False
    if not start_is_current:
        await ctx.send(AUTONOMY_START_STALE_REPLY)
        return

    scopes = list(ASSISTANT_AUTONOMY_ACTIONS)
    if minecraft_route_enabled:
        scopes.extend(MINECRAFT_ROUTE_ACTIONS)
    grant = grant_autonomy_authorization(
        guild_id,
        f"discord_user:{getattr(ctx.author, 'id', '')}",
        scopes=tuple(dict.fromkeys(scopes)),
    )
    if not isinstance(grant, dict) or not grant.get("ok"):
        await ctx.send("❌ 자율 행동 승인을 발급하지 못했어.")
        return
    if archive_autonomy_grant is not None:
        grant_id = str(
            ((grant.get("grant") or {}).get("grantId"))
            if isinstance(grant.get("grant"), dict)
            else ""
        )
        message = getattr(ctx, "message", None)
        channel = getattr(ctx, "channel", None)
        created_at = getattr(message, "created_at", None)
        try:
            if not grant_id or channel is None or message is None:
                raise RuntimeError("archive_autonomy_grant_context_missing")
            await _maybe_await(
                archive_autonomy_grant(
                    guild_id=int(guild_id),
                    channel_id=int(channel.id),
                    user_id=int(ctx.author.id),
                    owner_name=str(
                        getattr(ctx.author, "display_name", None)
                        or getattr(ctx.author, "global_name", None)
                        or getattr(ctx.author, "name", None)
                        or ctx.author.id
                    ),
                    message_id=int(message.id),
                    grant_id=grant_id,
                    authored_at=float(
                        created_at.timestamp()
                        if created_at is not None
                        else time.time()
                    ),
                    text=str(getattr(message, "content", "") or ""),
                )
            )
        except asyncio.CancelledError:
            revoke_autonomy_authorization(guild_id, reason_code="start_failed")
            raise
        except Exception as exc:
            revoke_autonomy_authorization(guild_id, reason_code="start_failed")
            if record_runtime_error is not None:
                record_runtime_error("conversation_archive_autonomy_grant_failed", exc)
            await ctx.send("❌ 자율 행동 기록을 안전하게 시작하지 못해 승인을 폐기했어.")
            return
    try:
        if guild_mutation_is_current is None:
            started = await engine.start()
        else:
            started = await engine.start(
                is_current=guild_mutation_is_current
            )
    except asyncio.CancelledError:
        revoke_autonomy_authorization(
            ctx.guild.id,
            reason_code="start_failed",
        )
        raise
    except Exception as exc:
        if record_runtime_error is not None:
            record_runtime_error("autonomy_start_failed", exc)
        revoke_autonomy_authorization(
            ctx.guild.id,
            reason_code="start_failed",
        )
        await ctx.send("❌ 자율 행동 시작에 실패했고 승인은 폐기했어.")
        return
    if started is False:
        revoke_autonomy_authorization(
            ctx.guild.id,
            reason_code="start_failed",
        )
        await ctx.send(AUTONOMY_START_STALE_REPLY)
        return
    await ctx.send("🤖 자율 행동 루프를 시작했어.")


async def handle_autonomy_stop_command(
    ctx: Any,
    *,
    autonomy_engines: dict[int, Any],
    revoke_autonomy_authorization: Any,
    guild_only_message: Any,
    record_runtime_error: Any = None,
    log: Any = print,
) -> None:
    if ctx.guild is None:
        await ctx.send(guild_only_message())
        return
    revoke_autonomy_authorization(
        ctx.guild.id,
        reason_code="explicit_autonomy_stop",
    )
    engine = autonomy_engines.get(ctx.guild.id)
    if engine is None:
        await ctx.send("이미 자율 행동이 꺼져 있어.")
        return
    try:
        await engine.stop()
    except Exception as exc:
        if record_runtime_error is not None:
            record_runtime_error("autonomy_stop_failed", exc)
        log("자율 행동 정지 오류 type=", type(exc).__name__)
        await ctx.send(
            public_failure_message("autonomy_stop_failed")
        )
        return
    await ctx.send("🛑 자율 행동 루프를 멈췄어.")


async def handle_autonomy_status_command(
    ctx: Any,
    *,
    autonomy_engines: dict[int, Any],
    get_routed_autonomy_executor: Any,
    get_autonomy_authorization_status: Any,
    build_reply: Any,
    guild_only_message: Any,
) -> None:
    if ctx.guild is None:
        await ctx.send(guild_only_message())
        return
    engine = autonomy_engines.get(ctx.guild.id)
    router = get_routed_autonomy_executor(ctx.guild.id)
    minecraft_enabled = bool(router and router.is_domain_enabled("minecraft"))
    try:
        authorization = get_autonomy_authorization_status()
    except Exception:
        authorization = {
            "state": "unknown",
            "auditReady": None,
        }
    await ctx.send(
        build_reply(
            engine.state if engine is not None else None,
            minecraft_enabled=minecraft_enabled,
            authorization=authorization,
            guild_id=ctx.guild.id,
        )
    )


async def handle_channel_setting_command(
    ctx: Any,
    action: str | None,
    channel: Any,
    *,
    setting_key: str,
    label: str,
    add_success: str,
    remove_success: str,
    normalize_action: Any,
    get_channel_ids: Any,
    add_channel_setting: Any,
    remove_channel_setting: Any,
    get_guild_command_prefix: Any,
    build_list_reply: Any,
    build_usage_reply: Any,
    guild_only_message: Any,
) -> None:
    if ctx.guild is None:
        await ctx.send(guild_only_message())
        return
    normalized_action = normalize_action(action)
    current = get_channel_ids(ctx.guild.id)
    if normalized_action in {"목록", "list"}:
        await ctx.send(build_list_reply(label=label, channel_ids=current, resolve_channel=ctx.guild.get_channel))
        return
    if channel is None:
        await ctx.send(build_usage_reply(get_guild_command_prefix(ctx.guild.id)))
        return
    if normalized_action in {"추가", "add"}:
        updated = add_channel_setting(ctx.guild.id, setting_key, channel.id)
        await ctx.send(add_success.format(channel=channel, count=len(updated)))
        return
    if normalized_action in {"제거", "remove", "삭제"}:
        updated = remove_channel_setting(ctx.guild.id, setting_key, channel.id)
        await ctx.send(remove_success.format(channel=channel, count=len(updated)))
        return
    await ctx.send(build_usage_reply(get_guild_command_prefix(ctx.guild.id)))


async def handle_restart_bot_command(
    ctx: Any,
    *,
    create_task: Any,
    restart_bot_process: Any,
) -> None:
    message = "🔄 봇을 재시작할게. 잠깐만 기다려줘."
    start_restart = lambda: create_task(restart_bot_process())
    send_with_hook = getattr(
        ctx,
        "send_with_post_delivery_hook",
        None,
    )
    if callable(send_with_hook):
        await send_with_hook(
            message,
            after_delivery=start_restart,
        )
        return
    await ctx.send(message)
    start_restart()


async def handle_shutdown_bot_command(
    ctx: Any,
    *,
    schedule_stack_shutdown: Any,
    create_task: Any,
    shutdown_bot_process: Any,
) -> None:
    message = (
        "Evelyn shutdown requested. Supervisors, bot, LLM, TTS, Voyager, "
        "and Evelyn-owned WSL services will stop if the full-stack helper "
        "starts; otherwise this bot process will stop instead."
    )

    def start_shutdown() -> None:
        if not schedule_stack_shutdown():
            create_task(shutdown_bot_process())

    send_with_hook = getattr(
        ctx,
        "send_with_post_delivery_hook",
        None,
    )
    if callable(send_with_hook):
        await send_with_hook(
            message,
            after_delivery=start_shutdown,
        )
        return
    await ctx.send(message)
    start_shutdown()


def resolve_opus_runtime_value() -> Any:
    try:
        from evelyn_voice.client import OPUS_ERROR_TO_SILENCE as opus_runtime_value
    except Exception:
        opus_runtime_value = None
    return opus_runtime_value


async def handle_status_command(
    ctx: Any,
    *,
    build_reply: Any,
    model_name: str,
    router_model_name: str,
    summary_model_name: str,
    stt_model_name: str,
    voice_debug_save_audio: bool,
    vad_enabled: bool,
    vad_provider: str,
    opus_runtime_value: Any = None,
) -> None:
    guild = ctx.guild
    vc = guild.voice_client if guild else None
    voice_channel_name = getattr(getattr(vc, "channel", None), "name", None) or "없음"
    listening = bool(vc and hasattr(vc, "is_listening") and vc.is_listening())
    await ctx.send(
        build_reply(
            model_name=model_name,
            router_model_name=router_model_name,
            summary_model_name=summary_model_name,
            stt_model_name=stt_model_name,
            voice_channel_name=voice_channel_name,
            listening=listening,
            voice_debug_save_audio=voice_debug_save_audio,
            opus_env_state=os.getenv("OPUS_ERROR_TO_SILENCE"),
            opus_runtime_value=resolve_opus_runtime_value() if opus_runtime_value is None else opus_runtime_value,
            vad_enabled=vad_enabled,
            vad_provider=vad_provider,
        )
    )


async def handle_evelyn_page_command(
    ctx: Any,
    *,
    resolve_page_url: Any,
) -> None:
    page_url = resolve_page_url()
    if not page_url:
        await ctx.send("아직 공개 이블린 페이지 URL을 못 찾았어. EVELYN_PAGE_URL을 설정하거나 GitHub Pages 배포를 먼저 붙여줘.")
        return
    await ctx.send(f"이블린 페이지: {page_url}")


async def handle_reset_guild_memory_command(
    ctx: Any,
    *,
    reset_guild_runtime_state: Any,
    get_guild_command_prefix: Any,
    build_reply: Any,
    guild_only_message: Any,
) -> None:
    if ctx.guild is None:
        await ctx.send(guild_only_message())
        return

    guild_id = ctx.guild.id
    current_prefix = get_guild_command_prefix(guild_id)

    try:
        reset_guild_runtime_state(guild_id)
    except RuntimeError as exc:
        error_code = str(exc)
        if error_code in _MEMORY_GUILD_RESET_FAILURE_CODES:
            await ctx.send(
                "기억을 안전하게 초기화하지 못했어. 잠시 뒤 다시 시도해줘. "
                "계속되면 기억 관리에서 예전 확인 기억을 직접 삭제한 뒤 다시 시도해줘."
            )
            return
        if error_code not in {
            AUTONOMY_COGNITIVE_REFRESH_INFLIGHT,
            AUTONOMY_RUNTIME_ACTIVE,
            MEMORY_BACKGROUND_WORK_INFLIGHT,
            SEARCH_BACKGROUND_WORK_INFLIGHT,
        }:
            raise
        await ctx.send(
            "자율 행동을 먼저 끈 뒤 다시 시도해줘."
            if error_code == AUTONOMY_RUNTIME_ACTIVE
            else "기억 정리 작업이 끝나는 중이야. 잠깐 뒤에 다시 시도해줘."
        )
        return
    refresh_epoch = getattr(ctx, "refresh_ingress_epoch", None)
    if callable(refresh_epoch):
        refresh_epoch()
    await ctx.send(build_reply(guild_name=ctx.guild.name, current_prefix=current_prefix))


async def handle_minecraft_connect_command(
    ctx: Any,
    *,
    enable_minecraft_mode: Any,
    enable_minecraft_autonomy_route: Any,
    build_reply: Any,
    guild_only_message: Any,
    record_runtime_error: Any = None,
    archive_minecraft_command: Any = None,
    archive_required: bool = False,
    log: Any = print,
) -> None:
    if ctx.guild is None:
        await ctx.send(guild_only_message())
        return
    try:
        command_root_id = await _archive_minecraft_command_root(
            ctx,
            archive_minecraft_command=archive_minecraft_command,
            archive_required=archive_required,
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        if record_runtime_error is not None:
            record_runtime_error("conversation_archive_minecraft_command_failed", exc)
        await ctx.send(
            "❌ 마인크래프트 명령 기록을 안전하게 시작하지 못해 접속을 실행하지 않았어."
        )
        return
    typing_stack = contextlib.AsyncExitStack()
    with contextlib.suppress(Exception):
        await typing_stack.enter_async_context(ctx.typing())
    try:
        try:
            enable_kwargs = {
                "issuer_ref": (
                    f"discord_user:{getattr(ctx.author, 'id', '')}"
                ),
                "source": "discord_command",
            }
            if command_root_id is not None:
                enable_kwargs["parent_record_ids"] = (command_root_id,)
            observed = await enable_minecraft_mode(
                ctx.guild.id,
                **enable_kwargs,
            )
            if (
                isinstance(observed, dict)
                and observed.get("outcome_verified") is True
                and observed.get("outcome_code")
                == MINECRAFT_CONNECTED_OUTCOME
                and minecraft_connection_confirmed(observed)
            ):
                route_enabled = await enable_minecraft_autonomy_route(
                    ctx.guild.id
                )
                if route_enabled is not True:
                    raise RuntimeError(
                        "minecraft_autonomy_route_enable_failed"
                    )
            reply_text = build_reply(observed)
        except Exception as exc:
            if record_runtime_error is not None:
                record_runtime_error("minecraft_connect_failed", exc)
            log("마인크래프트 접속 오류 type=", type(exc).__name__)
            reply_text = public_failure_message(
                "minecraft_connect_failed"
            )
    finally:
        with contextlib.suppress(Exception):
            await typing_stack.aclose()
    await ctx.send(reply_text)


async def handle_minecraft_disconnect_command(
    ctx: Any,
    *,
    disable_minecraft_mode: Any,
    disable_minecraft_autonomy_route: Any,
    guild_only_message: Any,
    record_runtime_error: Any = None,
    archive_minecraft_command: Any = None,
    archive_required: bool = False,
    log: Any = print,
) -> None:
    if ctx.guild is None:
        await ctx.send(guild_only_message())
        return
    try:
        command_root_id = await _archive_minecraft_command_root(
            ctx,
            archive_minecraft_command=archive_minecraft_command,
            archive_required=archive_required,
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        if record_runtime_error is not None:
            record_runtime_error(
                "conversation_archive_minecraft_command_failed",
                exc,
            )
        await ctx.send(
            "❌ 마인크래프트 명령 기록을 안전하게 시작하지 못해 종료를 실행하지 않았어."
        )
        return
    try:
        await disable_minecraft_autonomy_route(ctx.guild.id)
    except Exception as exc:
        log(
            "마인크래프트 자율 경로 비활성화 오류 type=",
            type(exc).__name__,
        )
    try:
        disable_kwargs = {}
        if command_root_id is not None:
            disable_kwargs["parent_record_ids"] = (command_root_id,)
        stopped = await disable_minecraft_mode(
            ctx.guild.id,
            **disable_kwargs,
        )
        if not (
            isinstance(stopped, dict)
            and stopped.get("outcome_verified") is True
            and stopped.get("outcome_code") == MINECRAFT_STOPPED_OUTCOME
            and minecraft_stop_confirmed(stopped)
        ):
            raise RuntimeError("minecraft_stop_unverified")
        reply_text = "🛑 Voyager 기반 마인크래프트 자율 모드를 중지했어."
        await ctx.send(reply_text)
    except Exception as exc:
        log("마인크래프트 연결 종료 오류 type=", type(exc).__name__)
        reply_text = public_failure_message(
            "minecraft_disconnect_failed"
        )
        await ctx.send(reply_text)


async def handle_minecraft_status_command(
    ctx: Any,
    *,
    get_minecraft_client: Any,
    get_minecraft_world_lease_status: Any,
    build_reply: Any,
    guild_only_message: Any,
    log: Any = print,
) -> None:
    if ctx.guild is None:
        await ctx.send(guild_only_message())
        return
    client = get_minecraft_client()
    try:
        status = await client.status()
        payload = dict(status) if isinstance(status, dict) else {}
        payload["world_lease"] = (
            get_minecraft_world_lease_status()
        )
        reply_text = build_reply(payload)
        await ctx.send(reply_text)
    except Exception as exc:
        log("마인크래프트 상태 확인 오류 type=", type(exc).__name__)
        reply_text = public_failure_message(
            "minecraft_status_failed"
        )
        await ctx.send(reply_text)


async def handle_minecraft_goal_command(
    ctx: Any,
    *,
    goal: str | None,
    set_minecraft_goal: Any,
    build_missing_reply: Any,
    build_updated_reply: Any,
    guild_only_message: Any,
    record_runtime_error: Any = None,
    archive_minecraft_command: Any = None,
    archive_required: bool = False,
    log: Any = print,
) -> None:
    if ctx.guild is None:
        await ctx.send(guild_only_message())
        return
    goal_text = clean_text(str(goal or ""))
    if not goal_text:
        reply_text = build_missing_reply(
            str(getattr(ctx, "prefix", None) or "!")
        )
        await ctx.send(reply_text)
        return
    try:
        command_root_id = await _archive_minecraft_command_root(
            ctx,
            archive_minecraft_command=archive_minecraft_command,
            archive_required=archive_required,
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        if record_runtime_error is not None:
            record_runtime_error("conversation_archive_minecraft_command_failed", exc)
        await ctx.send(
            "❌ 마인크래프트 명령 기록을 안전하게 시작하지 못해 목표를 바꾸지 않았어."
        )
        return
    try:
        goal_kwargs = {}
        if command_root_id is not None:
            goal_kwargs["parent_record_ids"] = (command_root_id,)
        status = await set_minecraft_goal(
            ctx.guild.id,
            goal_text,
            **goal_kwargs,
        )
        reply_text = build_updated_reply(goal_text, status)
        await ctx.send(reply_text)
    except Exception as exc:
        log("마인크래프트 목표 변경 오류 type=", type(exc).__name__)
        reply_text = public_failure_message(
            "minecraft_goal_failed"
        )
        await ctx.send(reply_text)


async def _archive_minecraft_command_root(
    ctx: Any,
    *,
    archive_minecraft_command: Any,
    archive_required: bool,
) -> str | None:
    if archive_minecraft_command is None:
        if archive_required is True:
            raise RuntimeError("archive_minecraft_command_unavailable")
        return None
    message = getattr(ctx, "message", None)
    channel = getattr(ctx, "channel", None)
    author = getattr(ctx, "author", None)
    guild = getattr(ctx, "guild", None)
    created_at = getattr(message, "created_at", None)
    if any(value is None for value in (guild, channel, author, message)):
        raise RuntimeError("archive_minecraft_command_context_missing")
    receipt = await _maybe_await(
        archive_minecraft_command(
            guild_id=int(guild.id),
            channel_id=int(channel.id),
            user_id=int(author.id),
            owner_name=str(
                getattr(author, "display_name", None)
                or getattr(author, "global_name", None)
                or getattr(author, "name", None)
                or author.id
            ),
            message_id=int(message.id),
            authored_at=float(
                created_at.timestamp()
                if created_at is not None
                else time.time()
            ),
            text=str(getattr(message, "content", "") or ""),
        )
    )
    if not isinstance(receipt, dict) or not str(receipt.get("recordId") or ""):
        raise RuntimeError("archive_minecraft_command_receipt_invalid")
    return str(receipt["recordId"])


def make_control_command_authorized_checker(*, allowed_user_ids: set[int] | frozenset[int]) -> Any:
    allowed = set(allowed_user_ids)

    def is_authorized(ctx: Any) -> bool:
        perms = getattr(ctx.author, "guild_permissions", None)
        return is_control_command_authorized_payload(
            author_id=getattr(ctx.author, "id", None),
            is_administrator=bool(perms and getattr(perms, "administrator", False)),
            allowed_user_ids=allowed,
        )

    return is_authorized


async def handle_control_command_error(ctx: Any, error: BaseException) -> None:
    if isinstance(error, commands.CheckFailure):
        await ctx.send(control_command_check_failure_message())
        return
    if isinstance(error, commands.UserInputError):
        prefix = str(getattr(ctx, "prefix", None) or "!")
        command = getattr(ctx, "command", None)
        name = str(getattr(command, "name", None) or "도움말")
        await ctx.send(
            f"명령 형식이 맞지 않아. 사용법: `{prefix}{name}`"
        )
        return
    raise error


async def handle_discord_command_error(
    ctx: Any,
    error: BaseException,
) -> None:
    if not isinstance(error, commands.CommandNotFound):
        raise error
    invoked_with = clean_text(
        str(getattr(ctx, "invoked_with", None) or "")
    ).lower()
    if invoked_with not in {
        "minecraft",
        "mc",
        "마인크래프트",
        "마크",
    }:
        return
    prefix = str(getattr(ctx, "prefix", None) or "!")
    await ctx.send(
        "Minecraft 접속 명령은 띄어쓰지 않고 입력해줘. "
        f"사용법: `{prefix}minecraft-connect` 또는 "
        f"`{prefix}마크접속`"
    )


__all__ = [
    "ARCHIVE_COMMAND_PAGE_LIMIT",
    "ARCHIVE_DELETE_CONFIRM_SECONDS",
    "DISCORD_SHARED_ARCHIVE_NOTICE",
    "RecordDeletionConfirmationGuard",
    "RecordDeletionConfirmationView",
    "handle_autonomy_start_command",
    "handle_autonomy_status_command",
    "handle_autonomy_stop_command",
    "handle_channel_setting_command",
    "handle_evelyn_page_command",
    "handle_feedback_application_command",
    "handle_join_voice_command",
    "handle_leave_voice_command",
    "handle_minecraft_connect_command",
    "handle_minecraft_disconnect_command",
    "handle_minecraft_goal_command",
    "handle_minecraft_status_command",
    "handle_prefix_command",
    "handle_rejoin_voice_command",
    "handle_record_consent_application_command",
    "handle_record_delete_application_command",
    "handle_record_view_application_command",
    "handle_control_command_error",
    "handle_discord_command_error",
    "handle_reset_guild_memory_command",
    "handle_restart_bot_command",
    "handle_shutdown_bot_command",
    "handle_status_command",
    "make_control_command_authorized_checker",
    "resolve_opus_runtime_value",
]
