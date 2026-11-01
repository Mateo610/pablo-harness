"""Build the coder's prompt from capped sections so it always fits the context window.

Shape ported from ai-coding-team's `context_manager.py`: the prompt is a list of
sections (task, skill card, file tree, files, last error), each with its own token
cap, assembled under a total budget. Finding the right code is M6's job (repo map,
Serena); this module decides how much of it fits.
"""

from __future__ import annotations

import math
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import yaml

from pablo.tasks import detect_task_type

SKILLS_DIR = Path(__file__).resolve().parents[2] / "skills"
SKIP_SUFFIXES = (".lock", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".woff", ".woff2", ".ico")
MAX_TREE_FILES = 200
MAX_FILES = 6

# Default caps in tokens, sized for a 16k window with ~4k left for the reply.
BUDGET = {"task": 1000, "skill": 400, "error": 600, "tree": 800, "files": 8000}
TOTAL = 10_500


def tokens(text: str) -> int:
    """Rough token count: ~4 characters per token for English and code."""
    return math.ceil(len(text) / 4)


def clip(text: str, max_tokens: int) -> str:
    """Keep whole lines up to the cap and say how much was cut."""
    if tokens(text) <= max_tokens:
        return text
    kept: list[str] = []
    used = 0
    lines = text.splitlines()
    for line in lines:
        cost = tokens(line + "\n")
        if used + cost > max_tokens - 12:
            break
        kept.append(line)
        used += cost
    return "\n".join(kept) + f"\n… ({len(lines) - len(kept)} more lines cut to fit)"


@dataclass
class Section:
    name: str
    text: str
    max_tokens: int
    title: str = ""

    def render(self) -> str:
        body = clip(self.text.strip(), self.max_tokens)
        return f"{self.title}\n{body}" if self.title else body


def assemble(sections: list[Section], total: int = TOTAL) -> str:
    """Render sections in order. If the total is still too big, shrink `files` first."""
    sections = [s for s in sections if s.text.strip()]
    rendered = [s.render() for s in sections]
    over = sum(tokens(r) for r in rendered) - total
    if over > 0:
        for i, section in enumerate(sections):
            if section.name == "files":
                section.max_tokens = max(500, tokens(rendered[i]) - over)
                rendered[i] = section.render()
    return "\n\n".join(rendered)


# ---- skill cards -------------------------------------------------------------
@dataclass(frozen=True)
class Skill:
    name: str
    when: tuple[str, ...]
    keywords: tuple[str, ...]
    body: str


def load_skills(root: Path = SKILLS_DIR) -> list[Skill]:
    skills = []
    for path in sorted(root.glob("*.md")) if root.is_dir() else []:
        text = path.read_text("utf-8")
        match = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.S)
        if not match:
            continue
        meta = yaml.safe_load(match.group(1)) or {}
        skills.append(
            Skill(
                name=str(meta.get("name", path.stem)),
                when=tuple(meta.get("when", [])),
                keywords=tuple(str(k).lower() for k in meta.get("keywords", [])),
                body=match.group(2).strip(),
            )
        )
    return skills


def pick_skill(task: str, skills: list[Skill] | None = None) -> Skill | None:
    """At most one card: keyword hits first (most specific), then the task type."""
    skills = load_skills() if skills is None else skills
    text = task.lower()
    scored = [
        (sum(1 for kw in s.keywords if re.search(rf"\b{re.escape(kw)}", text)), s) for s in skills
    ]
    best = max(scored, key=lambda pair: pair[0], default=(0, None))
    if best[0] > 0:
        return best[1]
    kind = detect_task_type(task)
    return next((s for s in skills if kind in s.when), None)


# ---- repo files --------------------------------------------------------------
def list_files(repo: Path) -> list[str]:
    try:
        out = subprocess.run(
            ["git", "ls-files"], cwd=repo, text=True, capture_output=True, check=True
        ).stdout
        return sorted(out.splitlines())
    except (subprocess.CalledProcessError, FileNotFoundError):
        return sorted(
            str(p.relative_to(repo))
            for p in repo.rglob("*")
            if p.is_file() and ".git" not in p.parts
        )


def numbered(text: str) -> str:
    return "\n".join(f"{i:4}|{line}" for i, line in enumerate(text.splitlines(), 1))


def pick_files(listed: list[str], task: str) -> list[str]:
    """Files the task names; otherwise the small sources (M6's repo map replaces this)."""
    named = [f for f in listed if f in task or Path(f).name in task]
    if not named:
        named = [f for f in listed if not f.endswith(SKIP_SUFFIXES)]
    return named[:MAX_FILES]


def build_coder_context(repo: Path, task: str, *, last_error: str = "") -> str:
    listed = list_files(repo)
    blocks = []
    for rel in pick_files(listed, task):
        body = (repo / rel).read_text(encoding="utf-8", errors="replace")
        blocks.append(f"--- {rel}\n{numbered(body)}")
    skill = pick_skill(task)
    sections = [
        Section("task", task, BUDGET["task"]),
        Section("skill", skill.body if skill else "", BUDGET["skill"], "Guidance:"),
        Section("error", last_error, BUDGET["error"], "Last attempt failed with:"),
        Section("tree", "\n".join(listed[:MAX_TREE_FILES]), BUDGET["tree"], "Repo files:"),
        Section("files", "\n\n".join(blocks), BUDGET["files"]),
    ]
    return assemble(sections)
