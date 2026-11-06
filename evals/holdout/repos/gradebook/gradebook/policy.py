"""How scores turn into a grade: category weights and the late penalty."""

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Policy:
    weights: dict[str, float]  # category -> weight; the weights add up to 1
    late_penalty: float = 0.1  # fraction of the points lost per late day
    drop_lowest: dict[str, int] = field(default_factory=dict)  # category -> how many to drop


def load_policy(data: dict[str, Any]) -> Policy:
    weights = {str(k): float(v) for k, v in data["weights"].items()}
    if abs(sum(weights.values()) - 1.0) > 1e-6:
        raise ValueError("weights must add up to 1")
    return Policy(
        weights=weights,
        late_penalty=float(data.get("late_penalty", 0.1)),
        drop_lowest={str(k): int(v) for k, v in data.get("drop_lowest", {}).items()},
    )
