"""Practice: Pablo makes checkable tasks from your repos, attempts them, keeps what's right.

A practice task takes a function that the repo's own tests exercise and breaks it:

- reimplement  the body is replaced with `raise NotImplementedError`; the task says what it
               did (its docstring) and that the tests describe it.
- bugfix       one small mutation (`<=` → `<`, `and` → `or`, `+ 1` dropped, ...) that makes
               those tests fail; the task quotes the failing assertion.

The coder attempts it in a throwaway copy, as a normal run at night urgency. The answer key is
the repo's original tests, restored before scoring: a fix that edits tests, conftest, or
sitecustomize is thrown out. When the coder fails, deep can try (`teacher`). Passing fixes
become examples; failures become failure notes; both become lane outcomes.

Never on the eval suite: suite repos are refused, and any function a suite task or solution
names is skipped in every repo, so practice can't leak into evals (AGENTS.md rule 7).
"""

from __future__ import annotations

import ast
import random
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pablo.gpu import Priority
from pablo.gpu import job as gpu_job
from pablo.lessons import Example, Failure, Lessons, Outcome, first_error
from pablo.sandbox import Sandbox
from pablo.verify import IGNORE, apply_diff, run_check

if TYPE_CHECKING:
    from pablo.orchestrator import Orchestrator

Kind = Literal["reimplement", "bugfix"]
MUTATIONS: list[tuple[str, str]] = [
    (r"(?<![<>=!])<=(?!=)", "<"),
    (r"(?<![<>=!-])<(?![<=])", "<="),
    (r"(?<![<>=!])>=(?!=)", ">"),
    (r"(?<![<>=!-])>(?![>=])", ">="),
    (r"==", "!="),
    (r"\s\+\s1\b", ""),
    (r"\s-\s1\b", ""),
    (r"\band\b", "or"),
    (r"\bmin\(", "max("),
    (r"\bmax\(", "min("),
]
CHEAT = re.compile(
    r"^(?:\+\+\+|---) (?:a/|b/)?(?:.*/)?(?:test_[^/]*\.py|[^/]*_test\.py|conftest\.py|"
    r"sitecustomize\.py|pytest\.ini|setup\.cfg|tox\.ini)$",
    re.M,
)


@dataclass
class Target:
    path: str  # repo-relative file
    name: str
    qualname: str
    start: int  # 1-based line of `def`
    body_start: int  # first body line after the docstring
    end: int
    doc: str
    indent: str
    tests: list[str] = field(default_factory=list)


@dataclass
class PracticeResult:
    repo: str
    kind: Kind
    target: str
    task: str
    passed: bool = False
    lane: str = ""
    tried: list[str] = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def is_test_file(rel: str) -> bool:
    name = Path(rel).name
    return name.startswith("test_") or name.endswith("_test.py") or "/tests/" in f"/{rel}"


def suite_names() -> set[str]:
    """Every identifier a suite task or its solution mentions: practice never touches these."""
    try:
        from pablo.evals import load_suite
    except Exception:
        return set()
    names: set[str] = set()
    for task in load_suite():
        text = task.task + "\n" + "\n".join(task.solution.values())
        names.update(re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", text))
    return names


def tracked_py(repo: Path) -> list[str]:
    try:
        out = subprocess.run(["git", "ls-files", "*.py"], cwd=repo, capture_output=True,
                             text=True, timeout=20, check=True).stdout  # fmt: skip
        return sorted(line for line in out.splitlines() if line)
    except (OSError, subprocess.SubprocessError):
        return sorted(str(p.relative_to(repo)) for p in repo.rglob("*.py"))


def find_targets(repo: Path, exclude: set[str]) -> list[Target]:
    files = tracked_py(repo)
    tests = {f: (repo / f).read_text("utf-8", errors="replace") for f in files if is_test_file(f)}
    out: list[Target] = []
    for rel in files:
        if is_test_file(rel) or Path(rel).name in ("conftest.py", "setup.py", "__init__.py"):
            continue
        try:
            source = (repo / rel).read_text("utf-8")
            tree = ast.parse(source)
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        lines = source.splitlines()
        for node, owner in _functions(tree):
            if node.name.startswith("_") or node.name in exclude or node.end_lineno is None:
                continue
            body = node.body
            doc = ast.get_docstring(node) or ""
            first = body[1] if doc and len(body) > 1 else body[0]
            if doc and len(body) == 1:
                continue  # only a docstring
            if node.end_lineno - first.lineno < 1 and not doc:
                continue  # one-liners make poor practice
            used_in = sorted(t for t, text in tests.items() if re.search(rf"\b{node.name}\b", text))
            if not used_in:
                continue
            line = lines[first.lineno - 1]
            out.append(Target(rel, node.name, f"{owner}{node.name}", node.lineno, first.lineno,
                              node.end_lineno, doc, line[: len(line) - len(line.lstrip())],
                              used_in))  # fmt: skip
    return out


def _functions(tree: ast.Module) -> list[tuple[ast.FunctionDef | ast.AsyncFunctionDef, str]]:
    out: list[tuple[ast.FunctionDef | ast.AsyncFunctionDef, str]] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.append((node, ""))
        elif isinstance(node, ast.ClassDef):
            out += [(n, f"{node.name}.") for n in node.body
                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]  # fmt: skip
    return out


def break_function(source: str, t: Target, kind: Kind, rng: random.Random) -> list[str]:
    """Candidate broken versions of the file (the first that makes the tests fail is used)."""
    lines = source.splitlines(keepends=True)
    if kind == "reimplement":
        new = [
            *lines[: t.body_start - 1],
            f"{t.indent}raise NotImplementedError\n",
            *lines[t.end :],
        ]
        return ["".join(new)]
    out = []
    body = list(range(t.body_start - 1, t.end))
    muts = MUTATIONS[:]
    rng.shuffle(muts)
    for pattern, repl in muts:
        for i in body:
            if lines[i].lstrip().startswith("#"):
                continue
            changed = re.sub(pattern, repl, lines[i], count=1)
            if changed != lines[i]:
                out.append("".join([*lines[:i], changed, *lines[i + 1 :]]))
                break
    return out


def _snapshot(src: Path, dst: Path) -> None:
    shutil.copytree(src, dst, ignore=IGNORE)
    git = ["git", "-c", "user.name=pablo", "-c", "user.email=pablo@localhost"]
    subprocess.run([*git, "init", "-q"], cwd=dst, check=True)
    subprocess.run([*git, "add", "-A"], cwd=dst, check=True)
    subprocess.run([*git, "commit", "-qm", "practice"], cwd=dst, check=True)


def pytest_on(checks: dict[str, list[str]], files: list[str]) -> list[str] | None:
    for command in checks.values():
        if any("pytest" in part for part in command):
            keep = [p for p in command if not p.startswith(("tests", "test/")) and p != "."]
            return [*keep, "-p", "no:cacheprovider", "--noconftest", "-o", "addopts=", *files]
    return None


def score(
    original: Path, broken: Path, diff: str, t: Target, command: list[str], box: Sandbox
) -> tuple[bool, str]:
    """Apply the fix to the broken copy, put the original tests back, run them."""
    if CHEAT.search(diff):
        return False, "the fix edits tests or test config"
    with tempfile.TemporaryDirectory(prefix="pablo-practice-score-") as tmp:
        scratch = Path(tmp) / "work"
        shutil.copytree(broken, scratch, ignore=IGNORE)
        ok, err = apply_diff(scratch, diff)
        if not ok:
            return False, f"didn't apply: {err}"
        for rel in t.tests:
            shutil.copy2(original / rel, scratch / rel)
        check = run_check("practice", command, scratch, 300, box)
        return check.passed, check.output_tail


class Practice:
    """One practice session against one repo, using the live orchestrator's GPU scheduler."""

    def __init__(
        self,
        orch: Orchestrator,
        *,
        lessons: Lessons | None = None,
        teacher: str | None = "deep",
        seed: int = 0,
        exclude: set[str] | None = None,
        on_result: Callable[[PracticeResult], None] | None = None,
    ) -> None:
        self.orch = orch
        self.lessons = lessons or Lessons(orch.settings.home)
        self.teacher = teacher if teacher in orch.settings.lanes else None
        self.rng = random.Random(seed)
        self.exclude = suite_names() if exclude is None else exclude
        self.on_result = on_result
        self.key = ""

    def session(
        self,
        repo: Path,
        n: int = 10,
        kinds: tuple[Kind, ...] = ("reimplement", "bugfix"),
        *,
        key: str = "",
        allow_suite: bool = False,
    ) -> list[PracticeResult]:
        """Up to `n` checkable tasks from `repo`. `key` names the repo in the lessons."""
        from pablo.demo import load_checks
        from pablo.evals import SUITE

        repo = repo.resolve()
        if not allow_suite and (SUITE.resolve() in repo.parents or repo == SUITE.resolve()):
            raise ValueError("practice never runs on the eval suite's repos")
        self.key = key or repo.name
        checks = load_checks(repo)
        targets = find_targets(repo, self.exclude)
        self.rng.shuffle(targets)
        results: list[PracticeResult] = []
        for t in targets:
            if len(results) >= n:
                break
            kind = kinds[len(results) % len(kinds)]
            result = self._one(repo, t, kind, checks)
            if result is not None:
                results.append(result)
                if self.on_result:
                    self.on_result(result)
        return results

    def _one(
        self, repo: Path, t: Target, kind: Kind, checks: dict[str, list[str]]
    ) -> PracticeResult | None:
        from pablo.config import Settings
        from pablo.orchestrator import Orchestrator

        command = pytest_on(checks, t.tests)
        if command is None:
            return None
        source = (repo / t.path).read_text("utf-8")
        with tempfile.TemporaryDirectory(prefix="pablo-practice-") as tmp:
            work = Path(tmp)
            broken: Path | None = None
            failing = ""
            for candidate in break_function(source, t, kind, self.rng):
                trial = work / f"broken-{self.rng.randrange(1 << 30)}"
                shutil.copytree(repo, trial, ignore=IGNORE)
                (trial / t.path).write_text(candidate, encoding="utf-8")
                check = run_check("practice", command, trial, 300, self.orch.sandbox)
                if not check.passed:
                    broken, failing = trial, check.output_tail
                    break
                shutil.rmtree(trial)
            if broken is None:
                return None  # no break the tests notice: not a checkable task
            base = work / "task"
            _snapshot(broken, base)
            task = self._task_text(t, kind, failing)
            result = PracticeResult(self.key, kind, f"{t.path}:{t.qualname}", task)
            isolated: Settings = self.orch.settings.model_copy(
                update={"home": work / "home"}, deep=True
            )
            for cfg in isolated.lanes.values():
                cfg.retry_when = "off"
            isolated.spec.enabled = False
            # The raw backend plus the live scheduler: wrapped once, sharing the one GPU queue.
            raw = getattr(self.orch.backend, "inner", self.orch.backend)
            orch = Orchestrator(isolated, raw, repo=base, checks=checks,
                                sandbox=self.orch.sandbox, gpu=self.orch.gpu)  # fmt: skip
            orch.retry_inline = True
            for lane in ["coder", *([self.teacher] if self.teacher else [])]:
                result.tried.append(lane)
                with gpu_job(Priority.NIGHT, "practice"):
                    run = orch.start(task, lane=lane)
                ok, tail = (score(repo, base, run.diff, t, command, self.orch.sandbox)
                            if run.diff else (False, "no diff"))  # fmt: skip
                self.lessons.add_outcome(Outcome(self.key, kind, lane, ok, "practice"))
                if ok:
                    result.passed, result.lane = True, lane
                    self.lessons.add_example(Example(self.key, task, run.diff, lane, "practice",
                                                     kind, [t.path]))  # fmt: skip
                    break
                result.reason = first_error(tail) or tail[-200:]
                self.lessons.add_failure(Failure(self.key, task, lane, result.reason, [t.path]))
            return result

    @staticmethod
    def _task_text(t: Target, kind: Kind, failing: str) -> str:
        where = f"`{t.qualname}` in `{t.path}`"
        tests = ", ".join(f"`{x}`" for x in t.tests[:3])
        if kind == "reimplement":
            what = f" What it does: {t.doc.strip().splitlines()[0]}" if t.doc.strip() else ""
            return (f"{where} lost its body (it raises NotImplementedError). Implement it again so "
                    f"{tests} pass. Don't change the tests.{what}")  # fmt: skip
        assertion = first_error(failing)
        return (f"The tests in {tests} fail: {assertion}. The bug is in {where}. Fix it there "
                "without changing the tests.")  # fmt: skip
