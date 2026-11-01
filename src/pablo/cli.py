"""`pablo` command line: the same harness the cockpit uses, from a terminal."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from pablo import __version__
from pablo.config import Settings, load_settings
from pablo.demo import demo_backend, load_checks
from pablo.gateway import ChatBackend, LiteLLMBackend
from pablo.orchestrator import Orchestrator, Run
from pablo.plan import Plan, starter_plan
from pablo.traces import TraceStore

app = typer.Typer(
    help="Pablo: a private cockpit for a team of local AI models.", no_args_is_help=True
)
console = Console()

RepoOpt = Annotated[Path, typer.Option("--repo", "-C", help="Project folder")]
FakeOpt = Annotated[bool, typer.Option("--fake", help="Scripted models, no GPU or gateway")]


def _backend(settings: Settings, fake: bool) -> ChatBackend:
    if fake:
        return demo_backend()
    return LiteLLMBackend(settings.gateway.base_url, settings.gateway.api_key)


def _orch(repo: Path, fake: bool) -> Orchestrator:
    settings = load_settings()
    return Orchestrator(settings, _backend(settings, fake), repo=repo, checks=load_checks(repo))


def _show(run: Run) -> None:
    color = {"ready": "green", "needs_you": "yellow", "failed": "red"}.get(run.state, "white")
    console.print(f"[bold]{run.lane}[/] · {run.model} · run [cyan]{run.id}[/]")
    if run.explanation:
        console.print(run.explanation)
    if run.verdict:
        for check in run.verdict["checks"]:
            mark = "[green]passed[/]" if check["passed"] else "[red]FAILED[/]"
            console.print(f"  {check['name']}: {mark} ({check['duration_s']}s)")
        console.print(f"  diff: +{run.verdict['added']} −{run.verdict['removed']}")
    if run.certainty is not None:
        console.print(f"  judge certainty: {run.certainty:.2f}  {run.judge_summary}")
    console.print(
        f"[{color}]{run.state.replace('_', ' ')}[/]" + (f": {run.question}" if run.question else "")
    )
    if run.state in ("ready", "needs_you"):
        console.print(
            f"  pablo approve {run.id}  ·  pablo reject {run.id}  ·  pablo escalate {run.id}"
        )


@app.command()
def version() -> None:
    """Print the version."""
    console.print(__version__)


@app.command()
def init(repo: RepoOpt = Path("."), name: str = "") -> None:
    """Create .pablo/plan.yaml in a project."""
    target = repo.resolve()
    if (target / ".pablo" / "plan.yaml").exists():
        console.print("[yellow].pablo/plan.yaml already exists[/]")
        raise typer.Exit(1)
    path = starter_plan(name or target.name).save(target)
    console.print(f"Wrote {path}. Edit the goal and phases, then `pablo plan`.")


@app.command()
def lanes() -> None:
    """Show the team."""
    table = Table("lane", "model", "kind", "trains", "role")
    for lane in load_settings().lanes.values():
        table.add_row(lane.name, lane.model, lane.kind, "yes" if lane.train_ok else "no", lane.role)
    console.print(table)


@app.command()
def plan(repo: RepoOpt = Path(".")) -> None:
    """Show phases, tasks, and gates."""
    current = Plan.load(repo.resolve())
    console.print(f"[bold]{current.project}[/]  {current.goal}")
    for phase in current.phases:
        done, total = phase.progress()
        console.print(f"\n[bold]Phase {phase.id} · {phase.title}[/]  {done}/{total}")
        for task in phase.tasks:
            console.print(f"  {task.id:<5} {task.status:<10} {task.title}")
        console.print(f"  [dim]Gate: {phase.gate}[/]")
    nxt = current.next_task()
    if nxt:
        console.print(f"\nNext: {nxt.id} {nxt.title}")


@app.command()
def run(
    task: Annotated[str, typer.Argument(help="What you want done, or a plan task id like 1.2")],
    repo: RepoOpt = Path("."),
    lane: Annotated[str | None, typer.Option(help="Force a lane")] = None,
    fake: FakeOpt = False,
) -> None:
    """Route a task, do it, verify it, judge it."""
    root = repo.resolve()
    task_id = None
    try:
        current = Plan.load(root)
        planned = current.task(task)
        task_id, task = planned.id, planned.title
        current.set_status(planned.id, "active")
        current.save(root)
    except (FileNotFoundError, KeyError):
        pass
    result = _orch(root, fake).start(task, lane=lane, task_id=task_id)
    _show(result)


@app.command()
def approve(run_id: str, repo: RepoOpt = Path("."), fake: FakeOpt = False) -> None:
    """Apply a run's diff to your repo and mark it trainable."""
    result = _orch(repo.resolve(), fake).approve(run_id)
    if result.task_id:
        try:
            current = Plan.load(repo.resolve())
            current.set_status(result.task_id, "done")
            current.save(repo.resolve())
        except (FileNotFoundError, KeyError):
            pass
    console.print(f"[green]approved[/] {run_id}")


@app.command()
def reject(run_id: str, repo: RepoOpt = Path("."), fake: FakeOpt = False) -> None:
    """Discard a run. Nothing in your repo changes."""
    _orch(repo.resolve(), fake).reject(run_id)
    console.print(f"rejected {run_id}")


@app.command()
def escalate(run_id: str, repo: RepoOpt = Path("."), fake: FakeOpt = False) -> None:
    """Send a run's task to the escalation lane (Claude). Never used as training data."""
    _show(_orch(repo.resolve(), fake).escalate(run_id))


@app.command()
def serve(
    repo: RepoOpt = Path("."),
    host: Annotated[str, typer.Option(help="127.0.0.1, or your Tailscale IP")] = "127.0.0.1",
    port: int = 8710,
    fake: FakeOpt = False,
) -> None:
    """Run the harness API (and the built cockpit, if web/dist exists)."""
    import uvicorn
    from fastapi.staticfiles import StaticFiles

    from pablo.api import create_app

    if host in ("0.0.0.0", "::"):
        console.print(
            "[red]Refusing to bind every interface. Use 127.0.0.1 or your Tailscale IP.[/]"
        )
        raise typer.Exit(2)
    api = create_app(_orch(repo.resolve(), fake))
    dist = Path(__file__).resolve().parents[2] / "web" / "dist"
    if dist.is_dir():
        api.mount("/", StaticFiles(directory=dist, html=True), name="cockpit")
    console.print(f"Pablo on http://{host}:{port}" + ("  (fake models)" if fake else ""))
    uvicorn.run(api, host=host, port=port, log_level="warning")


@app.command()
def tui(
    url: Annotated[str, typer.Option(help="Harness API, e.g. http://<tailscale-ip>:8710")] = (
        "http://127.0.0.1:8710"
    ),
) -> None:
    """The cockpit in your terminal (needs `pablo serve` running)."""
    from pablo.tui import run_tui

    run_tui(url)


@app.command()
def doctor() -> None:
    """Check the gateway, GPU, and disk."""
    settings = load_settings()
    ok = LiteLLMBackend(settings.gateway.base_url, settings.gateway.api_key).health()
    console.print(
        f"gateway {settings.gateway.base_url}: " + ("[green]up[/]" if ok else "[red]down[/]")
    )
    if shutil.which("nvidia-smi"):
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.used,memory.total", "--format=csv,noheader"],
            text=True,
            capture_output=True,
            check=False,
        ).stdout.strip()
        console.print(f"gpu: {out or 'nvidia-smi returned nothing'}")
    else:
        console.print("gpu: [yellow]nvidia-smi not found[/]")
    usage = shutil.disk_usage(settings.home if settings.home.exists() else Path.home())
    console.print(f"disk free: {usage.free / 1e9:.0f} GB")


@app.command("export-training")
def export_training(out: Path = Path("training/approved.jsonl")) -> None:
    """Export approved, trainable traces. Escalation traces are always excluded."""
    count = TraceStore(load_settings().traces_dir).export_training(out)
    console.print(f"wrote {count} examples to {out}")
