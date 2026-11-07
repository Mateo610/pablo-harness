"""Projects: the repos Pablo works on, and starting a new one.

`pablo serve` starts in one repo (the default project). Every git repo directly under
`projects.root` (default ~/projects) is a project too, and the owner can start a new one there:
a folder, `git init`, a README with what it's about, and `.pablo/project.yaml`.

Each project gets its own Orchestrator (its own repo, checks and sandbox), all sharing one GPU
scheduler, one event bus and one home, so runs from every project queue for the same GPU and
show up in the same inbox.

A project's mode is fixed when it's made:

- standard     the full system: Base, Best, Hybrid, the judge, and Claude by escalation.
- unfiltered   one model end to end: the no-guardrails model plans, writes and judges. Deep
               and Claude are never used, so nothing in the project depends on a model that
               might refuse part of it. Checks still run in the sandbox (they can't refuse),
               your approval is still the only way into the repo, and nothing trains on it.
"""

from __future__ import annotations

import re
import subprocess
import threading
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import yaml

from pablo.config import Settings

if TYPE_CHECKING:
    from pablo.orchestrator import Orchestrator

Mode = Literal["standard", "unfiltered"]
UNFILTERED_LANE = "advisor"  # the no-guardrails model drives unfiltered projects
_SLUG = re.compile(r"[^a-z0-9]+")
GITIGNORE = "__pycache__/\n*.pyc\n.venv/\nnode_modules/\ndist/\n.env\n"
CHECKS_YAML = (
    "# Checks every change in this project must not make worse. Python files also get the\n"
    "# imports check automatically.\ntests: python -m pytest -q -p no:cacheprovider\n"
)


def slug(name: str) -> str:
    out = _SLUG.sub("-", name.lower()).strip("-")[:48].strip("-")
    if not out:
        raise ValueError("a project needs a name with letters or numbers")
    return out


@dataclass
class Project:
    name: str  # the folder name; conversations and runs refer to it
    path: str
    title: str = ""
    about: str = ""
    mode: Mode = "standard"
    default: bool = False  # the repo `pablo serve` started in
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def read_project(path: Path, *, default: bool = False) -> Project:
    meta: dict[str, Any] = {}
    file = path / ".pablo" / "project.yaml"
    if file.is_file():
        try:
            meta = yaml.safe_load(file.read_text("utf-8")) or {}
        except yaml.YAMLError:
            meta = {}
    mode = meta.get("mode", "standard")
    return Project(
        name=path.name,
        path=str(path),
        title=str(meta.get("title") or path.name),
        about=str(meta.get("about") or ""),
        mode=mode if mode in ("standard", "unfiltered") else "standard",
        default=default,
        created_at=str(meta.get("created_at") or ""),
    )


def unfiltered_settings(settings: Settings) -> Settings:
    """Settings where every code lane is the no-guardrails model and nothing leaves it.

    The lanes keep their names (the router, checks and judge code don't change), but coder,
    deep and judge all run the advisor's model, never train, and never retry elsewhere.
    Claude, the LoRA candidate and media are removed, so escalation is impossible here.
    """
    s = settings.model_copy(deep=True)
    base = s.lanes[UNFILTERED_LANE]
    coder = s.lanes["coder"]
    writer = base.model_copy(
        update={
            "writes_code": True,
            "routable": True,
            "train_ok": False,
            "retry_on": None,
            "retry_when": "off",
            "fix_rounds": max(1, coder.fix_rounds),
            "edit_format": "blocks",
            "temperature": coder.temperature,
            "top_p": coder.top_p,
            "max_tokens": max(base.max_tokens, coder.max_tokens),
            "context_tokens": coder.context_tokens,
            "symbol_lookup": False,
        }
    )
    for name in ("coder", "deep"):
        role = s.lanes[name].role if name in s.lanes else writer.role
        s.lanes[name] = writer.model_copy(update={"name": name, "role": role})
    s.lanes["judge"] = s.lanes["judge"].model_copy(
        update={"model": base.model, "display": base.display, "train_ok": False}
    )
    for gone in ("claude", "coder-candidate", "media"):
        s.lanes.pop(gone, None)
    s.learn.routing = False  # there's only one model to route to
    return s


class Workspace:
    """Every project Pablo knows, and an Orchestrator for each (made when first used)."""

    def __init__(self, base: Orchestrator, root: Path | None = None) -> None:
        self.base = base
        self.root = (root or base.settings.projects_root()).expanduser()
        self._orchs: dict[str, Orchestrator] = {base.repo.name: base}
        self._lock = threading.Lock()
        if read_project(base.repo).mode == "unfiltered":
            raise ValueError("start `pablo serve` in a standard project")

    @property
    def default(self) -> str:
        return self.base.repo.name

    def _paths(self) -> dict[str, Path]:
        found = {self.base.repo.name: self.base.repo}
        if self.root.is_dir():
            for child in sorted(self.root.iterdir()):
                if child.is_dir() and (child / ".git").exists() and child.name not in found:
                    found[child.name] = child.resolve()
        return found

    def projects(self) -> list[Project]:
        return [read_project(p, default=n == self.default) for n, p in self._paths().items()]

    def project(self, name: str = "") -> Project:
        name = name or self.default
        path = self._paths().get(name)
        if path is None:
            raise KeyError(f"no project {name!r}")
        return read_project(path, default=name == self.default)

    def orch(self, name: str = "") -> Orchestrator:
        name = name or self.default
        with self._lock:
            if name in self._orchs:
                return self._orchs[name]
        project = self.project(name)
        made = self._make(project)
        with self._lock:
            return self._orchs.setdefault(name, made)

    def for_run(self, run_id: str) -> Orchestrator:
        """The orchestrator for the project a run changes (runs before projects: the default)."""
        run = self.base.get(run_id)  # every project shares the home, so any one can read it
        try:
            return self.orch(run.project)
        except KeyError:
            return self.base

    def _make(self, project: Project) -> Orchestrator:
        from pablo.orchestrator import Orchestrator
        from pablo.sandbox import LocalSandbox
        from pablo.verify import load_checks

        b = self.base
        settings = b.settings if project.mode == "standard" else unfiltered_settings(b.settings)
        repo = Path(project.path)
        inner = getattr(b.backend, "inner", b.backend)  # the new one gets its own GPU wrapper
        orch = Orchestrator(
            settings,
            inner,
            repo=repo,
            bus=b.bus,
            checks=load_checks(repo),
            swap=b.swap,
            sandbox=LocalSandbox() if isinstance(b.sandbox, LocalSandbox) else None,
            gpu=b.gpu,
        )
        orch.retry_inline = b.retry_inline
        orch.checks_from_repo = True  # new projects gain their checks as code arrives
        return orch

    def create(self, title: str, about: str = "", mode: Mode = "standard") -> Project:
        """A new folder under the root with git, a README, and the project file. Nothing else:
        the code comes from runs you approve."""
        if mode not in ("standard", "unfiltered"):
            raise ValueError("mode is standard or unfiltered")
        name = slug(title)
        if name in self._paths():
            raise FileExistsError(f"there's already a project called {name}")
        path = self.root / name
        if path.exists():
            raise FileExistsError(f"{path} already exists")
        (path / ".pablo").mkdir(parents=True)
        meta = {"title": title.strip(), "about": about.strip(), "mode": mode,
                "created_at": datetime.now(UTC).isoformat(timespec="seconds")}  # fmt: skip
        (path / ".pablo" / "project.yaml").write_text(
            yaml.safe_dump(meta, sort_keys=False), "utf-8"
        )
        readme = f"# {title.strip()}\n"
        if about.strip():
            readme += f"\n{about.strip()}\n"
        (path / "README.md").write_text(readme, "utf-8")
        (path / ".gitignore").write_text(GITIGNORE, "utf-8")
        # Tests run from the first step; "no tests yet" isn't a failure until some exist.
        (path / ".pablo" / "checks.yaml").write_text(CHECKS_YAML, "utf-8")
        who = ["-c", "user.name=Pablo", "-c", "user.email=pablo@localhost"]
        for cmd in (["init", "-q", "-b", "main"], ["add", "-A"],
                    [*who, "commit", "-q", "-m", f"Start {title.strip()}"]):  # fmt: skip
            done = subprocess.run(["git", *cmd], cwd=path, capture_output=True, text=True)
            if done.returncode != 0:
                raise RuntimeError(f"git {' '.join(cmd)}: {done.stderr.strip()}")
        return read_project(path)
