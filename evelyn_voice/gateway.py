from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from .dave_session import DaveSession
from .state import VoiceRuntimeState

log = logging.getLogger(__name__)

class VoiceGateway:
    def __init__(self, state: VoiceRuntimeState, dave: DaveSession):
        self.state = state
        self.dave = dave
        self._task: asyncio.Task | None = None
        self._closed = False
        self.ws: Any = None
        self._pending_dave_frames: list[tuple[str, Any]] = []
        self._replaying_pending_dave = False

    def _sync_dave_runtime_state(self) -> None:
        self.state.dave_ready = self.dave.ready
        self.state.dave_status = str(self.dave.status)

    async def _send_ws_json(self, payload: dict) -> None:
        if self.ws is None:
            log.warning("VOICE SEND JSON skipped | ws is None | payload=%r", payload)
            return

        sender = getattr(self.ws, "send_as_json", None)
        if sender is None:
            log.warning("VOICE SEND JSON skipped | send_as_json missing | payload=%r", payload)
            return

        await sender(payload)

    async def _send_ws_binary(self, opcode: int, payload: bytes) -> None:
        if self.ws is None:
            log.warning("VOICE SEND BINARY skipped | ws is None | opcode=%s", opcode)
            return

        sender = getattr(self.ws, "send_binary", None)
        if sender is None:
            log.warning("VOICE SEND BINARY skipped | send_binary missing | opcode=%s", opcode)
            return

        await sender(opcode, payload)

    async def _send_transition_ready(self, transition_id: int) -> None:
        if self.ws is None:
            log.warning("VOICE TRANSITION READY skipped | ws is None | transition_id=%s", transition_id)
            return

        sender = getattr(self.ws, "send_transition_ready", None)
        if sender is not None:
            await sender(transition_id)
            return

        await self._send_ws_json(
            {
                "op": 23,
                "d": {
                    "transition_id": transition_id,
                },
            }
        )

    async def _maybe_send_key_package(self, epoch: int | None) -> None:
        if epoch != 1:
            return

        creator = getattr(self.dave, "create_key_package", None)
        if creator is None:
            log.warning("DAVE KEY_PACKAGE skipped | dave_session.create_key_package() missing")
            return

        payload = creator()
        if asyncio.iscoroutine(payload):
            payload = await payload

        if not payload:
            log.warning("DAVE KEY_PACKAGE skipped | empty payload")
            return

        if not isinstance(payload, (bytes, bytearray)):
            log.warning("DAVE KEY_PACKAGE skipped | payload type=%s", type(payload).__name__)
            return

        await self._send_ws_binary(26, bytes(payload))
        log.info("DAVE KEY_PACKAGE sent | size=%d", len(payload))

    def try_apply_pending_dave(self) -> None:
        self.state.dave_apply_attempts += 1

        if self.state.external_sender_data is not None:
            try:
                raw = self.state.external_sender_data
                payload = raw.get("external_sender", raw) if isinstance(raw, dict) else raw
                self.dave.set_external_sender(payload)
                log.info("DAVE APPLY | external_sender applied")
                self.state.external_sender_data = None
            except Exception as e:
                self.state.last_dave_apply_error = f"external_sender: {e!r}"
                log.warning("DAVE APPLY failed | external_sender | err=%r raw=%r", e, raw)

        if self.state.pending_proposals is not None:
            try:
                raw = self.state.pending_proposals

                if isinstance(raw, dict):
                    operation_type = (
                        raw.get("operation_type")
                        or raw.get("operationType")
                        or raw.get("type")
                        or raw.get("op_type")
                    )
                    proposals = (
                        raw.get("proposals")
                        or raw.get("items")
                        or raw.get("proposal_list")
                        or raw
                    )
                    expected_user_ids = raw.get("expected_user_ids") or raw.get("expectedUserIds")

                    if isinstance(expected_user_ids, list):
                        parsed_ids = []
                        for uid in expected_user_ids:
                            try:
                                parsed_ids.append(int(uid))
                            except Exception:
                                continue
                        expected_user_ids = parsed_ids
                else:
                    operation_type = None
                    proposals = raw
                    expected_user_ids = None

                self.dave.process_proposals(operation_type, proposals, expected_user_ids)
                log.info("DAVE APPLY | proposals applied")
                self.state.pending_proposals = None
            except Exception as e:
                self.state.last_dave_apply_error = f"proposals: {e!r}"
                log.warning("DAVE APPLY failed | proposals | err=%r raw=%r", e, raw)

        if self.state.pending_commit_welcome is not None:
            try:
                raw = self.state.pending_commit_welcome
                payload = raw.get("commit", raw) if isinstance(raw, dict) else raw
                self.dave.process_commit(payload)
                log.info("DAVE APPLY | commit applied")
                self.state.pending_commit_welcome = None
            except Exception as e:
                self.state.last_dave_apply_error = f"commit: {e!r}"
                log.warning("DAVE APPLY failed | commit | err=%r raw=%r", e, raw)

        if self.state.pending_welcome is not None:
            try:
                raw = self.state.pending_welcome
                payload = raw.get("welcome", raw) if isinstance(raw, dict) else raw
                self.dave.process_welcome(payload)
                log.info("DAVE APPLY | welcome applied")
                self.state.pending_welcome = None
            except Exception as e:
                self.state.last_dave_apply_error = f"welcome: {e!r}"
                log.warning("DAVE APPLY failed | welcome | err=%r raw=%r", e, raw)

        self._sync_dave_runtime_state()
        log.info(
            "DAVE APPLY SUMMARY | ready=%s status=%s attempts=%s last_err=%r",
            self.state.dave_ready,
            self.state.dave_status,
            self.state.dave_apply_attempts,
            self.state.last_dave_apply_error,
        )

    async def connect(self) -> None:
        log.info(
            "VoiceGateway connect requested | guild=%s channel=%s endpoint=%s",
            self.state.guild_id,
            self.state.channel_id,
            self.state.endpoint,
        )
        self.state.ws_connected.set()

    def _base_dave_session(self) -> Any:
        if self.ws is None:
            return None
        connection = getattr(self.ws, "_connection", None)
        return getattr(connection, "dave_session", None)

    def _should_buffer_json_dave(self, parsed: Any) -> bool:
        if not isinstance(parsed, dict):
            return False
        op = parsed.get("op")
        return op in (22, 24)

    def _should_buffer_binary_dave(self, msg: bytes) -> bool:
        return len(msg) >= 3 and msg[2] in (25, 27, 28, 29, 30)

    async def _replay_pending_dave_frames(self, original_received_message, original_received_binary_message) -> None:
        if self._replaying_pending_dave:
            return
        if not self._pending_dave_frames:
            return
        if self._base_dave_session() is None:
            return

        self._replaying_pending_dave = True
        try:
            pending = self._pending_dave_frames
            self._pending_dave_frames = []
            for frame_kind, frame_payload in pending:
                if frame_kind == "json":
                    await original_received_message(frame_payload)
                else:
                    if original_received_binary_message is not None:
                        await original_received_binary_message(frame_payload)
        finally:
            self._replaying_pending_dave = False

    def bind_ws(self, ws: Any) -> None:
        self.ws = ws

        if ws is None:
            return

        if getattr(ws, "_evelyn_gateway_hooked", False):
            return

        original_received_message = getattr(ws, "received_message", None)
        if original_received_message is None:
            log.warning("VoiceGateway.bind_ws could not find received_message on ws=%r", ws)
            return
        original_received_binary_message = getattr(ws, "received_binary_message", None)

        if original_received_binary_message is not None:
            async def tapped_received_binary_message(msg) -> None:
                if (
                    not self._replaying_pending_dave
                    and isinstance(msg, (bytes, bytearray))
                    and self._base_dave_session() is None
                    and self._should_buffer_binary_dave(bytes(msg))
                ):
                    self._pending_dave_frames.append(("binary", bytes(msg)))

                result = await original_received_binary_message(msg)

                try:
                    if isinstance(msg, (bytes, bytearray)):
                        await self._handle_voice_binary_message(bytes(msg))
                    await self._replay_pending_dave_frames(original_received_message, original_received_binary_message)
                except Exception as e:
                    log.warning("VOICE BINARY hook error | err=%r", e)

                return result

            ws.received_binary_message = tapped_received_binary_message
        else:
            log.warning("VoiceGateway.bind_ws could not find received_binary_message on ws=%r", ws)

        async def tapped_received_message(msg) -> None:
            parsed = None

            if isinstance(msg, dict):
                parsed = msg
            elif isinstance(msg, str):
                try:
                    parsed = json.loads(msg)
                except Exception:
                    parsed = None
            elif isinstance(msg, (bytes, bytearray)):
                try:
                    parsed = json.loads(msg.decode("utf-8"))
                except Exception:
                    parsed = None

            if (
                not self._replaying_pending_dave
                and parsed is not None
                and self._base_dave_session() is None
                and self._should_buffer_json_dave(parsed)
            ):
                self._pending_dave_frames.append(("json", parsed))

            result = await original_received_message(msg)

            try:
                if isinstance(msg, (bytes, bytearray)) and parsed is None:
                    await self._handle_voice_binary_message(bytes(msg))

                if isinstance(parsed, dict):
                    await self.handle_voice_payload(parsed)

                await self._replay_pending_dave_frames(original_received_message, original_received_binary_message)
            except Exception as e:
                log.warning("VOICE JSON hook error | err=%r", e)

            return result

        ws.received_message = tapped_received_message
        ws._evelyn_gateway_hooked = True

    async def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        try:
            while not self._closed:
                await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            pass

    async def _handle_voice_binary_message(self, msg: bytes) -> None:
        if len(msg) < 3:
            log.warning("VOICE BINARY too short | len=%d", len(msg))
            return

        seq = int.from_bytes(msg[0:2], "big")
        op = msg[2]
        payload = msg[3:]

        self.state.last_voice_ws_op = op
        self.state.last_voice_ws_payload = {
            "binary": True,
            "seq": seq,
            "payload_len": len(payload),
        }
        setattr(self.state, "last_server_seq", seq)

        await self._handle_voice_binary_opcode(op, payload)


    async def _handle_voice_binary_opcode(self, op: int, payload: bytes) -> None:
        if op in (22, 24, 25, 27, 28, 29, 30):
            return

    async def handle_voice_payload(self, payload: dict) -> None:
        if not isinstance(payload, dict):
            return

        op = payload.get("op")
        data = payload.get("d")
        seq = payload.get("seq")

        if isinstance(seq, int):
            setattr(self.state, "last_server_seq", seq)

        self.state.last_voice_ws_op = op
        self.state.last_voice_ws_payload = data if isinstance(data, dict) else {"raw": data}

        if op == 5 and isinstance(data, dict):
            user_id = data.get("user_id")
            ssrc = data.get("ssrc")
            speaking = data.get("speaking")

            if user_id is not None and ssrc is not None:
                try:
                    user_id_i = int(user_id)
                    ssrc_i = int(ssrc)
                    self.state.bind_ssrc(user_id_i, ssrc_i)
                    self.state.set_current_speaking(user_id_i, ssrc_i)
                    log.info(
                        "VOICE MAP SPEAKING | user_id=%s ssrc=%s speaking=%s",
                        user_id_i,
                        ssrc_i,
                        speaking,
                    )
                except Exception as e:
                    log.warning("VOICE SPEAKING map failed | data=%r err=%r", data, e)
            else:
                log.info("VOICE SPEAKING | data=%r", data)
            return

        if op == 11 and isinstance(data, dict):
            user_ids = data.get("user_ids") or []
            parsed_ids = []
            if isinstance(user_ids, list):
                for uid in user_ids:
                    try:
                        parsed_ids.append(int(uid))
                    except Exception:
                        continue
            self.state.pending_user_ids = parsed_ids
            log.info("VOICE CLIENTS_CONNECT | data=%r", data)
            return

        if op == 12 and isinstance(data, dict):
            user_id = data.get("user_id")
            audio_ssrc = data.get("audio_ssrc", data.get("ssrc"))

            if user_id is not None and audio_ssrc is not None:
                try:
                    self.state.bind_ssrc(int(user_id), int(audio_ssrc))
                    log.info("VOICE MAP | user_id=%s ssrc=%s", user_id, audio_ssrc)
                except Exception as e:
                    log.warning("VOICE MAP failed | data=%r err=%r", data, e)
            else:
                log.info("VOICE CLIENT_CONNECT | data=%r", data)
            return

        if op == 13 and isinstance(data, dict):
            log.info("VOICE CLIENT_DISCONNECT | data=%r", data)
            try:
                uid = data.get("user_id")
                if uid is not None and self.state.current_speaking_user_id == int(uid):
                    self.state.set_current_speaking(None, None)
            except Exception:
                pass
            return

        if op == 6 and isinstance(data, dict):
            return

        if op == 8 and isinstance(data, dict):
            return
        if op in (22, 24, 25, 27, 28, 29, 30):
            return

    async def close(self) -> None:
        self._closed = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None