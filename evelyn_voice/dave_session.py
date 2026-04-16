from __future__ import annotations

import logging
from typing import Any

import davey

log = logging.getLogger(__name__)


class DaveSession:
    def __init__(self) -> None:
        self.protocol_version: int = int(davey.DAVE_PROTOCOL_VERSION)
        self.epoch: int | None = None
        self.ready: bool = False

        self.own_user_id: int | None = None
        self.channel_id: int | None = None

        self.session: davey.DaveSession | None = None
        self.status: Any = None

    def init_session(self, *, user_id: int, channel_id: int) -> None:
        self.own_user_id = user_id
        self.channel_id = channel_id
        self.session = davey.DaveSession(self.protocol_version, user_id, channel_id)
        self._refresh_state("init_session")

    def _ensure_session(self) -> davey.DaveSession:
        if self.session is None:
            raise RuntimeError("DaveSession is not initialized yet")
        return self.session

    def _refresh_state(self, where: str) -> None:
        if self.session is None:
            self.ready = False
            self.status = None
            return

        self.ready = bool(self.session.ready)
        self.status = getattr(self.session, "status", None)
        self.epoch = getattr(self.session, "epoch", self.epoch)
        self.protocol_version = getattr(self.session, "protocol_version", self.protocol_version)

        log.info(
            "DAVE state | where=%s ready=%s status=%r epoch=%r proto=%r",
            where,
            self.ready,
            self.status,
            self.epoch,
            self.protocol_version,
        )

    def set_protocol_version(self, version: int) -> None:
        self.protocol_version = version
        log.info("DAVE protocol_version set to %s", version)

    def set_epoch(self, epoch: int) -> None:
        self.epoch = epoch
        log.info("DAVE epoch set to %s", epoch)

    def process_welcome(self, welcome: Any) -> None:
        session = self._ensure_session()
        session.process_welcome(welcome)
        self._refresh_state("process_welcome")

    def process_commit(self, commit: Any) -> None:
        session = self._ensure_session()
        session.process_commit(commit)
        self._refresh_state("process_commit")

    def process_proposals(
        self,
        operation_type: Any,
        proposals: Any,
        expected_user_ids: list[int] | None = None,
    ) -> None:
        session = self._ensure_session()
        session.process_proposals(operation_type, proposals, expected_user_ids)
        self._refresh_state("process_proposals")

    def set_external_sender(self, external_sender_data: Any) -> None:
        session = self._ensure_session()
        session.set_external_sender(external_sender_data)
        self._refresh_state("set_external_sender")

    def decrypt_rtp(self, *, user_id: int, media_type: Any, packet: bytes) -> bytes:
        session = self._ensure_session()
        return session.decrypt(user_id, media_type, packet)

    def reset(self) -> None:
        if self.session is not None:
            try:
                self.session.reset()
            except Exception:
                pass

        self.session = None
        self.epoch = None
        self.ready = False
        self.status = None
        self.own_user_id = None
        self.channel_id = None

    def create_key_package(self) -> bytes | None:
        candidates = [
            getattr(self, "session", None),
            getattr(self, "_session", None),
            getattr(self, "mls", None),
            getattr(self, "_mls", None),
            getattr(self, "inner", None),
            getattr(self, "_inner", None),
        ]

        for obj in candidates:
            if obj is None:
                continue

            creator = getattr(obj, "get_serialized_key_package", None)
            if creator is None:
                continue

            try:
                payload = creator()
                if payload:
                    return bytes(payload)
                return None
            except Exception as e:
                log.warning("DAVE create_key_package failed | err=%r", e)
                return None

        log.warning("DAVE create_key_package failed | no get_serialized_key_package provider found")
        return None