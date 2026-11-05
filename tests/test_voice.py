"""Voice: local engines only, sentences spoken as the reply streams, turns land in chat."""

from __future__ import annotations

import base64
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from pablo.api import create_app
from pablo.config import Settings, VoiceConfig
from pablo.convos import CHAT_PROMPT, SPOKEN
from pablo.gateway import FakeBackend, Message
from pablo.orchestrator import Orchestrator
from pablo.voice import OpenAITTS, SentenceCutter, WhisperCppSTT, speakable

CHECKS = {"tests": [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"]}
REPLY = (
    "Sure, I can do that for you right now. It goes next to add in calc.py.\n"
    "```python\ndef sub(a, b):\n    return a - b\n```\n"
    "RUN: add sub(a, b) to calc.py\nThe checks will run before you see it."
)


class FakeSTT:
    def __init__(self, text: str) -> None:
        self.text = text
        self.got: list[tuple[bytes, str]] = []

    def transcribe(self, audio: bytes, mime: str) -> str:
        self.got.append((audio, mime))
        return self.text


class FakeTTS:
    def __init__(self) -> None:
        self.said: list[tuple[str, str]] = []

    def speak(self, text: str, voice: str = "") -> bytes:
        self.said.append((text, voice))
        return b"RIFF" + text.encode()


def setup(
    settings: Settings, repo: Path, heard: str = "add a sub function"
) -> tuple[TestClient, FakeBackend, FakeTTS]:
    def coder(model: str, msgs: Sequence[Message]) -> str:
        if CHAT_PROMPT in msgs[0]["content"]:
            return REPLY
        return "nothing"

    advisor = "Don't. Ship less."
    backend = FakeBackend(scripts={"pablo/coder": coder, "pablo/advisor": lambda m, x: advisor})
    settings.voice.voices = {"advisor": "deep-voice"}
    orch = Orchestrator(settings, backend, repo=repo, checks=CHECKS)
    tts = FakeTTS()
    app = create_app(orch, voice_engines=(FakeSTT(heard), tts))
    return TestClient(app, client=("127.0.0.1", 5000)), backend, tts


def events(resp: httpx.Response) -> list[dict[str, Any]]:
    return [json.loads(line) for line in resp.text.splitlines() if line.strip()]


def test_voice_engines_must_stay_on_pablo() -> None:
    ok = ("http://127.0.0.1:8090", "http://100.101.1.2:8880", "http://pablo.tail1.ts.net:9")
    for url in ok:
        assert VoiceConfig(stt_url=url, tts_url=url).enabled
    for url in ("https://api.openai.com", "http://192.168.1.20:8090", "http://example.com"):
        with pytest.raises(ValueError, match="this machine or your tailnet"):
            VoiceConfig(stt_url=url)


def test_sentences_come_out_as_soon_as_they_end() -> None:
    cut = SentenceCutter()
    out: list[str] = []
    for i in range(0, len(REPLY), 7):
        out += cut.feed(REPLY[i : i + 7])
    out += cut.flush()
    assert out == [
        "Sure, I can do that for you right now.",
        "It goes next to add in calc.py.",
        "The checks will run before you see it.",
    ]
    assert speakable("**Ship** it: see [the doc](https://x.y) and `pablo cert`") == (
        "Ship it: see the doc and pablo cert"
    )


def test_a_voice_turn_streams_text_then_speech_into_a_conversation(
    settings: Settings, repo: Path
) -> None:
    c, backend, tts = setup(settings, repo)
    wav = {"content-type": "audio/wav"}
    out = events(c.post("/api/voice/turn?lane=coder", content=b"WAVE", headers=wav))
    kinds = [e["type"] for e in out]
    assert kinds[:2] == ["conversation", "heard"] and kinds[-1] == "done"
    assert out[1]["text"] == "add a sub function"
    audio = [e for e in out if e["type"] == "audio"]
    assert [a["seq"] for a in audio] == [0, 1, 2]
    assert base64.b64decode(audio[0]["data"]) == b"RIFFSure, I can do that for you right now."
    assert all("def sub" not in t and "RUN" not in t for t, _ in tts.said)  # never read code aloud
    assert SPOKEN.strip()[:30] in backend.calls[0][1][0]["content"]
    turn = out[-1]["turn"]
    assert turn["user"]["content"] == "add a sub function" and turn["run"]  # RUN still works
    conv = c.get(f"/api/conversations/{out[0]['id']}").json()
    assert [m["role"] for m in conv["messages"]] == ["user", "assistant"]


def test_the_advisor_speaks_in_its_own_voice_and_keeps_its_conversation(
    settings: Settings, repo: Path
) -> None:
    c, _, tts = setup(settings, repo, heard="should I ship it")
    first = events(c.post("/api/voice/turn?lane=advisor", content=b"W"))
    cid = first[0]["id"]
    again = events(c.post(f"/api/voice/turn?lane=advisor&conversation={cid}", content=b"W"))
    assert again[0]["id"] == cid and tts.said[0] == ("Don't. Ship less.", "deep-voice")
    assert c.get(f"/api/conversations/{cid}").json()["conversation"]["lane"] == "advisor"


def test_what_voice_refuses(settings: Settings, repo: Path) -> None:
    c, _, _ = setup(settings, repo, heard="")
    assert c.post("/api/voice/turn?lane=claude", content=b"W").status_code == 400
    assert c.post("/api/voice/turn?lane=coder", content=b"").status_code == 400
    silent = events(c.post("/api/voice/turn?lane=coder", content=b"W"))
    assert silent[-1] == {"type": "error", "status": 422, "detail": "Didn't catch that. Try again?"}
    off = TestClient(create_app(Orchestrator(settings, FakeBackend(), repo=repo)),
                     client=("127.0.0.1", 5000))  # fmt: skip
    assert off.post("/api/voice/turn?lane=coder", content=b"W").status_code == 503
    assert off.get("/api/voice/status").json()["enabled"] is False


def test_engine_clients_send_what_the_servers_expect(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[dict[str, Any]] = []

    def post(url: str, **kw: Any) -> httpx.Response:
        sent.append({"url": url, **kw})
        req = httpx.Request("POST", url)
        if url.endswith("/inference"):
            return httpx.Response(200, json={"text": " hello there "}, request=req)
        return httpx.Response(200, content=b"RIFFaudio", request=req)

    monkeypatch.setattr(httpx, "post", post)
    assert WhisperCppSTT("http://127.0.0.1:8090/").transcribe(b"W", "audio/wav") == "hello there"
    assert sent[0]["url"] == "http://127.0.0.1:8090/inference"
    assert sent[0]["data"]["response_format"] == "json"
    assert OpenAITTS("http://127.0.0.1:8880", "kokoro").speak("Hi.", "af") == b"RIFFaudio"
    body = {"input": "Hi.", "response_format": "wav", "model": "kokoro", "voice": "af"}
    assert sent[1]["json"] == body
