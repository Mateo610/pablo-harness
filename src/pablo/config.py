"""Load lanes, gateway, and thresholds from config/lanes.yaml."""

from __future__ import annotations

import os
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

    @model_validator(mode="after")
    def _remote_never_trains(self) -> Lane:
        # Outputs from remote providers (Claude) are never training data.
        if self.kind == "remote" or self.name == ESCALATION_LANE:
            self.train_ok = False
            self.requires_approval = True
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


class Settings(BaseModel):
    gateway: Gateway = Field(default_factory=Gateway)
    thresholds: Thresholds = Field(default_factory=Thresholds)
    overnight: Overnight = Field(default_factory=Overnight)
    lanes: dict[str, Lane]
    home: Path = Field(default_factory=lambda: default_home())

    @model_validator(mode="after")
    def _name_lanes(self) -> Settings:
        for name, lane in self.lanes.items():
            lane.name = name
            if name == ESCALATION_LANE:
                lane.train_ok = False
                lane.requires_approval = True
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


def load_settings(path: Path | None = None) -> Settings:
    path = path or find_config()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return Settings.model_validate(data)
