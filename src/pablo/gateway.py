"""Talk to models through one OpenAI-compatible gateway (LiteLLM on Pablo).

Every lane goes through the same door, so swapping engines (KTransformers,
colibrì, llama.cpp, vLLM) is a gateway config change, not a code change.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Protocol, TypedDict

import httpx


class Message(TypedDict):
    role: str
    content: str


@dataclass(frozen=True)
class Completion:
    model: str
    text: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_s: float = 0.0


class ChatBackend(Protocol):
    def complete(
        self,
        model: str,
        messages: Sequence[Message],
        *,
        temperature: float = 0.2,
        top_p: float | None = None,
        max_tokens: int = 4096,
        timeout_s: float | None = None,
    ) -> Completion: ...


class GatewayError(RuntimeError):
    pass


class LiteLLMBackend:
    def __init__(self, base_url: str, api_key: str, timeout_s: float = 600.0) -> None:
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout_s,
        )

    def complete(
        self,
        model: str,
        messages: Sequence[Message],
        *,
        temperature: float = 0.2,
        top_p: float | None = None,
        max_tokens: int = 4096,
        timeout_s: float | None = None,
    ) -> Completion:
        started = time.perf_counter()
        payload: dict[str, object] = {
            "model": model,
            "messages": list(messages),
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if top_p is not None:
            payload["top_p"] = top_p
        try:
            resp = self._client.post(
                "/chat/completions",
                json=payload,
                timeout=timeout_s if timeout_s else httpx.USE_CLIENT_DEFAULT,
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise GatewayError(f"{model}: {exc}") from exc
        body = resp.json()
        usage = body.get("usage") or {}
        return Completion(
            model=model,
            text=body["choices"][0]["message"]["content"] or "",
            prompt_tokens=int(usage.get("prompt_tokens", 0)),
            completion_tokens=int(usage.get("completion_tokens", 0)),
            latency_s=time.perf_counter() - started,
        )

    def health(self) -> bool:
        try:
            return self._client.get("/models", timeout=5).status_code == 200
        except httpx.HTTPError:
            return False


Responder = Callable[[str, Sequence[Message]], str]


@dataclass
class FakeBackend:
    """Scripted backend for tests, demos, and building the UI without a GPU.

    `scripts` maps a model alias to a list of replies (consumed in order) or a
    function of (model, messages). Unknown models echo the last user message.
    """

    scripts: dict[str, list[str] | Responder] = field(default_factory=dict)
    calls: list[tuple[str, list[Message]]] = field(default_factory=list)

    def complete(
        self,
        model: str,
        messages: Sequence[Message],
        *,
        temperature: float = 0.2,
        top_p: float | None = None,
        max_tokens: int = 4096,
        timeout_s: float | None = None,
    ) -> Completion:
        del temperature, top_p
        self.calls.append((model, list(messages)))
        script = self.scripts.get(model)
        if callable(script):
            text = script(model, messages)
        elif script:
            text = script.pop(0)
        else:
            text = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        return Completion(
            model=model,
            text=text,
            prompt_tokens=sum(len(m["content"]) // 4 for m in messages),
            completion_tokens=len(text) // 4,
        )
