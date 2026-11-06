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
from typing import Any

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
# Never context: environments, caches, build output. Matters when the repo isn't under
# git (the M4c ledger sandbox), where the fallback walk would otherwise list .venv.
NOT_SOURCE_DIRS = frozenset(
    {".git", ".venv", "venv", "env", "node_modules", "__pycache__", ".mypy_cache",
     ".pytest_cache", ".ruff_cache", ".tox", "dist", "build", "site-packages", ".pablo"}
)  # fmt: skip


def list_files(repo: Path) -> list[str]:
    try:
        out = subprocess.run(
            ["git", "ls-files"], cwd=repo, text=True, capture_output=True, check=True
        ).stdout
        return sorted(out.splitlines())
    except (subprocess.CalledProcessError, FileNotFoundError):
        found = []
        for path in repo.rglob("*"):
            rel = path.relative_to(repo)
            if path.is_file() and not NOT_SOURCE_DIRS.intersection(rel.parts):
                found.append(str(rel))
        return sorted(found)


def numbered(text: str) -> str:
    return "\n".join(f"{i:4}|{line}" for i, line in enumerate(text.splitlines(), 1))


# ---- which files the task is about -----------------------------------------
# A stopgap until M6's repo map and symbol lookup: find files from what the task
# names (paths, `module.function`, snake_case and CamelCase symbols, backticked
# words), then the files that use those symbols (usually the tests), then the
# rest by how many of the task's words they contain.
READ_SUFFIXES = (
    ".py", ".pyi", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java", ".rb",
    ".md", ".yaml", ".yml", ".toml", ".sh", ".css", ".html", ".txt", ".cfg", ".ini",
)  # fmt: skip
MAX_READ_BYTES = 200_000
STOPWORDS = frozenset(
    {
        "this",
        "that",
        "with",
        "from",
        "into",
        "when",
        "then",
        "than",
        "they",
        "them",
        "their",
        "there",
        "what",
        "which",
        "while",
        "have",
        "should",
        "would",
        "could",
        "must",
        "only",
        "also",
        "make",
        "keep",
        "every",
        "each",
        "some",
        "more",
        "most",
        "less",
        "does",
        "done",
        "file",
        "files",
        "code",
        "test",
        "tests",
        "task",
        "change",
        "changes",
        "using",
        "used",
        "about",
        "after",
        "before",
        "under",
        "over",
        "still",
        "same",
        "other",
        "add",
        "fix",
    }
)


@dataclass(frozen=True)
class TaskRefs:
    stems: frozenset[str]  # module names: `report` from report.monthly
    symbols: frozenset[str]  # functions, classes, variables
    words: frozenset[str]  # plain words for ranking the rest


def task_refs(task: str) -> TaskRefs:
    stems: set[str] = set()
    symbols: set[str] = set(re.findall(r"`([A-Za-z_][A-Za-z0-9_]*)`", task))
    for raw in re.findall(r"[A-Za-z_][A-Za-z0-9_.]*", task):
        token = raw.strip(".")
        if "." in token:
            parts = [p for p in token.split(".") if len(p) >= 3]
            if len(parts) >= 2 and parts[-1] not in ("py", "ts", "tsx", "js", "md", "yaml"):
                stems.update(parts[:-1])
                symbols.add(parts[-1])
            elif parts:
                stems.add(parts[0])
        elif "_" in token.strip("_") or re.fullmatch(r"(?:[A-Z][a-z0-9]+){2,}", token):
            symbols.add(token)
    words = {w for w in re.findall(r"[a-z][a-z0-9]{3,}", task.lower()) if w not in STOPWORDS}
    return TaskRefs(frozenset(stems), frozenset(symbols), frozenset(words))


def _defines(text: str, symbol: str) -> bool:
    s = re.escape(symbol)
    pattern = (
        rf"^\s*(?:async\s+)?(?:def|class)\s+{s}\b"  # Python
        rf"|^\s*{s}\s*(?::[^=\n]*)?=(?!=)"  # module-level assignment
        rf"|\b(?:function|const|let|var|class|interface|type|enum)\s+{s}\b"  # TS/JS
    )
    return re.search(pattern, text, re.M) is not None


def _uses(text: str, refs: TaskRefs) -> bool:
    if any(re.search(rf"\b{re.escape(sym)}\b", text) for sym in refs.symbols):
        return True
    return any(
        re.search(rf"^\s*(?:from|import)\s+[\w.]*\b{re.escape(stem)}\b", text, re.M)
        for stem in refs.stems
    )


def pick_files(
    listed: list[str],
    task: str,
    repo: Path | None = None,
    max_files: int = MAX_FILES,
    *,
    repo_map: Any | None = None,
) -> list[str]:
    """Files the task is about, most relevant first, at most `max_files`.

    Order: files the task names by path, modules it names (`report` in report.monthly),
    files that define a symbol it names, files that use those symbols, then the rest
    ranked by the repo map (BM25 over symbols) when present, else by task-word hits.
    Without `repo` only paths are matched.
    """
    named = [f for f in listed if f in task or Path(f).name in task]
    if repo is None:
        if not named:
            named = [f for f in listed if not f.endswith(SKIP_SUFFIXES)]
        return named[:max_files]

    refs = task_refs(task)
    readable = [f for f in listed if f.endswith(READ_SUFFIXES)]
    cache: dict[str, str] = {}

    def text(rel: str) -> str:
        if rel not in cache:
            path = repo / rel
            try:
                data = path.read_bytes()[:MAX_READ_BYTES] if path.is_file() else b""
            except OSError:
                data = b""
            cache[rel] = data.decode("utf-8", errors="replace")
        return cache[rel]

    modules = [f for f in readable if Path(f).stem in refs.stems]
    definers = [f for f in readable if any(_defines(text(f), sym) for sym in refs.symbols)]
    picked: list[str] = []
    for rel in [*named, *modules, *definers]:
        if rel not in picked:
            picked.append(rel)
    if refs.symbols or refs.stems:
        for rel in readable:
            if rel not in picked and _uses(text(rel), refs):
                picked.append(rel)

    # A file that quotes the task (an eval or plan listing it) is the question, not the code.
    quote = " ".join(task.split())[:60]
    rest = [f for f in readable if f not in picked and quote not in " ".join(text(f).split())]
    if repo_map is not None:
        rest = repo_map.rank_files(task, rest)
    else:

        def score(rel: str) -> tuple[int, int]:
            body = text(rel).lower()
            path = rel.lower()
            hits = sum(3 if w in path else 1 for w in refs.words if w in path or w in body)
            return (-hits, len(body))

        rest = sorted(rest, key=score)
        if max_files <= MAX_FILES:  # a small window keeps only files that share task words
            rest = [f for f in rest if score(f)[0] < 0] or (rest if not picked else [])
    return [*picked, *rest][:max_files]


@dataclass(frozen=True)
class Budget:
    """Token caps for one prompt. `for_window` scales the files section with the lane."""

    total: int = TOTAL
    files: int = BUDGET["files"]
    tree: int = BUDGET["tree"]
    max_files: int = MAX_FILES

    @classmethod
    def for_window(cls, total: int) -> Budget:
        if total <= TOTAL:
            return cls()
        extra = total - TOTAL
        tree = min(BUDGET["tree"] + extra // 10, 3000)
        files = BUDGET["files"] + extra - (tree - BUDGET["tree"])
        return cls(total=total, files=files, tree=tree, max_files=60)

    def shrunk(self, tokens_out: int) -> Budget:
        """The same budget with `tokens_out` fewer tokens of files, for prompts that add more."""
        if tokens_out <= 0:
            return self
        files = max(2500, self.files - tokens_out)
        return Budget(total=self.total - (self.files - files), files=files, tree=self.tree,
                      max_files=self.max_files)  # fmt: skip

    @property
    def per_file(self) -> int:
        """No single file may eat more than a third of the files section."""
        return max(1500, self.files // 3)


def context_files(
    repo: Path,
    task: str,
    budget: Budget | None = None,
    listed: list[str] | None = None,
    *,
    repo_map: Any | None = None,
    home: Path | None = None,
) -> list[str]:
    """The files a prompt for this task will carry, in order (shown in the run's replay)."""
    budget = budget or Budget()
    files = listed if listed is not None else list_files(repo)
    rmap = repo_map
    if rmap is None:
        from pablo.repomap import load_or_build

        rmap = load_or_build(repo, home=home, listed=files)
    return pick_files(files, task, repo, budget.max_files, repo_map=rmap)


def build_coder_context(
    repo: Path,
    task: str,
    *,
    last_error: str = "",
    budget: Budget | None = None,
    home: Path | None = None,
) -> str:
    budget = budget or Budget()
    listed = list_files(repo)
    from pablo.repomap import load_or_build

    rmap = load_or_build(repo, home=home, listed=listed)
    blocks = []
    for rel in context_files(repo, task, budget, listed, repo_map=rmap, home=home):
        body = (repo / rel).read_text(encoding="utf-8", errors="replace")
        blocks.append(f"--- {rel}\n{clip(numbered(body), budget.per_file)}")
    skill = pick_skill(task)
    tree = rmap.render() if rmap.symbols else "\n".join(listed[:MAX_TREE_FILES])
    sections = [
        Section("task", task, BUDGET["task"]),
        Section("skill", skill.body if skill else "", BUDGET["skill"], "Guidance:"),
        Section("error", last_error, BUDGET["error"], "Last attempt failed with:"),
        Section("tree", tree, budget.tree, "Repo map:" if rmap.symbols else "Repo files:"),
        Section("files", "\n\n".join(blocks), budget.files),
    ]
    return assemble(sections, budget.total)
