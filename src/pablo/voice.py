"""Voice: speech in, speech out, both from local processes on Pablo.

Speech-to-text and text-to-speech are separate servers (installed in M9b). This module only
talks to them over HTTP on loopback or the tailnet (config refuses anything else), so audio
never leaves your machines.

A voice turn is a chat turn: the transcript goes to `Chat.say(spoken=True)`, the reply
streams, and each finished sentence is spoken while the next is still being written.
"""

from __future__ import annotations

import re
from typing import Protocol

import httpx

from pablo.config import VoiceConfig


class VoiceOff(RuntimeError):
    pass


class Transcriber(Protocol):
    def transcribe(self, audio: bytes, mime: str) -> str: ...


class Speaker(Protocol):
    def speak(self, text: str, voice: str = "") -> bytes: ...


class WhisperCppSTT:
    """whisper.cpp's server: POST /inference, multipart `file`. Send 16 kHz mono WAV."""

    def __init__(self, url: str, language: str = "en", timeout_s: float = 60.0) -> None:
        self.url, self.language, self.timeout_s = url.rstrip("/"), language, timeout_s

    def transcribe(self, audio: bytes, mime: str) -> str:
        resp = httpx.post(
            f"{self.url}/inference",
            files={"file": ("speech.wav", audio, mime or "audio/wav")},
            data={"response_format": "json", "language": self.language, "temperature": "0"},
            timeout=self.timeout_s,
        )
        resp.raise_for_status()
        return str(resp.json().get("text", "")).strip()


class OpenAISTT:
    """Any server with the OpenAI transcription API, e.g. a faster-whisper server."""

    def __init__(self, url: str, model: str, language: str = "en", timeout_s: float = 60.0) -> None:
        self.url, self.model = url.rstrip("/"), model or "whisper-1"
        self.language, self.timeout_s = language, timeout_s

    def transcribe(self, audio: bytes, mime: str) -> str:
        resp = httpx.post(
            f"{self.url}/v1/audio/transcriptions",
            files={"file": ("speech.wav", audio, mime or "audio/wav")},
            data={"model": self.model, "language": self.language, "response_format": "json"},
            timeout=self.timeout_s,
        )
        resp.raise_for_status()
        return str(resp.json().get("text", "")).strip()


class OpenAITTS:
    """The OpenAI speech API (POST /v1/audio/speech), which local TTS servers commonly speak."""

    def __init__(self, url: str, model: str, fmt: str = "wav", timeout_s: float = 60.0) -> None:
        self.url, self.model, self.fmt, self.timeout_s = url.rstrip("/"), model, fmt, timeout_s

    def speak(self, text: str, voice: str = "") -> bytes:
        body: dict[str, str] = {"input": text, "response_format": self.fmt}
        if self.model:
            body["model"] = self.model
        if voice:
            body["voice"] = voice
        resp = httpx.post(f"{self.url}/v1/audio/speech", json=body, timeout=self.timeout_s)
        resp.raise_for_status()
        return resp.content


def engines(cfg: VoiceConfig) -> tuple[Transcriber, Speaker]:
    if not cfg.enabled:
        raise VoiceOff("Voice isn't set up on Pablo yet (voice.stt_url and voice.tts_url).")
    stt: Transcriber = (
        WhisperCppSTT(cfg.stt_url, cfg.language, cfg.timeout_s)
        if cfg.stt_api == "whisper.cpp"
        else OpenAISTT(cfg.stt_url, cfg.stt_model, cfg.language, cfg.timeout_s)
    )
    return stt, OpenAITTS(cfg.tts_url, cfg.tts_model, cfg.tts_format, cfg.timeout_s)


def reachable(url: str) -> bool:
    """Something answers at this address (any HTTP status counts)."""
    if not url:
        return False
    try:
        httpx.get(url, timeout=2)
        return True
    except httpx.HTTPError:
        return False


_FENCE = re.compile(r"```.*?(```|$)", re.S)
_HARNESS_LINE = re.compile(r"^\s*(RUN|REMEMBER):.*$", re.M)
_MARKS = re.compile(r"[*_`#>]+")
_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")


def speakable(text: str) -> str:
    """What to say out loud: no code, no harness lines, no markdown symbols."""
    text = _FENCE.sub(" ", text)
    text = _HARNESS_LINE.sub("", text)
    text = _LINK.sub(r"\1", text)
    text = _MARKS.sub("", text)
    text = re.sub(r"^\s*[-•]\s+", "", text, flags=re.M)
    return re.sub(r"\s+", " ", text).strip()


class SentenceCutter:
    """Feed streamed text in; get whole sentences out as soon as they end.

    Short fragments wait for the next sentence, so the voice doesn't stutter on "Sure."
    Text inside a code fence is held back until the fence closes, then dropped by `speakable`.
    """

    MIN_CHARS = 24

    def __init__(self) -> None:
        self._buf = ""

    def feed(self, delta: str) -> list[str]:
        self._buf += delta
        out: list[str] = []
        while True:
            cut = self._next_cut()
            if cut is None:
                return out
            piece, self._buf = self._buf[:cut], self._buf[cut:]
            said = speakable(piece)
            if said:
                out.append(said)

    def _next_cut(self) -> int | None:
        """The first sentence end past MIN_CHARS that isn't inside a code block."""
        for match in re.finditer(r"(?<=[.!?])\s+|\n", self._buf):
            cut = match.end()
            if cut >= self.MIN_CHARS and self._buf[:cut].count("```") % 2 == 0:
                return cut
        return None

    def flush(self) -> list[str]:
        said, self._buf = speakable(self._buf), ""
        return [said] if said else []
