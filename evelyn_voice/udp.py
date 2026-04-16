from __future__ import annotations

import asyncio
import logging
import socket

log = logging.getLogger(__name__)


class VoiceUDPTransport:
    def __init__(self, sock: socket.socket):
        self.sock = sock
        self.sock.setblocking(False)
        self._keepalive_task: asyncio.Task | None = None
        self._closed = False

    async def open(self) -> None:
        log.info("Using existing discord.py UDP socket")

    async def start_keepalive(self) -> None:
        if self._keepalive_task is not None:
            return
        self._keepalive_task = asyncio.create_task(self._keepalive_loop())

    async def _keepalive_loop(self) -> None:
        try:
            while not self._closed:
                await asyncio.sleep(5.0)
                log.debug("UDP keepalive tick")
        except asyncio.CancelledError:
            pass

    async def recv_packet(self) -> bytes:
        loop = asyncio.get_running_loop()
        data, _addr = await loop.sock_recvfrom(self.sock, 4096)
        return data

    async def close(self) -> None:
        self._closed = True
        if self._keepalive_task is not None:
            self._keepalive_task.cancel()
            try:
                await self._keepalive_task
            except asyncio.CancelledError:
                pass
            self._keepalive_task = None