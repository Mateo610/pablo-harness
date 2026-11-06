"""Load lanes, gateway, and thresholds from config/lanes.yaml."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator

ESCALATION_LANE = "claude"


class Lane(BaseModel):
    name: str = ""
    role: str
    model: str
    # The model's own name for menus ("Qwen3.6 35B-A3B"); the lane name stays internal.
    display: str = ""
    color: str = "#a39c92"
    kind: Literal["local", "remote"] = "local"
    train_ok: bool = True
    requires_approval: bool = False
    keywords: list[str] = Field(default_factory=list)
    # How code lanes write changes: find-and-replace blocks (default) or a unified diff.
    edit_format: Literal["blocks", "diff"] = "blocks"
    # Whether this lane is asked for code changes (verified and judged) or just an answer.
    writes_code: bool = False
    # Reasoning models spend max_tokens on thinking too, so slow lanes get more of both.
    max_tokens: int = 4096
    timeout_s: float = 600.0
    # Prompt budget in tokens. The coder's 16k window leaves ~10.5k for context;
    # a lane with a bigger window (deep: 64k) gets more of the repo.
    context_tokens: int = 10_500
    # Whether the router may send work here on its own. False keeps a lane available
    # by hand (--lane, compare, the overnight queue) but out of automatic routing.
    routable: bool = True
    # After checks get worse, show the coder the failing output this many times and let it fix
    # its change. 0 turns it off. Remote lanes never get extra rounds (each one costs a call).
    fix_rounds: int = 1
    # When a run here lands in Needs you, try the task again on this local lane (the coder
    # retries on deep). "now" runs it right after; "overnight" queues it for the window.
    # Never the escalation lane: the harness does not send work to Claude on its own.
    retry_on: str | None = None
    retry_when: Literal["now", "overnight", "off"] = "now"
    # Which Needs-you runs retry. "broken": only when the change itself is bad (run failed, no
    # diff, edits didn't apply, checks not clean). "needs_you": also clean runs the judge was
    # unsure about. The 3-run cascade spent most retries re-solving clean, correct runs.
    retry_if: Literal["broken", "needs_you"] = "broken"
    # Two-step coding: first list symbols to look up, then edit with those defs/refs in context.
    # One-shot (edit in the first reply) stays the fallback when the model skips the list.
    symbol_lookup: bool = False
    # Sampling. Most lanes stay at 0.2. A lane sets top_p only when its model card requires it.
    temperature: float = 0.2
    top_p: float | None = None
    # Runs on the GPU (takes the scheduler's lease). The router runs on the CPU.
    gpu: bool = True

    @model_validator(mode="after")
    def _remote_never_trains(self) -> Lane:
        # Outputs from remote providers (Claude) are never training data.
        if self.kind == "remote" or self.name == ESCALATION_LANE:
            self.train_ok = False
            self.requires_approval = True
            self.routable = False
            self.fix_rounds = 0
        return self


class Gateway(BaseModel):
    base_url: str = "http://127.0.0.1:4000/v1"
    api_key_env: str = "PABLO_GATEWAY_KEY"

    @property
    def api_key(self) -> str:
        return os.environ.get(self.api_key_env, "sk-pablo-local")


class Thresholds(BaseModel):
    judge_needs_you: float = 0.75


class Overnight(BaseModel):
    """When the queue runs, and when the morning brief is written (Pablo's local time)."""

    start: str = "01:00"
    end: str = "06:30"
    brief_at: str = "07:30"
    default_lane: str = "deep"
    max_jobs: int = 8

    @model_validator(mode="after")
    def _valid_times(self) -> Overnight:
        for value in (self.start, self.end, self.brief_at):
            hours, _, minutes = value.partition(":")
            if not (
                hours.isdigit() and minutes.isdigit() and int(hours) < 24 and int(minutes) < 60
            ):
                raise ValueError(f"overnight times must be HH:MM, got '{value}'")
        return self


class SandboxConfig(BaseModel):
    """Where checks run. `local` is a plain subprocess (tests, CI, --fake).

    `container` runs each check in a throwaway container: no network, read-only image,
    tmpfs /tmp, no capabilities, CPU/memory/process limits, only the scratch copy mounted.
    """

    kind: Literal["local", "container"] = "local"
    engine: str = "podman"  # container CLI: podman (rootless) or docker
    runtime: str = "runsc"  # OCI runtime: runsc is gVisor; "" uses the engine's default
    image: str = "pablo-check:py311"  # base image from scripts/sandbox/Dockerfile
    user: Literal["auto", "host", "root"] = "auto"  # auto: podman keep-id, docker your uid
    memory: str = "4g"
    cpus: float = 4.0
    # Host logical CPUs the check is pinned to. Empty means no pin.
    # 12-15 leaves physical cores 0-3 (threads 0-3 and 8-11) for llama.cpp.
    cpu_set: str = "12-15"
    pids: int = 256
    tmpfs_mb: int = 512

    @model_validator(mode="after")
    def _cpu_set_is_a_list(self) -> SandboxConfig:
        if self.cpu_set and re.fullmatch(r"[0-9]+(?:[,-][0-9]+)*", self.cpu_set) is None:
            raise ValueError(f"sandbox cpu_set must be a taskset list, got {self.cpu_set!r}")
        return self


def _private_url(url: str) -> bool:
    """Loopback or a Tailscale address: audio and text never leave your machines."""
    import ipaddress
    from urllib.parse import urlparse

    host = urlparse(url).hostname or ""
    if host == "localhost":
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return host.endswith(".ts.net")  # MagicDNS names resolve inside the tailnet only
    return (
        ip.is_loopback
        or ip in ipaddress.ip_network("100.64.0.0/10")
        or ip in (ipaddress.ip_network("fd7a:115c:a1e0::/48"))
    )


class VoiceConfig(BaseModel):
    """Speech in and out, both separate processes on Pablo (see docs/cursor-prompts/M9b-voice.md).

    `stt_api`: "whisper.cpp" (POST /inference) or "openai" (POST /v1/audio/transcriptions).
    TTS speaks the OpenAI-style POST /v1/audio/speech. Empty URLs mean voice is off.
    """

    stt_url: str = ""
    stt_api: Literal["whisper.cpp", "openai"] = "whisper.cpp"
    stt_model: str = ""
    tts_url: str = ""
    tts_model: str = ""
    tts_format: Literal["wav", "mp3"] = "wav"
    voices: dict[str, str] = Field(default_factory=dict)  # lane -> voice name; "" = default
    language: str = "en"
    timeout_s: float = 60.0

    @model_validator(mode="after")
    def _stays_on_pablo(self) -> VoiceConfig:
        for name, url in (("stt_url", self.stt_url), ("tts_url", self.tts_url)):
            if url and not _private_url(url):
                raise ValueError(
                    f"voice {name} must be on this machine or your tailnet, got {url!r}"
                )
        return self

    @property
    def enabled(self) -> bool:
        return bool(self.stt_url and self.tts_url)


class SpecConfig(BaseModel):
    """Task-derived checks (src/pablo/spec.py): tests written from the task by the `lane` model
    before it sees the diff. Off until the suite shows they help."""

    enabled: bool = False
    lane: str = "judge"
    timeout_s: float = 120.0


class ResidentConfig(BaseModel):
    """A non-LLM process that lives on the GPU (the TTS server) and can't share it with some
    models. The scheduler pauses it before one of those loads and wakes it when it's needed."""

    name: str
    start: str  # shell-free command, e.g. "systemctl --user start pablo-tts.service"
    stop: str
    health: str  # URL that answers when it's up
    conflicts: list[str] = Field(default_factory=list)  # gateway models, e.g. ["pablo/deep"]
    warmup_s: float = 90.0


class ProjectsConfig(BaseModel):
    # Where projects live: every git repo directly inside is one, and new ones are made here.
    # Empty means <home>/projects.
    root: str = ""


class GpuConfig(BaseModel):
    """The GPU scheduler (src/pablo/gpu.py). `mode` is the starting mode; the owner's switch
    on Home overrides it (saved in ~/.pablo/gpu-mode.json, read by every Pablo process)."""

    mode: Literal["interactive", "balanced", "deep"] = "balanced"
    idle_s: float = 120.0  # balanced: background work waits this long after the last live call
    residents: list[ResidentConfig] = Field(default_factory=list)
    unload_url: str = ""
    # Where every Pablo process shares its GPU lease table. Empty: the home. An eval runs in a
    # throwaway home but sets this to the real one, so it queues with `pablo serve`.
    shared_dir: str = ""  # llama-swap's unload endpoint: frees an idle model's VRAM for a resident


class LearnConfig(BaseModel):
    """Learning without retraining (src/pablo/lessons.py). Recording always happens; using it
    in prompts and routing is off until the suite shows it helps."""

    examples: bool = False  # show the coder 1 or 2 right answers from this repo
    failures: bool = False  # warn the coder what broke on similar changes before
    routing: bool = False  # send a kind of task to deep when deep clearly does better here
    example_tokens: int = 1500


class PracticeConfig(BaseModel):
    """Nightly practice (src/pablo/practice.py): checkable tasks made from these repos, run in
    the overnight window after the queue, at night urgency. Never the eval suite's repos."""

    repos: list[str] = Field(default_factory=list)
    nightly_tasks: int = 0  # 0 = off
    teacher: str = "deep"  # tries what the coder got wrong; "" for none


class Settings(BaseModel):
    gateway: Gateway = Field(default_factory=Gateway)
    thresholds: Thresholds = Field(default_factory=Thresholds)
    overnight: Overnight = Field(default_factory=Overnight)
    sandbox: SandboxConfig = Field(default_factory=SandboxConfig)
    voice: VoiceConfig = Field(default_factory=VoiceConfig)
    spec: SpecConfig = Field(default_factory=SpecConfig)
    gpu: GpuConfig = Field(default_factory=GpuConfig)
    learn: LearnConfig = Field(default_factory=LearnConfig)
    practice: PracticeConfig = Field(default_factory=PracticeConfig)
    projects: ProjectsConfig = Field(default_factory=ProjectsConfig)
    lanes: dict[str, Lane]
    home: Path = Field(default_factory=lambda: default_home())

    @model_validator(mode="after")
    def _name_lanes(self) -> Settings:
        for name, lane in self.lanes.items():
            lane.name = name
            if name == ESCALATION_LANE:
                lane.train_ok = False
                lane.requires_approval = True
                lane.routable = False
        for required in ("router", "coder", "judge"):
            if required not in self.lanes:
                raise ValueError(f"lanes.yaml must define a '{required}' lane")
        for name, lane in self.lanes.items():
            target = lane.retry_on
            if target is None:
                continue
            if target == ESCALATION_LANE or target not in self.lanes:
                raise ValueError(
                    f"lane '{name}' retry_on must be another local lane, not {target!r}"
                )
            if target == name or self.lanes[target].kind != "local":
                raise ValueError(
                    f"lane '{name}' retry_on must be another local lane, not {target!r}"
                )
        return self

    def projects_root(self) -> Path:
        return (
            Path(self.projects.root).expanduser() if self.projects.root else self.home / "projects"
        )

    def lane(self, name: str) -> Lane:
        try:
            return self.lanes[name]
        except KeyError as exc:
            raise KeyError(f"unknown lane '{name}'") from exc

    @property
    def traces_dir(self) -> Path:
        return self.home / "traces"


def default_home() -> Path:
    return Path(os.environ.get("PABLO_HOME", Path.home() / ".pablo"))


def find_config(start: Path | None = None) -> Path:
    """Find lanes.yaml: $PABLO_CONFIG, then ./config/lanes.yaml upward, then the packaged copy."""
    env = os.environ.get("PABLO_CONFIG")
    if env:
        return Path(env)
    here = (start or Path.cwd()).resolve()
    for folder in (here, *here.parents):
        candidate = folder / "config" / "lanes.yaml"
        if candidate.is_file():
            return candidate
    return Path(__file__).resolve().parents[2] / "config" / "lanes.yaml"


def load_env_file(path: Path) -> None:
    """Load KEY=VALUE lines from path. Variables already in the environment win."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").strip()
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key = key.strip()
        if not key or key in os.environ:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        os.environ[key] = value


def load_settings(path: Path | None = None) -> Settings:
    path = path or find_config()
    load_env_file(path.parent / ".env")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return Settings.model_validate(data)
