"""Talk to models through one OpenAI-compatible gateway (LiteLLM on Pablo).

Every lane goes through the same door, so swapping engines (KTransformers,
colibrì, llama.cpp, vLLM) is a gateway config change, not a code change.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, TypedDict, cast

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


Delta = Callable[[str], None]


def stream_complete(
    backend: ChatBackend,
    model: str,
    messages: Sequence[Message],
    on_delta: Delta,
    **kw: Any,
) -> Completion:
    """Stream the reply through `on_delta` when the backend can; else one call, one delta."""
    stream = getattr(backend, "stream", None)
    if stream is None:
        done = backend.complete(model, messages, **kw)
        on_delta(done.text)
        return done
    return cast(Completion, stream(model, messages, on_delta=on_delta, **kw))


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

    def stream(
        self,
        model: str,
        messages: Sequence[Message],
        *,
        on_delta: Delta,
        temperature: float = 0.2,
        top_p: float | None = None,
        max_tokens: int = 4096,
        timeout_s: float | None = None,
    ) -> Completion:
        """Same request as `complete`, streamed. Reasoning text is never forwarded."""
        started = time.perf_counter()
        payload: dict[str, object] = {
            "model": model,
            "messages": list(messages),
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if top_p is not None:
            payload["top_p"] = top_p
        parts: list[str] = []
        usage: dict[str, Any] = {}
        try:
            with self._client.stream(
                "POST",
                "/chat/completions",
                json=payload,
                timeout=timeout_s if timeout_s else httpx.USE_CLIENT_DEFAULT,
            ) as resp:
                resp.raise_for_status()
                for line in resp.iter_lines():
                    if not line.startswith("data: ") or line == "data: [DONE]":
                        continue
                    chunk = json.loads(line[6:])
                    usage = chunk.get("usage") or usage
                    for choice in chunk.get("choices") or []:
                        piece = (choice.get("delta") or {}).get("content") or ""
                        if piece:
                            parts.append(piece)
                            on_delta(piece)
        except (httpx.HTTPError, ValueError) as exc:
            raise GatewayError(f"{model}: {exc}") from exc
        text = "".join(parts)
        return Completion(
            model=model,
            text=text,
            prompt_tokens=int(usage.get("prompt_tokens", 0)),
            completion_tokens=int(usage.get("completion_tokens", 0)) or len(text) // 4,
            latency_s=time.perf_counter() - started,
        )

    def health(self) -> bool:
        try:
            return self._client.get("/models", timeout=5).status_code == 200
        except httpx.HTTPError:
            return False
