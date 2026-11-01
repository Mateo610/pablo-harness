"""`pablo` command line: the same harness the cockpit uses, from a terminal."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.table import Table

from pablo import __version__
from pablo.config import Settings, load_settings
from pablo.demo import demo_backend, load_checks
from pablo.gateway import ChatBackend, LiteLLMBackend
from pablo.orchestrator import Orchestrator, Run
from pablo.plan import Plan, starter_plan
from pablo.sandbox import LocalSandbox, make_sandbox
from pablo.swap import SwapView
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
    return Orchestrator(
        settings,
        _backend(settings, fake),
        repo=repo,
        checks=load_checks(repo),
        swap=None if fake else SwapView(),
        # Scripted models write only our own demo diffs, so --fake checks run locally.
        sandbox=LocalSandbox() if fake else None,
    )


def _show(run: Run) -> None:
    color = {"ready": "green", "needs_you": "yellow", "failed": "red"}.get(run.state, "white")
    console.print(f"[bold]{run.lane}[/] · {run.model} · run [cyan]{run.id}[/]")
    if run.explanation:
        console.print(run.explanation)
    if run.verdict:
        for check in run.verdict["checks"]:
            status = check.get("status", "passed" if check["passed"] else "failed")
            mark = {
                "passed": "[green]passed[/]",
                "already failing": "[yellow]failed before this diff too[/]",
                "regressed": "[red]FAILED (broken by this diff)[/]",
            }.get(status, f"[red]FAILED ({status})[/]")
            console.print(f"  {check['name']}: {mark} ({check['duration_s']}s)")
        console.print(f"  diff: +{run.verdict['added']} −{run.verdict['removed']}")
        if run.verdict.get("sandbox"):
            console.print(f"  sandbox: {run.verdict['sandbox']}")
        for note in run.verdict.get("notes") or []:
            console.print(f"  note: {note}")
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
def approve(
    run_id: str,
    repo: RepoOpt = Path("."),
    fake: FakeOpt = False,
    hunks: Annotated[
        str | None, typer.Option(help="Keep only these hunks, e.g. 0:0,1:2 (see `pablo hunks`)")
    ] = None,
    force: Annotated[bool, typer.Option(help="Apply kept hunks even if their checks fail")] = False,
) -> None:
    """Apply a run's diff (or some of its hunks) to your repo."""
    from pablo.orchestrator import PartialChecksFailed

    keep = [h.strip() for h in hunks.split(",")] if hunks else None
    try:
        result = _orch(repo.resolve(), fake).approve(run_id, keep, force=force)
    except PartialChecksFailed as exc:
        console.print(f"[red]{exc}[/]  (use --force to apply anyway)")
        raise typer.Exit(1) from exc
    if result.task_id:
        try:
            current = Plan.load(repo.resolve())
            current.set_status(result.task_id, "done")
            current.save(repo.resolve())
        except (FileNotFoundError, KeyError):
            pass
    console.print(f"[green]approved[/] {run_id}")


@app.command("hunks")
def show_hunks(run_id: str, repo: RepoOpt = Path("."), fake: FakeOpt = False) -> None:
    """List a run's hunks with their ids, for `pablo approve --hunks`."""
    from rich.syntax import Syntax

    from pablo.hunks import parse

    run_ = _orch(repo.resolve(), fake).get(run_id)
    for f in parse(run_.diff):
        for h in f.hunks:
            console.print(f"[bold cyan]{h.id}[/]  {f.path}  +{h.added} −{h.removed}")
            console.print(Syntax("\n".join([h.header, *h.lines]), "diff", theme="ansi_dark"))


@app.command()
def compare(
    task: str,
    lanes: Annotated[str, typer.Option(help="Comma-separated lanes")] = "coder,deep",
    repo: RepoOpt = Path("."),
    fake: FakeOpt = False,
) -> None:
    """Run one task on several lanes side by side. Approving one rejects the rest."""
    for result in _orch(repo.resolve(), fake).compare(task, [x.strip() for x in lanes.split(",")]):
        _show(result)
        console.print()


@app.command()
def replay(run_id: str, repo: RepoOpt = Path("."), fake: FakeOpt = False) -> None:
    """Show everything that happened in a run, in order."""
    from datetime import datetime as dt

    events = _orch(repo.resolve(), fake).events(run_id)
    if not events:
        console.print("No events recorded for this run.")
        return
    t0 = dt.fromisoformat(events[0]["at"])
    for e in events:
        offset = (dt.fromisoformat(e["at"]) - t0).total_seconds()
        data = {k: v for k, v in e["data"].items() if k != "lane"}
        detail = data.get("text") or data.get("summary") or data.get("reason") or data
        lane = e["data"].get("lane", "")
        console.print(f"[dim]+{offset:>5.0f}s[/] [bold]{e['kind']:<9}[/] {lane}  {detail}")


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
    api.state.scheduler.start()
    cfg = load_settings().overnight
    console.print(f"Pablo on http://{host}:{port}" + ("  (fake models)" if fake else ""))
    console.print(f"Overnight queue runs {cfg.start}–{cfg.end}; brief after {cfg.brief_at}.")
    uvicorn.run(api, host=host, port=port, log_level="warning")


queue_app = typer.Typer(help="Overnight queue: slow work that runs while you sleep.")
app.add_typer(queue_app, name="queue")


@queue_app.command("add")
def queue_add(
    task: str,
    lane: Annotated[str | None, typer.Option(help="Lane (default: overnight.default_lane)")] = None,
) -> None:
    """Queue a task for tonight."""
    from pablo.overnight import JobQueue

    settings = load_settings()
    if lane and settings.lane(lane).requires_approval:
        console.print(f"[red]'{lane}' can't be queued; escalate a run instead.[/]")
        raise typer.Exit(2)
    job = JobQueue(settings.home).add(task, lane)
    window = f"{settings.overnight.start}–{settings.overnight.end}"
    console.print(f"queued [cyan]{job.id}[/] for tonight ({window}): {task}")


@queue_app.command("list")
def queue_list() -> None:
    """Show queued and recent jobs."""
    from pablo.overnight import JobQueue

    table = Table("job", "status", "lane", "task", "run")
    for job in JobQueue(load_settings().home).all()[-30:]:
        if job.status != "cancelled":
            table.add_row(
                job.id, job.status, job.lane or "default", job.task[:60], job.run_id or ""
            )
    console.print(table)


@queue_app.command("cancel")
def queue_cancel(job_id: str) -> None:
    """Remove a job before it runs."""
    from pablo.overnight import JobQueue

    JobQueue(load_settings().home).cancel(job_id)
    console.print(f"cancelled {job_id}")


@queue_app.command("run")
def queue_run(repo: RepoOpt = Path("."), fake: FakeOpt = False) -> None:
    """Run every queued job now instead of waiting for tonight."""
    from pablo.overnight import JobQueue, OvernightRunner

    orch = _orch(repo.resolve(), fake)
    runner = OvernightRunner(orch, JobQueue(orch.settings.home), orch.settings.overnight)
    for job in runner.run_pending(force=True):
        console.print(
            f"{job.id} {job.status}: {job.task}" + (f" → run {job.run_id}" if job.run_id else "")
        )


@app.command()
def brief(
    repo: RepoOpt = Path("."),
    fake: FakeOpt = False,
    make: Annotated[bool, typer.Option("--make", help="Write today's brief now")] = False,
) -> None:
    """Show the morning brief (or write today's with --make)."""
    from rich.markdown import Markdown

    from pablo.overnight import BriefStore, JobQueue, build_brief, now_local

    orch = _orch(repo.resolve(), fake)
    store = BriefStore(orch.settings.home)
    if make:
        made = build_brief(
            orch,
            JobQueue(orch.settings.home),
            now_local().date(),
            orch.repo,
            since=store.last_created(),
        )
        store.save(made)
    latest = store.latest()
    if latest is None:
        console.print(
            "No brief yet. One is written each morning; `pablo brief --make` writes one now."
        )
        raise typer.Exit(0)
    md = store.root / f"{latest['day']}.md"
    console.print(Markdown(md.read_text("utf-8")))


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
def doctor(repo: RepoOpt = Path(".")) -> None:
    """Check the gateway, GPU, disk, and check sandbox."""
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
    box = make_sandbox(settings.sandbox, repo.resolve())
    ready, why = box.ready()
    console.print(
        f"sandbox {box.label}: " + ("[green]ready[/]" if ready else f"[red]not ready[/] ({why})")
    )


@app.command("export-training")
def export_training(out: Path = Path("training/approved.jsonl")) -> None:
    """Export approved, trainable traces. Escalation traces are always excluded."""
    count = TraceStore(load_settings().traces_dir).export_training(out)
    console.print(f"wrote {count} examples to {out}")


@app.command("eval")
def eval_suite(
    lane: Annotated[str, typer.Option(help="Lane to score")] = "coder",
    only: Annotated[str, typer.Option(help="Comma-separated task ids")] = "",
    fake: FakeOpt = False,
    write: Annotated[
        bool, typer.Option(help="Append to evals/results.jsonl and docs/benchmarks.md")
    ] = False,
) -> None:
    """Score a lane on the 30-task coder suite against hidden tests (evals/suite)."""
    from pablo.evals import append_result, run_suite
    from pablo.sandbox import LocalSandbox

    settings = load_settings()
    backend = _backend(settings, fake)

    def show(r: Any) -> None:
        mark = "[green]pass[/]" if r.hidden_passed else "[red]FAIL[/]"
        cert = "—" if r.certainty is None else f"{r.certainty:.2f}"
        console.print(f"  {r.id:<32} {r.refs:<6} {r.state:<9} {mark}  {cert}  {r.wall_s:.0f}s")

    report = run_suite(
        settings,
        backend,
        lane=lane,
        only={x.strip() for x in only.split(",") if x.strip()} or None,
        sandbox=LocalSandbox() if fake else None,
        swap=None if fake else SwapView(),
        on_task=show,
    )
    s = report.summary()
    console.print(
        f"pass {s['pass_rate']:.0%} {s['pass_rate_by_refs']} · ready {s['ready']} "
        f"(precision {s['ready_precision']}) · false ready {s['false_ready']} · wall {s['wall_s']}s"
    )
    if write and not fake:
        append_result(report)
        docs = Path("docs/benchmarks.md")
        if docs.is_file():
            with docs.open("a", encoding="utf-8") as fh:
                fh.write("\n" + report.to_markdown() + "\n")
        console.print("appended to evals/results.jsonl and docs/benchmarks.md")


@app.command("eval-judge")
def eval_judge(
    fake: FakeOpt = False,
    model: Annotated[
        str | None, typer.Option(help="Gateway alias (default: the judge lane)")
    ] = None,
    write: Annotated[bool, typer.Option(help="Append the table to docs/benchmarks.md")] = False,
    case_set: Annotated[
        str, typer.Option("--set", help="main (tune on these) or holdout (score only, never tune)")
    ] = "main",
) -> None:
    """Score the judge on fixed good/bad diffs (evals/judge)."""
    from pablo.judge_eval import run_judge_eval

    settings = load_settings()
    alias = model or settings.lane("judge").model
    report = run_judge_eval(
        _backend(settings, fake),
        alias,
        threshold=settings.thresholds.judge_needs_you,
        case_set=case_set,
    )
    table = report.to_markdown(alias)
    console.print(table)
    sneaky = [r.id for r in report.tests_passed_but_bad if not r.correct]
    if sneaky:
        console.print(
            f"[yellow]Passed tests but should be rejected, judge missed: {', '.join(sneaky)}[/]"
        )
    if write:
        bench = Path(__file__).resolve().parents[2] / "docs" / "benchmarks.md"
        with bench.open("a", encoding="utf-8") as fh:
            fh.write("\n" + table)
        console.print(f"appended to {bench}")


@app.command("export-traces")
def export_traces(out: Path = Path("traces-toon")) -> None:
    """Daily TOON stats files (one row per run, no prompts or code) for quick analysis."""
    from pablo.traces import export_toon

    files = export_toon(TraceStore(load_settings().traces_dir), out)
    console.print(f"wrote {len(files)} TOON file(s) to {out}")


sandbox_app = typer.Typer(help="The container checks run in: no network, read-only, limited.")
app.add_typer(sandbox_app, name="sandbox")


@sandbox_app.command("build")
def sandbox_build(
    repo: RepoOpt = Path("."),
    base: Annotated[bool, typer.Option("--base", help="Build the base image instead")] = False,
) -> None:
    """Build the check image (needs network; checks never do). Rebuild when deps change."""
    from pablo.config import find_config
    from pablo.sandbox import build_base, build_project, project_image

    settings = load_settings()
    cfg = settings.sandbox
    if base:
        dockerfile = find_config().parent.parent / "scripts" / "sandbox" / "Dockerfile"
        code = build_base(cfg, dockerfile).returncode
        tag = cfg.image
    else:
        code = build_project(cfg, repo.resolve()).returncode
        tag = project_image(cfg.image, repo.resolve())
    if code != 0:
        console.print(f"[red]build failed[/] ({code})")
        raise typer.Exit(code)
    console.print(f"built [bold]{tag}[/]")


@sandbox_app.command("status")
def sandbox_status(repo: RepoOpt = Path(".")) -> None:
    """Which sandbox checks use for this repo, and whether it can run."""
    box = make_sandbox(load_settings().sandbox, repo.resolve())
    ready, why = box.ready()
    console.print(f"{box.label}: " + ("[green]ready[/]" if ready else f"[red]not ready[/] ({why})"))
    if not ready:
        raise typer.Exit(1)
