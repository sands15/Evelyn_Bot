from __future__ import annotations

import asyncio
import logging
import os
import struct
from collections import deque

import davey
import discord
from discord.opus import Decoder
from nacl.bindings import crypto_aead_xchacha20poly1305_ietf_decrypt

from .dave_session import DaveSession
from .gateway import VoiceGateway
from .sink import AudioSink, NullSink
from .state import VoiceRuntimeState
from .udp import VoiceUDPTransport


async def on_user_audio(member, pcm_bytes: bytes):
    return None


log = logging.getLogger(__name__)


def _parse_rtp_header(packet: bytes):
    if len(packet) < 12:
        return None

    b0, b1 = packet[0], packet[1]
    version = (b0 >> 6) & 0b11
    cc = b0 & 0x0F
    x = (b0 >> 4) & 0x01
    marker = (b1 >> 7) & 0x01
    payload_type = b1 & 0x7F
    sequence = struct.unpack_from(">H", packet, 2)[0]
    timestamp = struct.unpack_from(">I", packet, 4)[0]
    ssrc = struct.unpack_from(">I", packet, 8)[0]

    base_header_len = 12 + (cc * 4)
    unencrypted_header_len = base_header_len
    full_header_len = base_header_len

    if x:
        if len(packet) < base_header_len + 4:
            return None

        unencrypted_header_len = base_header_len + 4
        ext_len_words = struct.unpack_from(">H", packet, base_header_len + 2)[0]
        full_header_len = base_header_len + 4 + (ext_len_words * 4)

    if len(packet) < full_header_len:
        return None

    return {
        "version": version,
        "payload_type": payload_type,
        "marker": marker,
        "sequence": sequence,
        "timestamp": timestamp,
        "ssrc": ssrc,
        "header_len": full_header_len,
        "unencrypted_header_len": unencrypted_header_len,
    }


class EvelynVoiceClient(discord.VoiceClient):

    def _dump_frame_probe(
        self,
        *,
        kind: str,
        idx: int,
        group_index: int,
        ts: int,
        first_seq: int,
        last_seq: int,
        packet_count: int,
        frame_bytes: bytes,
        note: str = "",
    ) -> None:
        return

    def _sync_dave_from_base(self) -> None:
        base_conn = getattr(self, "_connection", None)
        base_dave = getattr(base_conn, "dave_session", None)

        if base_dave is None:
            log.info("DAVE BASE SYNC | no base dave_session yet")
            return

        if self.dave.session is not base_dave:
            self.dave.session = base_dave
            self.dave.own_user_id = int(self.client.user.id) if self.client.user else None
            self.dave.channel_id = int(self.channel.id)
            log.info("DAVE BASE SYNC | adopted base dave_session object")

        # custom wrapper 상태 갱신
        self.dave._refresh_state("sync_from_base")

        self.runtime.dave_ready = self.dave.ready
        self.runtime.dave_status = str(self.dave.status)
        self.runtime.dave_epoch = self.dave.epoch
        self.runtime.dave_protocol_version = self.dave.protocol_version

        log.info(
            "DAVE BASE SYNC | ready=%s status=%r epoch=%r proto=%r",
            self.dave.ready,
            self.dave.status,
            self.dave.epoch,
            self.dave.protocol_version,
        )

    def __init__(self, client: discord.Client, channel: discord.abc.Connectable):
        super().__init__(client, channel)

        self.runtime = VoiceRuntimeState(
            guild_id=getattr(channel.guild, "id", None),
            channel_id=getattr(channel, "id", None),
        )
        self.dave_inner_fail_log_count = 0
        self.dave_inner_fail_log_limit = 3

        self.dave = DaveSession()
        self.gateway = VoiceGateway(self.runtime, self.dave)
        self.opus_decoder = Decoder()

        self.udp_transport: VoiceUDPTransport | None = None
        self.sink: AudioSink | None = None
        self.on_user_audio = on_user_audio

        self._receive_task: asyncio.Task | None = None
        self._decrypt_task: asyncio.Task | None = None
        self._utterance_task: asyncio.Task | None = None

        self.media_queue: asyncio.Queue = asyncio.Queue(maxsize=2000)
        self.media_packet_count = 0
        self.decrypt_packet_count = 0

        self.end_silence_sec = 0.45
        self.voice_payload_threshold = 60
        self.preroll_packet_limit = max(0, int(round(float(os.getenv("VOICE_PREROLL_MS", "400")) / 20.0)))

        self.utterance_states: dict[int, dict] = {}
        self.utterance_count = 0
        self.utterance_queue: asyncio.Queue = asyncio.Queue(maxsize=32)

    def _decrypt_standard_voice_packet(self, packet_bytes: bytes) -> tuple[bytes, dict] | None:
        info = _parse_rtp_header(packet_bytes)
        if info is None:
            return None

        mode = self.runtime.voice_mode
        key = self.runtime.voice_secret_key

        if mode != "aead_xchacha20_poly1305_rtpsize":
            return None
        if not key:
            return None

        encrypted = packet_bytes[info["unencrypted_header_len"]:]
        if len(encrypted) < 4:
            return None

        nonce_suffix = encrypted[-4:]
        ciphertext = encrypted[:-4]

        nonce = bytearray(24)
        nonce[:4] = nonce_suffix
        nonce = bytes(nonce)

        aad_candidates = [packet_bytes[:12]]

        unenc_header_len = info["unencrypted_header_len"]
        if unenc_header_len > 12:
            aad_candidates.append(packet_bytes[:unenc_header_len])

        decrypted_extension_len = max(0, info["header_len"] - info["unencrypted_header_len"])

        for aad in aad_candidates:
            try:
                plaintext = crypto_aead_xchacha20poly1305_ietf_decrypt(
                    ciphertext,
                    aad,
                    nonce,
                    key,
                )
                if decrypted_extension_len:
                    if len(plaintext) < decrypted_extension_len:
                        log.warning(
                            "STD DECRYPT ext underflow | ssrc=%s seq=%s ts=%s plain_len=%d ext_len=%d",
                            info["ssrc"],
                            info["sequence"],
                            info["timestamp"],
                            len(plaintext),
                            decrypted_extension_len,
                        )
                        return None
                    plaintext = plaintext[decrypted_extension_len:]
                return plaintext, info
            except Exception:
                pass

        log.warning(
            "STD DECRYPT failed | ssrc=%s seq=%s ts=%s mode=%s header_len=%s unenc_header_len=%s enc_len=%s",
            info["ssrc"],
            info["sequence"],
            info["timestamp"],
            mode,
            info["header_len"],
            info["unencrypted_header_len"],
            len(encrypted),
        )
        return None

    async def connect(
        self,
        *,
        timeout: float,
        reconnect: bool,
        self_deaf: bool = False,
        self_mute: bool = False,
    ) -> None:
        log.info(
            "EvelynVoiceClient.connect() called | timeout=%s reconnect=%s",
            timeout,
            reconnect,
        )

        self.dave.init_session(
            user_id=int(self.client.user.id),
            channel_id=int(self.channel.id),
        )
        self.runtime.dave_protocol_version = self.dave.protocol_version
        self.runtime.dave_ready = self.dave.ready
        self.runtime.dave_status = str(self.dave.status)

        connect_task = asyncio.create_task(
            super().connect(
                timeout=timeout,
                reconnect=reconnect,
                self_deaf=self_deaf,
                self_mute=self_mute,
            )
        )

        while not connect_task.done():
            ws = getattr(self, "ws", None)
            if (
                ws is not None
                and hasattr(ws, "received_message")
                and not getattr(ws, "_evelyn_gateway_hooked", False)
            ):
                self.gateway.bind_ws(ws)
            await asyncio.sleep(0.01)

        await connect_task

        self.runtime.endpoint = getattr(self, "endpoint", None)
        self.runtime.session_id = getattr(self, "session_id", None)
        self.runtime.token = getattr(self, "token", None)

        # SESSION_DESCRIPTION 백필
        ws_secret = getattr(self.ws, "secret_key", None)
        vc_mode = getattr(self, "mode", None)
        vc_secret = getattr(self, "secret_key", None)

        if self.runtime.voice_mode is None and vc_mode:
            self.runtime.voice_mode = vc_mode

        if self.runtime.voice_secret_key is None:
            if isinstance(vc_secret, (bytes, bytearray)) and vc_secret:
                self.runtime.voice_secret_key = bytes(vc_secret)
            elif isinstance(ws_secret, list) and ws_secret:
                try:
                    self.runtime.voice_secret_key = bytes(int(x) & 0xFF for x in ws_secret)
                except Exception as e:
                    log.warning("CONNECT BACKFILL | ws.secret_key parse failed | err=%r", e)

        log.info(
            "CONNECT BACKFILL | mode=%r key_len=%s ws_secret=%s",
            self.runtime.voice_mode,
            len(self.runtime.voice_secret_key) if self.runtime.voice_secret_key else None,
            isinstance(ws_secret, list) and len(ws_secret) or None,
        )

        self.gateway.bind_ws(self.ws)

        await self.gateway.connect()

        await self.gateway.start()

        # TEMP: custom gateway apply 대신 base discord dave_session 기준으로 동기화
        self._sync_dave_from_base()
        self._enable_dave_passthrough()

        self.gateway.try_apply_pending_dave()

        log.info(
            "Base voice connected | endpoint=%s session_id=%s ssrc=%s",
            self.runtime.endpoint,
            self.runtime.session_id,
            getattr(self, "ssrc", None),
        )

        base_sock = self._find_base_udp_socket()
        log.info("Base UDP socket found: %r", base_sock)

        if base_sock is None:
            log.warning("Could not find base discord.py UDP socket")
        else:
            self.udp_transport = VoiceUDPTransport(base_sock)
            await self.udp_transport.open()
            self.runtime.udp_ready.set()

        log.info("EvelynVoiceClient ready | udp=%s", self.runtime.udp_ready.is_set())
    
    def _get_base_dave_session(self):
        base_conn = getattr(self, "_connection", None)
        return getattr(base_conn, "dave_session", None)

    def _enable_dave_passthrough(self) -> None:
        base_dave = self._get_base_dave_session()
        if base_dave is None:
            return

        try:
            base_dave.set_passthrough_mode(True, 10)
            log.info("DAVE passthrough enabled")
        except Exception as e:
            log.warning("DAVE passthrough enable failed | err=%r", e)

    def _dave_can_passthrough(self, user_id: int | None) -> bool:
        if user_id is None:
            return False

        base_dave = self._get_base_dave_session()
        if base_dave is None:
            return False

        checker = getattr(base_dave, "can_passthrough", None)
        if checker is None:
            return False

        try:
            return bool(checker(int(user_id)))
        except Exception:
            return False

    def _get_dave_decryption_stats(self, user_id: int | None) -> dict | None:
        if user_id is None:
            return None

        base_dave = self._get_base_dave_session()
        if base_dave is None:
            return None

        getter = getattr(base_dave, "get_decryption_stats", None)
        if getter is None:
            return None

        try:
            stats = getter(int(user_id), davey.MediaType.audio)
        except Exception:
            return None

        if stats is None:
            return None

        return {
            "attempts": getattr(stats, "attempts", None),
            "successes": getattr(stats, "successes", None),
            "failures": getattr(stats, "failures", None),
            "passthroughs": getattr(stats, "passthroughs", None),
            "duration": getattr(stats, "duration", None),
        }

    def _candidate_dave_user_ids(self, primary_user_id: int) -> list[int]:
        candidates: list[int] = []

        def add(value) -> None:
            if value is None:
                return
            try:
                value_i = int(value)
            except Exception:
                return
            if value_i not in candidates:
                candidates.append(value_i)

        add(primary_user_id)
        add(self.runtime.current_speaking_user_id)

        for mapped_user_id in self.runtime.ssrc_to_user_id.values():
            add(mapped_user_id)

        try:
            for member in getattr(self.channel, "members", []):
                if not getattr(member, "bot", False):
                    add(member.id)
        except Exception:
            pass

        return candidates

    def _decrypt_dave_inner_packet(self, *, user_id: int, outer_plain: bytes) -> tuple[bytes | None, int | None]:
        if not outer_plain:
            return None, None

        # Discord Opus silence packet
        if outer_plain == b"\xF8\xFF\xFE":
            return outer_plain, user_id

        base_dave = self._get_base_dave_session()
        if base_dave is None:
            log.warning("DAVE INNER | no base dave_session")
            return None, None

        allow_passthrough = self._dave_can_passthrough(user_id)

        if not getattr(base_dave, "ready", False):
            return (outer_plain, user_id) if allow_passthrough else (None, None)

        try:
            decrypted = base_dave.decrypt(
                int(user_id),
                davey.MediaType.audio,
                bytes(outer_plain),
            )
            return decrypted, user_id
        except Exception as e:
            err_text = repr(e)
            log_allowed = self.dave_inner_fail_log_count < self.dave_inner_fail_log_limit

            if allow_passthrough and "UnencryptedWhenPassthroughDisabled" in err_text:
                if log_allowed:
                    log.info(
                        "DAVE INNER passthrough | user_id=%s in_len=%d prefix=%s",
                        user_id,
                        len(outer_plain),
                        outer_plain[:8].hex(),
                    )
                return outer_plain, user_id

            if "NoValidCryptorFound" in err_text:
                for candidate_user_id in self._candidate_dave_user_ids(user_id):
                    if candidate_user_id == int(user_id):
                        continue
                    try:
                        decrypted = base_dave.decrypt(
                            int(candidate_user_id),
                            davey.MediaType.audio,
                            bytes(outer_plain),
                        )
                        log.warning(
                            "DAVE INNER remap | old_user_id=%s new_user_id=%s in_len=%d prefix=%s",
                            user_id,
                            candidate_user_id,
                            len(outer_plain),
                            outer_plain[:8].hex(),
                        )
                        return decrypted, candidate_user_id
                    except Exception:
                        pass

            if log_allowed:
                log.warning(
                    "DAVE INNER failed | user_id=%s in_len=%d passthrough=%s prefix=%s stats=%r candidates=%r err=%r",
                    user_id,
                    len(outer_plain),
                    allow_passthrough,
                    outer_plain[:8].hex(),
                    self._get_dave_decryption_stats(user_id),
                    self._candidate_dave_user_ids(user_id),
                    e,
                )
            self.dave_inner_fail_log_count += 1
            return None, None

    def is_connected(self) -> bool:
        return super().is_connected()

    def is_listening(self) -> bool:
        return self._receive_task is not None and not self._receive_task.done()

    def listen(self, sink: AudioSink | None = None) -> None:
        if self.udp_transport is None:
            raise RuntimeError("UDP transport가 아직 준비되지 않았습니다. 먼저 join 상태를 확인하세요.")

        if sink is None:
            sink = NullSink()
        self.sink = sink

        if self._receive_task is None:
            self._receive_task = asyncio.create_task(self._receive_loop())

        if self._decrypt_task is None:
            self._decrypt_task = asyncio.create_task(self._decrypt_loop())

        if self._utterance_task is None:
            self._utterance_task = asyncio.create_task(self._utterance_loop())

        log.info("Receive loop started")

    async def _receive_loop(self) -> None:
        assert self.udp_transport is not None
        self.runtime.receive_ready.set()

        try:
            while True:
                packet = await self.udp_transport.recv_packet()
                info = _parse_rtp_header(packet)
                if info is None:
                    continue

                if info["payload_type"] != 120:
                    continue

                payload = packet[info["header_len"]:]

                packet_info = {
                    "raw_packet": packet,
                    "ssrc": info["ssrc"],
                    "sequence": info["sequence"],
                    "timestamp": info["timestamp"],
                    "payload_type": info["payload_type"],
                    "marker": info["marker"],
                    "header_len": info["header_len"],
                    "payload": payload,
                }

                try:
                    self.media_queue.put_nowait(packet_info)
                except asyncio.QueueFull:
                    try:
                        _ = self.media_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                    self.media_queue.put_nowait(packet_info)

                self.media_packet_count += 1
        except asyncio.CancelledError:
            pass
        finally:
            self.runtime.receive_ready.clear()

    async def _decrypt_loop(self) -> None:
        try:
            while True:
                now = asyncio.get_running_loop().time()

                try:
                    packet_info = await asyncio.wait_for(self.media_queue.get(), timeout=0.05)
                except asyncio.TimeoutError:
                    packet_info = None

                if packet_info is not None:
                    payload = packet_info["payload"]
                    ssrc = packet_info["ssrc"]
                    sequence = packet_info["sequence"]
                    timestamp = packet_info["timestamp"]
                    payload_len = len(payload)

                    current_packet = {
                        "raw_packet": packet_info.get("raw_packet"),
                        "ssrc": ssrc,
                        "sequence": sequence,
                        "timestamp": timestamp,
                        "payload": payload,
                    }

                    state = self.utterance_states.setdefault(
                        ssrc,
                        {
                            "in_utterance": False,
                            "last_voice_like_at": 0.0,
                            "packets": [],
                            "preroll": deque(maxlen=self.preroll_packet_limit),
                        },
                    )

                    if payload_len >= self.voice_payload_threshold:
                        state["last_voice_like_at"] = now
                        if not state["in_utterance"]:
                            state["in_utterance"] = True
                            state["packets"] = list(state["preroll"])
                            log.info(
                                "UTTERANCE START | ssrc=%d seq=%d ts=%d payload=%d preroll=%d",
                                ssrc,
                                sequence,
                                timestamp,
                                payload_len,
                                len(state["packets"]),
                            )

                    if state["in_utterance"]:
                        state["packets"].append(current_packet)

                    state["preroll"].append(current_packet)
                    self.decrypt_packet_count += 1

                now = asyncio.get_running_loop().time()

                for ssrc, state in list(self.utterance_states.items()):
                    if not state["in_utterance"]:
                        continue
                    if (now - state["last_voice_like_at"]) < self.end_silence_sec:
                        continue

                    state["in_utterance"] = False
                    self.utterance_count += 1

                    packet_count = len(state["packets"])
                    first_seq = state["packets"][0]["sequence"] if packet_count else -1
                    last_seq = state["packets"][-1]["sequence"] if packet_count else -1

                    log.info(
                        "UTTERANCE END | idx=%d ssrc=%d packets=%d first_seq=%d last_seq=%d gap=%.3f",
                        self.utterance_count,
                        ssrc,
                        packet_count,
                        first_seq,
                        last_seq,
                        now - state["last_voice_like_at"],
                    )

                    utterance_packets = state["packets"].copy()

                    try:
                        self.utterance_queue.put_nowait(
                            {
                                "idx": self.utterance_count,
                                "ssrc": ssrc,
                                "packets": utterance_packets,
                            }
                        )
                    except asyncio.QueueFull:
                        log.warning("utterance_queue is full, dropping utterance idx=%d ssrc=%d", self.utterance_count, ssrc)

                    state["packets"] = []

        except asyncio.CancelledError:
            pass

    async def _utterance_loop(self) -> None:
        try:
            while True:
                item = await self.utterance_queue.get()

                idx = item["idx"]
                ssrc = item["ssrc"]
                packets = item["packets"]

                packet_count = len(packets)
                first_seq = packets[0]["sequence"] if packet_count else -1
                last_seq = packets[-1]["sequence"] if packet_count else -1
                total_payload = sum(len(p["payload"]) for p in packets)

                await self._process_utterance_packets(item)
        except asyncio.CancelledError:
            pass

    async def _process_utterance_packets(self, item: dict) -> None:
        outer_fail = 0
        dave_fail = 0
        opus_fail = 0
        real_silence = 0
        self.dave_inner_fail_log_count = 0

        idx = item["idx"]
        ssrc = item["ssrc"]
        packets = item["packets"]
        dave_success = 0

        if not packets:
            return

        first_seq = packets[0]["sequence"]
        last_seq = packets[-1]["sequence"]
        total_payload = sum(len(p["payload"]) for p in packets)

        user_id = self.runtime.ssrc_to_user_id.get(ssrc)

        if user_id is None:
            try:
                human_members = [m for m in getattr(self.channel, "members", []) if not getattr(m, "bot", False)]
            except Exception:
                human_members = []

            if len(human_members) == 1:
                user_id = int(human_members[0].id)
                self.runtime.bind_ssrc(user_id, ssrc)
                log.info("VOICE MAP FALLBACK | user_id=%d ssrc=%d", user_id, ssrc)

        if user_id is None:
            log.warning("No user_id mapping yet for ssrc=%d; skipping idx=%d", ssrc, idx)
            return
        log.info("MAP DEBUG | idx=%d ssrc=%d user_id=%s", idx, ssrc, user_id)
        self._sync_dave_from_base()

        use_dave = bool(self.dave.ready)
        use_std = bool(
            self.runtime.voice_secret_key
            and self.runtime.voice_mode == "aead_xchacha20_poly1305_rtpsize"
        )

        if not use_dave and not use_std:
            log.warning(
                "No decrypt path yet; skipping idx=%d | dave_ready=%s mode=%r key=%s",
                idx,
                self.dave.ready,
                getattr(self.runtime, "voice_mode", None),
                bool(getattr(self.runtime, "voice_secret_key", None)),
            )
            return

        success = 0
        failed = 0
        pcm_chunks: list[bytes] = []
        SILENCE_PCM = b"\x00" * (960 * 2 * 2)

        for packet_index, p in enumerate(packets, start=1):
            raw_packet = p.get("raw_packet")
            if raw_packet is None:
                failed += 1
                outer_fail += 1
                if packet_index <= 5:
                    log.warning(
                        "RAW PACKET missing | idx=%d pkt=%d seq=%d ts=%d",
                        idx,
                        packet_index,
                        p["sequence"],
                        p["timestamp"],
                    )
                continue

            outer_result = self._decrypt_standard_voice_packet(raw_packet)
            if not outer_result:
                failed += 1
                outer_fail += 1
                if packet_index <= 5:
                    log.warning(
                        "OUTER DECRYPT failed | idx=%d pkt=%d seq=%d ts=%d payload=%d",
                        idx,
                        packet_index,
                        p["sequence"],
                        p["timestamp"],
                        len(p["payload"]),
                    )
                continue

            outer_plain, outer_info = outer_result

            used_dave_inner = False
            if use_dave:
                opus_packet, resolved_user_id = self._decrypt_dave_inner_packet(
                    user_id=int(user_id),
                    outer_plain=outer_plain,
                )
                if resolved_user_id is not None and int(resolved_user_id) != int(user_id):
                    user_id = int(resolved_user_id)
                    self.runtime.bind_ssrc(int(user_id), int(ssrc))

                if opus_packet is None:
                    failed += 1
                    dave_fail += 1

                    if packet_index <= 3:
                        log.warning(
                            "PACKET DAVE failed | idx=%d pkt=%d seq=%d ts=%d outer_len=%d ext_len=%d",
                            idx,
                            packet_index,
                            p["sequence"],
                            p["timestamp"],
                            len(outer_plain),
                            max(0, outer_info["header_len"] - outer_info["unencrypted_header_len"]),
                        )
                    continue

                used_dave_inner = True
            else:
                opus_packet = outer_plain

            if opus_packet == b"\xF8\xFF\xFE":
                real_silence += 1
                pcm_chunks.append(SILENCE_PCM)
                success += 1
                continue

            if len(opus_packet) < 8:
                failed += 1
                opus_fail += 1
                if packet_index <= 5:
                    log.warning(
                        "PACKET too short | idx=%d pkt=%d seq=%d ts=%d len=%d",
                        idx,
                        packet_index,
                        p["sequence"],
                        p["timestamp"],
                        len(opus_packet),
                    )
                continue

            try:
                pcm = self.opus_decoder.decode(opus_packet, fec=False)
            except Exception as e:
                failed += 1
                opus_fail += 1
                if packet_index <= 5:
                    log.warning(
                        "PACKET OPUS failed | idx=%d pkt=%d seq=%d ts=%d bytes=%d err=%r",
                        idx,
                        packet_index,
                        p["sequence"],
                        p["timestamp"],
                        len(opus_packet),
                        e,
                    )
                continue

            if not pcm:
                failed += 1
                opus_fail += 1
                continue

            pcm_chunks.append(pcm)
            success += 1

            if used_dave_inner:
                dave_success += 1


        log.info(
            "DECRYPT SUMMARY | idx=%d packets=%d success=%d failed=%d pcm_chunks=%d dave_ok=%d outer_fail=%d dave_fail=%d opus_fail=%d real_silence=%d",
            idx,
            len(packets),
            success,
            failed,
            len(pcm_chunks),
            dave_success,
            outer_fail,
            dave_fail,
            opus_fail,
            real_silence,
        )

        if not pcm_chunks:
            return

        pcm_bytes = b"".join(pcm_chunks)

        member = None
        try:
            member = self.channel.guild.get_member(int(user_id))
        except Exception:
            member = None

        if getattr(self, "on_user_audio", None) is not None:
            try:
                log.info(
                    "on_user_audio call | idx=%d user_id=%s member=%s pcm_bytes=%d",
                    idx,
                    user_id,
                    getattr(member, "display_name", None),
                    len(pcm_bytes),
                )
                await self.on_user_audio(member, pcm_bytes)
                log.info("on_user_audio ok | idx=%d pcm_bytes=%d", idx, len(pcm_bytes))
            except Exception as e:
                log.warning("on_user_audio callback failed | idx=%d err=%r", idx, e)

    def stop_listening(self) -> None:
        if self._receive_task is not None:
            self._receive_task.cancel()
            self._receive_task = None

        if self._decrypt_task is not None:
            self._decrypt_task.cancel()
            self._decrypt_task = None

        if self._utterance_task is not None:
            self._utterance_task.cancel()
            self._utterance_task = None

        if self.sink is not None:
            self.sink.cleanup()
            self.sink = None

        self.utterance_states.clear()

        log.info("Receive loop stopped")

    async def disconnect(self, *, force: bool = False) -> None:
        self.stop_listening()

        if self.gateway is not None:
            await self.gateway.close()

        self.dave.reset()

        if self.udp_transport is not None:
            await self.udp_transport.close()
            self.udp_transport = None

        await super().disconnect(force=force)
        log.info("EvelynVoiceClient disconnected")

    def _find_base_udp_socket(self):
        candidates = {
            "self.socket": getattr(self, "socket", None),
            "self._socket": getattr(self, "_socket", None),
            "self._connection.socket": getattr(getattr(self, "_connection", None), "socket", None),
            "self.ws.socket": getattr(getattr(self, "ws", None), "socket", None),
        }

        if candidates["self._connection.socket"] is not None:
            return candidates["self._connection.socket"]

        if candidates["self.socket"] is not None:
            return candidates["self.socket"]

        return None

    @staticmethod
    def _parse_endpoint(endpoint: str | None) -> tuple[str, int]:
        if not endpoint:
            return "127.0.0.1", 50000

        endpoint = endpoint.replace("wss://", "").replace("ws://", "")
        if ":" in endpoint:
            host, port_text = endpoint.rsplit(":", 1)
            try:
                return host, int(port_text)
            except ValueError:
                return host, 443
        return endpoint, 443