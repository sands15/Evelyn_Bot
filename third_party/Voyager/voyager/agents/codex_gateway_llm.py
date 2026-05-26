from __future__ import annotations

import os
from typing import Any

import requests
from langchain.schema import AIMessage


class CodexGatewayLLM:
    def __init__(
        self,
        url: str = os.getenv("VOYAGER_CODEX_GATEWAY_URL", "http://127.0.0.1:8787/codex/action"),
        model: str = os.getenv("VOYAGER_CODEX_MODEL", "gpt-5.5"),
        timeout_sec: int = 260,
    ) -> None:
        self.url = url
        self.model = model
        self.timeout_sec = int(timeout_sec)
        self.model_name = "codex-gateway"

    def __call__(self, messages: list[Any]) -> AIMessage:
        prompt = self._messages_to_prompt(messages)
        response = requests.post(
            self.url,
            json={
                "prompt": prompt,
                "model": self.model,
                "timeout_sec": self.timeout_sec,
                "source": "voyager-action",
                "priority": int(os.getenv("VOYAGER_CODEX_GATEWAY_PRIORITY", "50")),
            },
            timeout=self.timeout_sec + 10,
        )
        response.raise_for_status()
        data = response.json()
        if not data.get("ok"):
            raise RuntimeError(data.get("error", "Codex gateway failed"))
        content = data.get("content", "").strip()
        if "```" in content:
            return AIMessage(content=content)
        return AIMessage(content=f"```javascript\n{content}\n```")

    def _messages_to_prompt(self, messages: list[Any]) -> str:
        chunks: list[str] = []
        for message in messages:
            role = getattr(message, "type", None) or getattr(message, "role", None) or message.__class__.__name__
            content = getattr(message, "content", str(message))
            chunks.append(f"[{role}]\n{content}")
        return "\n\n".join(chunks)
