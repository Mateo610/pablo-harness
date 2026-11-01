"""Pick a lane for each task.

Two layers: fast keyword rules (free, explainable), then an optional model
classifier (the router lane) when the rules are unsure. The router never picks
the escalation lane on its own; only you can send work to Claude. It also skips
any lane marked `routable: false` (deep, since M4c), which stays available by hand.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from pablo.config import ESCALATION_LANE, Settings
from pablo.gateway import ChatBackend, Message
from pablo.tasks import estimate_complexity  # re-exported for callers

RULE_CONFIDENT = 0.6


@dataclass(frozen=True)
class RouteDecision:
    lane: str
    confidence: float
    reason: str
    complexity: str  # "quick" | "build" | "deep"


class Router:
    def __init__(self, settings: Settings, backend: ChatBackend | None = None) -> None:
        self.settings = settings
        self.backend = backend

    def _routable(self, name: str) -> bool:
        lane = self.settings.lanes.get(name)
        return lane is not None and lane.routable and name != ESCALATION_LANE

    def _rule_scores(self, task: str) -> dict[str, int]:
        text = task.lower()
        scores: dict[str, int] = {}
        for name, lane in self.settings.lanes.items():
            if not self._routable(name) or not lane.keywords:
                continue
            hits = sum(1 for kw in lane.keywords if re.search(rf"\b{re.escape(kw)}", text))
            if hits:
                scores[name] = hits
        return scores

    def route(self, task: str, *, lane: str | None = None) -> RouteDecision:
        complexity = estimate_complexity(task)
        if lane:
            self.settings.lane(lane)  # validates the name
            return RouteDecision(lane, 1.0, "picked by you", complexity)

        scores = self._rule_scores(task)
        if scores:
            best = max(scores, key=lambda k: scores[k])
            total = sum(scores.values())
            confidence = scores[best] / total if total else 0.0
            if complexity == "deep" and self._routable("deep") and best == "coder":
                best, confidence = "deep", max(confidence, 0.5)
            if confidence >= RULE_CONFIDENT:
                return RouteDecision(best, round(confidence, 2), f"keywords → {best}", complexity)

        if self.backend is not None:
            decided = self._ask_router_model(task)
            if decided is not None:
                return RouteDecision(decided[0], decided[1], "router model", complexity)

        fallback = "deep" if complexity == "deep" and self._routable("deep") else "coder"
        return RouteDecision(fallback, 0.5, "default lane", complexity)

    def _ask_router_model(self, task: str) -> tuple[str, float] | None:
        assert self.backend is not None
        choices = [
            n for n in self.settings.lanes if self._routable(n) and n not in ("router", "judge")
        ]
        messages: list[Message] = [
            {
                "role": "system",
                "content": (
                    "You route tasks for a team of models. Reply with JSON only: "
                    '{"lane": one of ' + json.dumps(choices) + ', "confidence": 0..1}'
                ),
            },
            {"role": "user", "content": task},
        ]
        router = self.settings.lane("router")
        reply = self.backend.complete(
            router.model,
            messages,
            temperature=router.temperature,
            top_p=router.top_p,
            max_tokens=64,
        )
        try:
            data = json.loads(_first_json(reply.text))
            lane = str(data["lane"])
            confidence = float(data.get("confidence", 0.5))
        except (ValueError, KeyError, TypeError):
            return None
        if lane not in choices:
            return None
        return lane, max(0.0, min(1.0, confidence))


def _first_json(text: str) -> str:
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise ValueError("no JSON object in reply")
    return match.group(0)
