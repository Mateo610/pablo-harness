"""Voice: local engines only, sentences spoken as the reply streams, turns land in chat."""

from __future__ import annotations

import base64
import json
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from tests.fakes import FakeBackend

from pablo.api import create_app
from pablo.config import Settings, VoiceConfig
from pablo.convos import CHAT_PROMPT, SPOKEN
from pablo.gateway import Message
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
        self.got: list[tuple[bytes, str, str]] = []

    def transcribe(self, audio: bytes, mime: str, prompt: str = "") -> str:
        self.got.append((audio, mime, prompt))
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
    assert again[0]["id"] == cid and tts.said[0] == ("Don't.", "deep-voice")
    assert c.get(f"/api/conversations/{cid}").json()["conversation"]["lane"] == "advisor"


def test_what_voice_refuses(settings: Settings, repo: Path) -> None:
    c, _, _ = setup(settings, repo, heard="")
    assert c.post("/api/voice/turn?lane=claude", content=b"W").status_code == 400
    deep = c.post("/api/voice/turn?lane=deep", content=b"W")  # can't share the GPU with the voice
    assert deep.status_code == 400 and "can't talk" in deep.json()["detail"]
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
    stt = WhisperCppSTT("http://127.0.0.1:8090/")
    assert stt.transcribe(b"W", "audio/wav", "Project words: load_line.") == "hello there"
    assert sent[0]["url"] == "http://127.0.0.1:8090/inference"
    assert sent[0]["data"]["response_format"] == "json"
    assert sent[0]["data"]["prompt"] == "Project words: load_line."
    assert OpenAITTS("http://127.0.0.1:8880", "kokoro").speak("Hi.", "af") == b"RIFFaudio"
    body = {"input": "Hi.", "response_format": "wav", "model": "kokoro", "voice": "af"}
    assert sent[1]["json"] == body


def test_a_call_keeps_the_same_system_prompt_so_the_cache_hits(
    settings: Settings, repo: Path
) -> None:
    """Voice turns skip per-message file picking and recall; text chat keeps both."""
    c, backend, _ = setup(settings, repo)
    first = events(c.post("/api/voice/turn?lane=coder", content=b"W"))
    cid = first[0]["id"]
    events(c.post(f"/api/voice/turn?lane=coder&conversation={cid}", content=b"W"))
    one, two = (
        msgs[0]["content"] for m, msgs in backend.calls if CHAT_PROMPT in msgs[0]["content"]
    )
    assert one == two
    assert "The repo at a glance" in one and "def add(a, b)" in one
    assert "The repo, for this message" not in one and "From earlier conversations" not in one


def test_speech_to_text_hears_the_projects_words(settings: Settings, repo: Path) -> None:
    from pablo.voice import vocabulary

    (repo / "ledger.py").write_text(
        "class LedgerBook:\n    pass\n\n\ndef load_line(raw):\n    return raw\n"
    )
    subprocess.run(["git", "add", "ledger.py"], cwd=repo, check=True)
    hint = vocabulary(repo, settings.home)
    assert hint.startswith("Project words:") and "load_line" in hint and "LedgerBook" in hint
    assert "ledger" in hint and "test_add" not in hint and len(hint) < 800
    stt = FakeSTT("rename load line")
    orch = Orchestrator(settings, FakeBackend(), repo=repo, checks=CHECKS)
    app = create_app(orch, voice_engines=(stt, FakeTTS()))
    TestClient(app, client=("127.0.0.1", 5000)).post("/api/voice", content=b"W")
    assert "load_line" in stt.got[0][2]


def test_a_turn_wakes_a_paused_voice_server_first(settings: Settings, repo: Path) -> None:
    from pablo.config import ResidentConfig
    from pablo.gpu import GpuScheduler

    asked: list[str] = []
    up = {"tts": False}

    def run(command: str) -> None:
        asked.append(command)
        up["tts"] = command == "start"

    tts_cfg = ResidentConfig(name="tts", start="start", stop="stop",
                             health="http://127.0.0.1:8880", conflicts=["pablo/deep"])  # fmt: skip
    gpu = GpuScheduler(settings.home, idle_s=0, residents_cfg=[tts_cfg], run=run,
                       up=lambda url: up["tts"], unload=asked.append,
                       sleep=lambda s: None)  # fmt: skip
    backend = FakeBackend(scripts={"pablo/coder": lambda m, x: REPLY})
    orch = Orchestrator(settings, backend, repo=repo, checks=CHECKS, gpu=gpu)
    tts = FakeTTS()
    c = TestClient(create_app(orch, voice_engines=(FakeSTT("hi"), tts)), client=("127.0.0.1", 5000))

    out = events(c.post("/api/voice/turn?lane=coder", content=b"W"))
    kinds = [e["type"] for e in out]
    assert kinds.index("status") < kinds.index("delta")  # "waking the voice…" before the answer
    assert asked == ["start"] and tts.said and kinds[-1] == "done"
    assert c.get("/api/voice/status").json()["tts_state"] == "running"

    again = events(c.post("/api/voice/turn?lane=coder", content=b"W"))
    assert "status" not in [e["type"] for e in again] and asked == ["start"]  # awake: no wake


def test_a_voice_server_that_wont_wake_says_so(settings: Settings, repo: Path) -> None:
    from pablo.config import ResidentConfig
    from pablo.gpu import GpuScheduler

    tts_cfg = ResidentConfig(name="tts", start="start", stop="stop", health="h", warmup_s=2)
    gpu = GpuScheduler(settings.home, idle_s=0, residents_cfg=[tts_cfg], run=lambda c: None,
                       up=lambda url: False, sleep=lambda s: None)  # fmt: skip
    orch = Orchestrator(settings, FakeBackend(), repo=repo, checks=CHECKS, gpu=gpu)
    c = TestClient(create_app(orch, voice_engines=(FakeSTT("hi"), FakeTTS())),
                   client=("127.0.0.1", 5000))  # fmt: skip
    out = events(c.post("/api/voice/turn?lane=coder", content=b"W"))
    assert out[-1]["type"] == "error" and out[-1]["status"] == 503
    assert "didn't wake" in out[-1]["detail"]


def test_a_finished_run_is_said_out_loud(settings: Settings, repo: Path) -> None:
    from pablo.voice import news_line

    c, _, tts = setup(settings, repo)
    turn = events(c.post("/api/voice/turn?lane=coder", content=b"W"))[-1]["turn"]
    run_id = turn["run"]["id"]
    import time

    for _ in range(100):  # the run works on a thread; wait for it to finish
        if c.get(f"/api/runs/{run_id}").json()["state"] != "running":
            break
        time.sleep(0.05)
    said = c.post("/api/voice/news", json={"run_id": run_id, "lane": "coder"}).json()
    assert not said["skip"] and said["data"] and "needs you" in said["text"]
    assert tts.said[-1][0] == said["text"]  # synthesized on Pablo, like every other line
    assert c.post("/api/voice/news", json={"run_id": "nope"}).status_code == 404

    class R:
        task = "create greet.py with greet(name)\n\nThis is step 2 of a plan: a greeter"
        state, certainty, question = "ready", 0.9, ""

    line = news_line(R())
    assert line.startswith("Step 2, create greet.py") and "90 percent" in line
    R.state, R.question = "needs_you", "The checks got worse. Look at test_add."
    assert news_line(R()).endswith("needs you. The checks got worse")
    assert "waiting for the owner" in news_line(type("X", (R,), {"state": "ready"})(),
                                                can_apply=False)  # fmt: skip


def test_a_voice_server_stopped_elsewhere_reads_as_sleeping(
    settings: Settings, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A CLI deep run stops the TTS unit; a long-running serve must not keep saying running."""
    import pablo.api
    from pablo.config import ResidentConfig
    from pablo.gpu import GpuScheduler

    tts_cfg = ResidentConfig(name="tts", start="start", stop="stop",
                             health="http://127.0.0.1:8880", conflicts=["pablo/deep"])  # fmt: skip
    gpu = GpuScheduler(settings.home, residents_cfg=[tts_cfg], run=lambda c: None,
                       up=lambda url: False, sleep=lambda s: None)  # fmt: skip
    gpu.residents["tts"].state = "running"  # what this process last saw
    settings.voice.stt_url, settings.voice.tts_url = (
        "http://127.0.0.1:8090",
        "http://127.0.0.1:8880",
    )
    stt_url = settings.voice.stt_url
    monkeypatch.setattr(pablo.api, "reachable", lambda url: url == stt_url)
    orch = Orchestrator(settings, FakeBackend(), repo=repo, checks=CHECKS, gpu=gpu)
    c = TestClient(create_app(orch), client=("127.0.0.1", 5000))
    status = c.get("/api/voice/status").json()
    assert status["tts"] is False and status["tts_state"] == "paused"  # the page says it wakes
    assert gpu.residents["tts"].state == "paused"


def test_the_first_thing_said_is_short() -> None:
    """Speech costs time per word, so the first piece ends at its first clause or sentence."""
    text = (
        "Okay, so the plan has three steps, and the first one reads the sensor. "
        "Then we add alerts. Want me to start?"
    )
    cut = SentenceCutter()
    out: list[str] = []
    for i in range(0, len(text), 5):
        out += cut.feed(text[i : i + 5])
    out += cut.flush()
    assert out == [
        "Okay, so the plan has three steps,",
        "and the first one reads the sensor.",
        "Then we add alerts. Want me to start?",
    ]
    first = SentenceCutter()
    assert first.feed("Four. ") == ["Four."]  # a one-word answer goes out at once
    assert first.feed("Next, a longer one, ") == []  # after that, whole sentences only
