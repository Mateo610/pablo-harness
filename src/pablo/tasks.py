"""What kind of task is this, and how hard? Pure functions, no model calls.

Ported from ai-coding-team (`context_manager.detect_task_type` and the keyword
vocabulary of `complexity_predictor.ComplexityPredictor`). The old predictor's 1-10
score lost to the harness's simpler rule on tests/data/complexity.yaml, so only its
design vocabulary survives, as a "deep" detector. The old predictor also sent high
scores to Claude automatically; here complexity only picks between local lanes.
"""

from __future__ import annotations

import re

# Checked in order: more specific types first (same priority as the original).
TASK_TYPES: list[tuple[str, tuple[str, ...]]] = [
    (
        "api",
        (
            "flask",
            "fastapi",
            "django rest",
            "express",
            "api endpoint",
            "rest api",
            "endpoint",
            "route",
            "graphql",
            "webhook",
        ),
    ),
    (
        "database",
        (
            "sqlalchemy",
            "django model",
            "database model",
            "orm",
            "migration",
            "schema",
            "database",
            "sql ",
        ),
    ),
    ("test", ("test", "pytest", "unittest", "assert", "coverage")),
    (
        "etl",
        (
            "etl",
            "data pipeline",
            "data processing",
            "data transformation",
            "data cleaning",
            "data migration",
        ),
    ),
    ("data", ("pandas", "numpy", "dataframe", "csv", "plot", "statistics", "analy")),
    (
        "bugfix",
        (
            "fix",
            "bug",
            "broken",
            "error",
            "crash",
            "fails",
            "failing",
            "exception",
            "wrong",
            "regression",
        ),
    ),
    (
        "refactor",
        (
            "refactor",
            "rename",
            "extract",
            "clean up",
            "cleanup",
            "simplify",
            "reorganize",
            "restructure",
            "dedupe",
        ),
    ),
    ("docs", ("docstring", "readme", "document", "comment", "changelog")),
    (
        "frontend",
        ("react", "component", "css", "html", "tsx", "jsx", "button", "page", "layout", "tailwind"),
    ),
    (
        "devops",
        (
            "docker",
            "systemd",
            "deploy",
            "ci ",
            "github actions",
            "workflow",
            "nginx",
            "kubernetes",
            "bash script",
        ),
    ),
    (
        "algorithm",
        (
            "algorithm",
            "sort",
            "search",
            "graph",
            "tree",
            "dynamic programming",
            "recursion",
            "complexity",
            "parse",
        ),
    ),
    ("crud", ("create", "read", "update", "delete", "crud", "add a function", "add function")),
]

COMPLEX_KEYWORDS = (
    "architecture",
    "design system",
    "refactor entire",
    "across the codebase",
    "optimize performance",
    "security audit",
    "distributed",
    "scalability",
    "migration",
    "redesign",
    "restructure",
    "multi-tenant",
    "microservice",
    "event-driven",
    "real-time",
    "data pipeline",
    "machine learning",
    "prove",
    "overnight",
    "research",
    "strategy",
)


def _has(text: str, keyword: str) -> bool:
    # Short keywords match whole words; phrases and stems match as substrings.
    if len(keyword) <= 4 and " " not in keyword.strip():
        return re.search(rf"\b{re.escape(keyword.strip())}\b", text) is not None
    return keyword in text


def detect_task_type(task: str) -> str:
    text = task.lower()
    for name, keywords in TASK_TYPES:
        if any(_has(text, kw) for kw in keywords):
            return name
    return "general"


def deep_signals(task: str) -> int:
    """How many independent signs say this is long, multi-part, or design work."""
    text = task.lower()
    hits = sum(1 for kw in COMPLEX_KEYWORDS if _has(text, kw))
    parts = len(re.findall(r"[,;:]", task)) + len(re.findall(r"\b(then|and)\b", text))
    breadth = re.search(r"\b(whole|entire|every|all)\b", text) is not None
    long_task = len(task.split()) > 120
    return hits + (parts >= 4) + breadth + 2 * long_task


def estimate_complexity(task: str) -> str:
    """quick / build / deep: which local lane effort the task deserves.

    quick/build use the original harness rule (short, no build verb = quick), which beat the
    ported 1-10 score on tests/data/complexity.yaml. Deep uses the legacy project's design
    vocabulary plus breadth and multi-part signals, where the original rule was weakest.
    """
    if deep_signals(task) >= 2:
        return "deep"
    if len(task.split()) < 12 and not re.search(
        r"\b(add|build|implement|refactor|create|write|make)\b", task, re.I
    ):
        return "quick"
    return "build"
