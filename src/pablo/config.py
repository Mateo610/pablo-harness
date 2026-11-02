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
    # Sampling. Most lanes stay at 0.2. A lane sets top_p only when its model card requires it.
    temperature: float = 0.2
    top_p: float | None = None

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


class Settings(BaseModel):
    gateway: Gateway = Field(default_factory=Gateway)
    thresholds: Thresholds = Field(default_factory=Thresholds)
    overnight: Overnight = Field(default_factory=Overnight)
    sandbox: SandboxConfig = Field(default_factory=SandboxConfig)
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
        return self

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
