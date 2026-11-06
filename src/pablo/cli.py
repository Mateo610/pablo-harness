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


def _settings(fake: bool) -> Settings:
    settings = load_settings()
    if fake:  # scripted models never touch the GPU: leave the voice server alone
        settings.gpu.residents = []
    return settings


def _orch(repo: Path, fake: bool) -> Orchestrator:
    settings = _settings(fake)
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
    tls_cert: Annotated[
        Path | None, typer.Option(help="TLS certificate (default: ~/.pablo/tls/pablo.crt)")
    ] = None,
    tls_key: Annotated[
        Path | None, typer.Option(help="TLS key (default: ~/.pablo/tls/pablo.key)")
    ] = None,
) -> None:
    """Run the harness API (and the built cockpit, if web/dist exists).

    With a certificate (`pablo cert`), it serves HTTPS, which browsers require for the mic.
    """
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
    settings = load_settings()
    cfg = settings.overnight
    tls = settings.home / "tls"
    cert = tls_cert or (tls / "pablo.crt")
    key = tls_key or (tls / "pablo.key")
    secure = cert.is_file() and key.is_file()
    name = (tls / "name").read_text().strip() if secure and (tls / "name").is_file() else host
    scheme = "https" if secure else "http"
    console.print(f"Pablo on {scheme}://{name}:{port}" + ("  (fake models)" if fake else ""))
    if not secure:
        console.print("[dim]HTTP: the browser won't allow the mic. `pablo cert` fixes that.[/]")
    console.print(f"Overnight queue runs {cfg.start}–{cfg.end}; brief after {cfg.brief_at}.")
    uvicorn.run(
        api,
        host=host,
        port=port,
        log_level="warning",
        ssl_certfile=str(cert) if secure else None,
        ssl_keyfile=str(key) if secure else None,
    )


@app.command("cert")
def cert_cmd() -> None:
    """Get a TLS certificate for Pablo's tailnet name (tailscale cert), for HTTPS and the mic.

    The certificate is for `pablo.<your-tailnet>.ts.net` only; it expires in about 90 days, so
    run this again monthly (or from a timer). Keys stay in ~/.pablo/tls (mode 0700).
    """
    import json as _json

    try:
        status = _json.loads(
            subprocess.run(
                ["tailscale", "status", "--json", "--peers=false"],
                capture_output=True, text=True, timeout=10, check=True,
            ).stdout
        )  # fmt: skip
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        console.print(f"[red]tailscale isn't answering: {exc}[/]")
        raise typer.Exit(1) from exc
    name = str((status.get("Self") or {}).get("DNSName", "")).rstrip(".")
    if not name:
        console.print("[red]This machine has no tailnet DNS name. Turn on MagicDNS and HTTPS.[/]")
        raise typer.Exit(1)
    tls = load_settings().home / "tls"
    tls.mkdir(parents=True, exist_ok=True)
    tls.chmod(0o700)
    cmd = ["tailscale", "cert", "--cert-file", str(tls / "pablo.crt"),
           "--key-file", str(tls / "pablo.key"), name]  # fmt: skip
    done = subprocess.run(cmd, capture_output=True, text=True)
    if done.returncode != 0:
        console.print(f"[red]{done.stderr.strip()}[/]")
        console.print("If that's a permission error, run it once with sudo, then chown the files:")
        console.print("  sudo " + " ".join(cmd))
        console.print(f"  sudo chown -R $USER {tls}")
        raise typer.Exit(1)
    (tls / "name").write_text(name + "\n")
    console.print(f"Certificate for {name} in {tls}. Restart `pablo serve`, then open")
    console.print(f"  https://{name}:8710")


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


@app.command("learn")
def learn(
    nightly: Annotated[
        bool, typer.Option(help="Skip unless ≥50 new approved examples since last train")
    ] = False,
    force: Annotated[bool, typer.Option(help="Run even with fewer than 50 new examples")] = False,
    skip_train: Annotated[
        bool, typer.Option(help="Prepare data only; do not call Unsloth (tests / dry-run)")
    ] = False,
    skip_eval: Annotated[
        bool, typer.Option(help="Train (or prepare) but do not run the candidate suite")
    ] = False,
    repeat: Annotated[
        int, typer.Option(help="Suite runs for the coder and for the candidate, each")
    ] = 3,
    fake: FakeOpt = False,
) -> None:
    """Export → train LoRA → eval coder and candidate the same night → gated promotion item."""
    from datetime import UTC, datetime

    from pablo.learn import (
        MIN_NEW_EXAMPLES,
        ClaudeInTrainingError,
        Promotion,
        gate,
        new_adapter_dir,
        prepare_training,
        publish_candidate,
        run_train_script,
        save_promotion,
        should_run_nightly,
        train_marker,
        write_marker,
    )

    settings = load_settings()
    store = TraceStore(settings.traces_dir)
    if nightly and not force:
        ok, reason, n = should_run_nightly(store, settings.home)
        if not ok:
            console.print(f"[yellow]skip[/]: {reason}")
            raise typer.Exit(0)
        console.print(f"{n} new examples (≥{MIN_NEW_EXAMPLES}); starting cycle")

    export = settings.home / "learn" / "approved.jsonl"
    count = store.export_training(export)
    if count == 0:
        console.print("[yellow]skip[/]: no approved trainable traces")
        raise typer.Exit(0)
    console.print(f"exported {count} examples → {export}")

    adapter_dir = new_adapter_dir()
    try:
        prepared = prepare_training(export, adapter_dir)
    except ClaudeInTrainingError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(2) from exc
    console.print(
        f"prepared train={prepared.train_n} holdout={prepared.holdout_n} "
        f"dupes_skipped={prepared.skipped_dupes} eval_tasks_dropped={prepared.skipped_eval}"
    )

    if not skip_train:
        # Train on the prepared split only: the holdout and any eval-suite task stay out.
        proc = run_train_script(prepared.train_path, adapter_dir, skip_train=False)
        if proc.returncode != 0:
            console.print(proc.stdout)
            console.print(f"[red]train failed[/]\n{proc.stderr}")
            raise typer.Exit(proc.returncode)
        console.print(proc.stdout)
        write_marker(train_marker(settings.home))
    else:
        run_train_script(prepared.train_path, adapter_dir, skip_train=True)
        console.print("[yellow]skip-train[/]: data prepared only")

    publish_candidate(adapter_dir)
    result = None
    wall_s = 0.0
    commit = ""
    if not skip_eval and not skip_train and not fake:
        from pablo.evals import aggregate_markdown, append_result, run_suite

        sides: dict[str, list[Any]] = {"coder": [], "coder-candidate": []}
        for i in range(max(1, repeat)):
            # Interleaved, so both sides see the same box on the same night.
            for lane_name, reports in sides.items():
                console.print(f"run {i + 1} of {repeat}: {lane_name}")
                report = run_suite(
                    settings, _backend(settings, fake=False), lane=lane_name, swap=SwapView()
                )
                append_result(report)
                reports.append(report)
        result = gate(sides["coder"], sides["coder-candidate"])
        wall_s = sum(float(r.summary()["wall_s"]) for r in sides["coder-candidate"])
        commit = sides["coder"][0].commit
        console.print(
            f"coder {result.baseline_mean:.0%} ± {result.baseline_spread:.0%} · candidate "
            f"{result.candidate_mean:.0%} ± {result.candidate_spread:.0%} · {result.reason}"
        )
        docs = Path("docs/benchmarks.md")
        if docs.is_file():
            with docs.open("a", encoding="utf-8") as fh:
                for reports in sides.values():
                    fh.write("\n" + aggregate_markdown(reports) + "\n")
    else:
        console.print("[yellow]skip-eval[/]: no candidate score")

    beats = bool(result and result.beats)
    promo = Promotion(
        id=adapter_dir.name,
        created_at=datetime.now(UTC).isoformat(),
        adapter=str(adapter_dir),
        baseline_pass_rate=result.baseline_mean if result else 0.0,
        candidate_pass_rate=result.candidate_mean if result else -1.0,
        baseline_commit=commit,
        candidate_wall_s=wall_s,
        beats_baseline=beats,
        state="pending" if beats else "held",
        note=result.reason if result else "not evaluated",
        runs=result.runs if result else 0,
        baseline_spread=result.baseline_spread if result else 0.0,
        candidate_spread=result.candidate_spread if result else 0.0,
        baseline_false_ready=result.baseline_false_ready if result else 0,
        candidate_false_ready=result.candidate_false_ready if result else 0,
        train_examples=prepared.train_n,
    )
    path = save_promotion(promo, settings.home)
    mark = "[green]beats baseline → pending promote[/]" if beats else "[yellow]held[/]"
    console.print(f"promotion {promo.id} {mark} → {path}")


@app.command("promote")
def promote(
    reject: Annotated[bool, typer.Option("--reject", help="Reject the pending promotion")] = False,
    note: Annotated[str, typer.Option(help="Optional note")] = "",
) -> None:
    """Apply or reject the pending LoRA promotion (human only; never automatic)."""
    from pablo.learn import load_pending, promote_adapter, reject_promotion

    settings = load_settings()
    pending = load_pending(settings.home)
    if pending is None:
        console.print("no pending promotion")
        raise typer.Exit(1)
    if reject:
        reject_promotion(settings.home, note=note)
        console.print(f"rejected {pending.id}")
        return
    try:
        done = promote_adapter(settings.home)
    except (ValueError, FileNotFoundError) as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(1) from exc
    console.print(
        f"[green]promoted[/] {done.id}: candidate {done.candidate_pass_rate:.0%} "
        f"> baseline {done.baseline_pass_rate:.0%}"
    )
    console.print("restart pablo-swap so the coder picks up coder-active/adapter.gguf")


@app.command("eval")
def eval_suite(
    lane: Annotated[str, typer.Option(help="Lane to score")] = "coder",
    only: Annotated[str, typer.Option(help="Comma-separated task ids")] = "",
    repeat: Annotated[int, typer.Option(help="Run the suite N times and report the spread")] = 1,
    cascade: Annotated[
        bool, typer.Option(help="Retry Needs-you runs on the lane's retry_on lane (coder → deep)")
    ] = False,
    learn: Annotated[
        str, typer.Option(help="Turn on learning pieces: examples,failures,routing")
    ] = "",
    lessons: Annotated[
        Path | None,
        typer.Option(help="Start from this lessons folder (from practice --suite-seeds)"),
    ] = None,
    spec: Annotated[
        bool | None,
        typer.Option(
            "--spec/--no-spec", help="Force task-derived checks on or off (default: config)"
        ),
    ] = None,
    fake: FakeOpt = False,
    write: Annotated[
        bool, typer.Option(help="Append to evals/results.jsonl and docs/benchmarks.md")
    ] = False,
    suite_set: Annotated[
        str,
        typer.Option(
            "--set",
            help="main (evals/suite) or holdout (harder tasks; totals only, never studied)",
        ),
    ] = "main",
    allow_remote: Annotated[
        bool,
        typer.Option(
            help="Let a remote lane (claude) be scored: every suite task leaves the box, and it "
            "costs API calls. Nothing from your repos is sent."
        ),
    ] = False,
) -> None:
    """Score a lane on the coder suite against hidden tests (evals/suite or evals/holdout)."""
    from contextlib import nullcontext

    from pablo.gpu import GpuScheduler

    settings = _settings(fake)
    backend = _backend(settings, fake)
    # A real eval owns the GPU: hold Deep Work in the real home so `pablo serve` doesn't wake
    # the voice server (or start night work) mid-suite. The owner's mode comes back after.
    gpu = GpuScheduler(home=settings.home, residents_cfg=settings.gpu.residents,
                       unload_url=settings.gpu.unload_url)  # fmt: skip
    hold = nullcontext() if fake else gpu.mode_for("deep")

    lane_cfg = load_settings().lane(lane)
    if (lane_cfg.kind == "remote" or lane_cfg.requires_approval) and not allow_remote:
        raise typer.BadParameter(
            f"'{lane}' leaves the box: add --allow-remote to send the suite's tasks to it"
        )
    if cascade and lane_cfg.kind == "remote":
        raise typer.BadParameter("a remote lane doesn't cascade")
    if suite_set not in ("main", "holdout"):
        raise typer.BadParameter("--set is main or holdout")
    sealed = suite_set == "holdout"
    if sealed and only:
        raise typer.BadParameter("the holdout runs whole: --only would point at single tasks")
    done = [0]

    def show(r: Any) -> None:
        if sealed:  # totals only: which task passed is exactly what we mustn't study
            done[0] += 1
            console.print(f"  task {done[0]} done", end="\r")
            return
        mark = "[green]pass[/]" if r.hidden_passed else "[red]FAIL[/]"
        cert = "—" if r.certainty is None else f"{r.certainty:.2f}"
        fix = f"  fix: {r.fix_outcome}" if r.fix_outcome else ""
        console.print(f"  {r.id:<32} {r.refs:<6} {r.state:<9} {mark}  {cert}  {r.wall_s:.0f}s{fix}")

    with hold:
        if not fake:
            _eval_preflight(settings, backend, gpu, lane, cascade)
        reports, stopped = _eval_runs(settings, backend, lane, only, repeat, cascade, spec, learn,
                             lessons, fake, write, show, sealed)  # fmt: skip
    if reports and sealed:
        _sealed_report(reports, write, fake)
    elif reports:
        _eval_report(reports, len(reports), write, fake)
    if stopped:
        raise typer.Exit(3)


def _eval_preflight(
    settings: Settings, backend: ChatBackend, gpu: Any, lane: str, cascade: bool
) -> None:
    """Every model the suite needs answers once before an hour-long run starts.

    Deep first (its lease pauses the voice server, so this also proves that works), then the
    judge, then the lane, so the lane is loaded for the first task.
    """
    from pablo.evals import preflight
    from pablo.gpu import ScheduledBackend, off_gpu_models

    retry = settings.lane(lane).retry_on if cascade else None
    models = [settings.lane(n).model for n in (retry, "judge", lane) if n]
    console.print("preflight: " + " · ".join(models))
    problems = preflight(ScheduledBackend(backend, gpu, off_gpu_models(settings)), models)
    if problems:
        for line in problems:
            console.print(f"[red]preflight failed[/] {line}")
        console.print("Nothing was run. Fix the model above, then start the eval again.")
        raise typer.Exit(3)
    for r in gpu.snapshot()["residents"]:
        console.print(f"  resident {r['name']}: {r['state']}")


def _eval_runs(
    settings: Settings,
    backend: ChatBackend,
    lane: str,
    only: str,
    repeat: int,
    cascade: bool,
    spec: bool | None,
    learn: str,
    lessons: Path | None,
    fake: bool,
    write: bool,
    show: Any,
    sealed: bool = False,
) -> tuple[list[Any], bool]:
    from pablo.evals import HOLDOUT, SUITE, InfraError, append_result, append_sealed, run_suite
    from pablo.judge_replay import append_cases
    from pablo.sandbox import LocalSandbox

    reports: list[Any] = []
    for i in range(max(1, repeat)):
        if repeat > 1:
            console.print(f"[bold]run {i + 1} of {repeat}[/]")
        try:
            report = run_suite(
                settings,
                backend,
                lane=lane,
                only={x.strip() for x in only.split(",") if x.strip()} or None,
                sandbox=LocalSandbox() if fake else None,
                swap=None if fake else SwapView(),
                on_task=show,
                cascade=cascade,
                spec=spec,
                learn={x.strip() for x in learn.split(",") if x.strip()} or None,
                lessons=lessons,
                root=HOLDOUT if sealed else SUITE,
            )
        except InfraError as exc:
            console.print(f"[red]stopped: the box failed, not the model[/] {exc}")
            console.print(f"Runs finished before this one are kept ({len(reports)}). This one "
                          "isn't written: its numbers would be noise.")  # fmt: skip
            return reports, True
        reports.append(report)
        if sealed:
            if write and not fake:
                append_sealed(report)  # totals only, and no judge replay from the holdout
            continue
        s = report.summary()
        console.print(
            f"pass {s['pass_rate']:.0%} {s['pass_rate_by_refs']} · ready {s['ready']} "
            f"(precision {s['ready_precision']}) · false ready {s['false_ready']} · "
            f"fix tried {s['fix_tried']} {s['fix_outcomes']} · wall {s['wall_s']}s"
        )
        if write and not fake:
            append_result(report)
            added = append_cases(report.replay)
            console.print(f"judge replay: {added} new diffs in evals/judge/replay.jsonl")
    return reports, False


def _sealed_report(reports: list[Any], write: bool, fake: bool) -> None:
    from pablo.evals import sealed_markdown

    text = sealed_markdown(reports)
    console.print(text.split("\n\n", 1)[1])
    if write and not fake:
        docs = Path("docs/benchmarks.md")
        if docs.is_file():
            with docs.open("a", encoding="utf-8") as fh:
                fh.write("\n" + text + "\n")
        console.print("appended totals to evals/holdout-results.jsonl and docs/benchmarks.md")


def _eval_report(reports: list[Any], repeat: int, write: bool, fake: bool) -> None:
    from pablo.evals import aggregate, aggregate_markdown

    if repeat > 1:
        a = aggregate(reports)
        console.print(
            f"[bold]{a['runs']} runs:[/] passed {a['pass_counts']} · mean "
            f"{a['mean_pass_rate']:.0%} ± {a['stdev_pass_rate']:.0%} · flaky {a['flaky']}"
        )
    if write and not fake:
        docs = Path("docs/benchmarks.md")
        if docs.is_file():
            with docs.open("a", encoding="utf-8") as fh:
                for report in reports:
                    fh.write("\n" + report.to_markdown() + "\n")
                if repeat > 1:
                    fh.write("\n" + aggregate_markdown(reports) + "\n")
        console.print("appended to evals/results.jsonl and docs/benchmarks.md")


@app.command("eval-build")
def eval_build(
    lane: Annotated[str, typer.Option(help="auto (Hybrid), coder, deep, ...")] = "auto",
    only: Annotated[str, typer.Option(help="Comma-separated project ids")] = "",
    fake: FakeOpt = False,
    write: Annotated[
        bool, typer.Option(help="Append to evals/build-results.jsonl and docs/benchmarks.md")
    ] = False,
) -> None:
    """Plan mode end to end: plan a small tool, build it step by step, run its acceptance test."""
    from contextlib import nullcontext

    from pablo.build_eval import append_build, build_markdown, build_summary, run_build
    from pablo.evals import current_commit
    from pablo.gpu import GpuScheduler
    from pablo.sandbox import LocalSandbox

    settings = _settings(fake)
    backend = _backend(settings, fake)
    gpu = GpuScheduler(home=settings.home, residents_cfg=settings.gpu.residents,
                       unload_url=settings.gpu.unload_url)  # fmt: skip
    hold = nullcontext() if fake else gpu.mode_for("deep")

    def show(r: Any) -> None:
        mark = "[green]pass[/]" if r.accepted else "[red]FAIL[/]"
        how = "no plan" if not r.planned else f"{r.steps} steps, {r.approved} approved"
        err = f"  [red]{r.error}[/]" if r.error else ""
        console.print(f"  {r.id:<20} {mark}  {how}  {r.wall_s:.0f}s{err}")

    with hold:
        if not fake:
            _eval_preflight(settings, backend, gpu, "coder" if lane == "auto" else lane,
                            lane == "auto")  # fmt: skip
        results = run_build(
            settings, backend, lane=lane,
            only={x.strip() for x in only.split(",") if x.strip()} or None,
            sandbox=LocalSandbox() if fake else None, on_result=show,
        )  # fmt: skip
    summary = build_summary(results, lane=lane, commit=current_commit(Path(".")))
    text = build_markdown(summary)
    console.print(text.split("\n\n")[1])
    if write and not fake:
        append_build(summary)
        docs = Path("docs/benchmarks.md")
        if docs.is_file():
            with docs.open("a", encoding="utf-8") as fh:
                fh.write("\n" + text + "\n")
        console.print("appended to evals/build-results.jsonl and docs/benchmarks.md")


@app.command("eval-chat")
def eval_chat(
    lane: Annotated[str, typer.Option(help="auto (Hybrid), coder, deep, advisor")] = "auto",
    spoken: Annotated[bool, typer.Option(help="Ask as a voice call would")] = False,
    fake: FakeOpt = False,
    write: Annotated[
        bool, typer.Option(help="Append to evals/chat-results.jsonl and docs/benchmarks.md")
    ] = False,
) -> None:
    """Does the chat model talk, start one change, or plan, when it should? (evals/chat)"""
    from pablo.chat_eval import append_chat, chat_markdown, chat_summary, run_chat_eval
    from pablo.evals import current_commit

    settings = _settings(fake)

    def show(c: Any) -> None:
        mark = "[green]ok[/]  " if c.chose == c.label else "[red]MISS[/]"
        console.print(f"  {mark} wanted {c.label:<4} chose {c.chose:<4} {c.text[:60]}")

    cases = run_chat_eval(settings, _backend(settings, fake), lane=lane, spoken=spoken,
                          on_case=show)  # fmt: skip
    summary = chat_summary(cases, lane=lane, spoken=spoken, commit=current_commit(Path(".")))
    text = chat_markdown(summary)
    console.print(text.split("\n\n", 1)[1])
    if write and not fake:
        append_chat(summary)
        docs = Path("docs/benchmarks.md")
        if docs.is_file():
            with docs.open("a", encoding="utf-8") as fh:
                fh.write("\n" + text + "\n")
        console.print("appended to evals/chat-results.jsonl and docs/benchmarks.md")


@app.command("eval-judge")
def eval_judge(
    fake: FakeOpt = False,
    model: Annotated[
        str | None, typer.Option(help="Gateway alias (default: the judge lane)")
    ] = None,
    write: Annotated[bool, typer.Option(help="Append the table to docs/benchmarks.md")] = False,
    case_set: Annotated[
        str,
        typer.Option(
            "--set",
            help="main (tune on these), holdout (score only, never tune), or replay "
            "(real suite diffs labeled by hidden tests; the bake-off number)",
        ),
    ] = "main",
    max_tokens: Annotated[
        int, typer.Option(help="Room for the reply; a judge that reasons needs ~4096")
    ] = 300,
) -> None:
    """Score the judge on fixed good/bad diffs (evals/judge), or replay real suite diffs."""
    from pablo.judge_eval import run_judge_eval

    settings = load_settings()
    alias = model or settings.lane("judge").model
    if case_set == "replay":
        from pablo.judge_replay import REPLAY, run_replay

        if not REPLAY.is_file():
            console.print("[yellow]No replay yet: run `pablo eval --write` first.[/]")
            raise typer.Exit(1)
        replay = run_replay(
            _backend(settings, fake), alias, threshold=settings.thresholds.judge_needs_you,
            max_tokens=max_tokens,
        )  # fmt: skip
        table = replay.to_markdown()
        console.print(table)
        if write:
            bench = Path(__file__).resolve().parents[2] / "docs" / "benchmarks.md"
            with bench.open("a", encoding="utf-8") as fh:
                fh.write("\n" + table + "\n")
            console.print(f"appended to {bench}")
        return
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


@app.command("chat")
def chat_cmd(
    text: Annotated[str, typer.Argument(help="What you want to say")],
    conv: Annotated[str | None, typer.Option(help="Conversation id (default: start one)")] = None,
    lane: Annotated[str | None, typer.Option(help="auto, coder, deep, advisor, claude")] = None,
    do: Annotated[bool | None, typer.Option("--do/--talk", help="Run it, or only talk")] = None,
    repo: RepoOpt = Path("."),
    fake: FakeOpt = False,
) -> None:
    """Talk to Pablo in a conversation that remembers. `pablo chat --conv ID ...` continues one."""
    from pablo.convos import AUTO, Chat

    chat = Chat(_orch(repo.resolve(), fake))
    cid = conv or chat.start(lane=lane or AUTO).id
    confirm = False
    if (lane or chat.store.conversation(cid, "owner").lane) == "claude":
        confirm = typer.confirm("This leaves Pablo for Claude. Send?")
        if not confirm:
            raise typer.Exit(1)
    turn = chat.say(cid, text, lane=lane, do=do, confirm=confirm)
    console.print(turn.reply.content)
    for mem in turn.proposed:
        console.print(f"[magenta]remember? {mem.text}[/]  pablo memory keep {mem.id}")
    console.print(f'[dim]conversation {cid} · pablo chat --conv {cid} "..."[/]')


memory_app = typer.Typer(help="What Pablo remembers for you. Nothing is kept unless you keep it.")
app.add_typer(memory_app, name="memory")


def _memory_store() -> Any:
    from pablo.convos import OWNER, Store

    store = Store(load_settings().home / "pablo.db")
    store.ensure_profile(OWNER, "Owner", "owner")
    return store


@app.command("practice")
def practice_cmd(
    repo: RepoOpt = Path("."),
    n: Annotated[int, typer.Option(help="How many tasks to make and attempt")] = 10,
    teacher: Annotated[
        str, typer.Option(help="Lane that tries what the coder missed; '' for none")
    ] = "deep",
    suite_seeds: Annotated[
        bool, typer.Option(help="Practice on the suite's seed repos, for measuring (needs --out)")
    ] = False,
    out: Annotated[
        Path | None, typer.Option(help="Write lessons here instead of ~/.pablo/lessons")
    ] = None,
    fake: FakeOpt = False,
) -> None:
    """Make checkable tasks from a repo, attempt them, keep the fixes that pass its tests.

    Kept fixes become examples, misses become failure notes, both become lane outcomes
    (~/.pablo/lessons). Functions the eval suite names are always skipped.
    """
    import tempfile as _tempfile

    from pablo.lessons import Lessons
    from pablo.practice import Practice, PracticeResult

    def show(r: PracticeResult) -> None:
        mark = f"[green]kept · {r.lane}[/]" if r.passed else f"[red]missed[/] {r.reason[:70]}"
        console.print(f"  {r.kind:<11} {r.target:<48} {mark}")

    if not suite_seeds:
        orch = _orch(repo.resolve(), fake)
        lessons = Lessons(out) if out else None
        coach = Practice(orch, lessons=lessons, teacher=teacher or None, on_result=show)
        results = coach.session(repo.resolve(), n)
    else:
        if out is None:
            console.print("[red]--suite-seeds writes to a separate folder: pass --out DIR[/]")
            raise typer.Exit(2)
        from pablo.evals import load_suite, materialize

        firsts: dict[str, Any] = {}
        for task in load_suite():
            if task.repo != "harness":  # the harness seed needs the full app installed
                firsts.setdefault(task.repo, task)
        results = []
        with _tempfile.TemporaryDirectory(prefix="pablo-seed-practice-") as tmp:
            seeds = {name: materialize(task, Path(tmp)) for name, task in firsts.items()}
            orch = _orch(next(iter(seeds.values())), fake)
            coach = Practice(orch, lessons=Lessons(out), teacher=teacher or None, on_result=show)
            for name, path in seeds.items():
                console.print(f"[bold]{name}[/]")
                results += coach.session(path, max(1, n // len(seeds)), key=name, allow_suite=True)
    kept = sum(r.passed for r in results)
    console.print(
        f"kept {kept} of {len(results)} · lessons in {(out or load_settings().home) / 'lessons'}"
    )


@app.command("lessons")
def lessons_cmd(
    where: Annotated[Path | None, typer.Option(help="A home folder (default ~/.pablo)")] = None,
) -> None:
    """What Pablo has learned without retraining: examples, failure notes, lane outcomes."""
    from collections import Counter

    from pablo.lessons import Lessons

    store = Lessons(where or load_settings().home)
    examples, failures = store.examples(), store.failures()
    by = Counter((e.repo, e.source) for e in examples)
    console.print(f"[bold]examples[/] {len(examples)}")
    for (repo_name, source), count in sorted(by.items()):
        console.print(f"  {repo_name:<24} {source:<9} {count}")
    console.print(f"[bold]failure notes[/] {len(failures)}")
    rows = store._read("outcomes.jsonl")
    seen = sorted({(r["repo"], r["kind"]) for r in rows})
    if seen:
        console.print("[bold]outcomes[/] (right / tried)")
    for repo_name, kind in seen:
        rec = store.lane_record(repo_name, kind)
        cells = "  ".join(f"{lane} {r}/{t}" for lane, (r, t) in sorted(rec.items()))
        console.print(f"  {repo_name:<24} {kind:<10} {cells}")


@app.command("token")
def token_cmd(port: int = 8710) -> None:
    """The owner token for local clients, and a link that signs this machine's browser in."""
    from pablo.access import owner_token

    tok = owner_token(load_settings().home)
    console.print(tok)
    console.print(f"[dim]browser on Pablo: http://127.0.0.1:{port}/?token={tok}[/]")
    console.print("[dim]The TUI and scripts on this machine read it from ~/.pablo/owner.token.[/]")


@app.command("whoami")
def whoami_cmd(
    url: Annotated[str, typer.Option(help="Harness API")] = "http://127.0.0.1:8710",
) -> None:
    """Who Pablo thinks you are, as seen through the API."""
    from pablo.client import HarnessClient, HarnessError

    try:
        me = HarnessClient(url)._call("GET", "/api/me")
    except HarnessError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(1) from exc
    console.print(f"{me['name']} · {me['role']} · via {me['via']} · approve: {me['can_approve']}")


@memory_app.command("list")
def memory_list(
    status: Annotated[str | None, typer.Option(help="kept, proposed, or rejected")] = None,
) -> None:
    for mem in _memory_store().memories("owner", status=status):
        console.print(f"{mem.id}  {mem.status:<9} {mem.scope:<16} {mem.text}")


@memory_app.command("add")
def memory_add(text: str, scope: str = "profile") -> None:
    mem = _memory_store().remember("owner", text, scope=scope)
    console.print(f"kept {mem.id}: {mem.text}")


@memory_app.command("keep")
def memory_keep(memory_id: str) -> None:
    console.print(f"kept: {_memory_store().decide(memory_id, 'owner', 'kept').text}")


@memory_app.command("reject")
def memory_reject(memory_id: str) -> None:
    console.print(f"rejected: {_memory_store().decide(memory_id, 'owner', 'rejected').text}")


@memory_app.command("forget")
def memory_forget(memory_id: str) -> None:
    _memory_store().forget(memory_id, "owner")
    console.print(f"forgot {memory_id}")


sandbox_app = typer.Typer(help="The container checks run in: no network, read-only, limited.")
app.add_typer(sandbox_app, name="sandbox")


@sandbox_app.command("build")
def sandbox_build(
    repo: RepoOpt = Path("."),
    base: Annotated[bool, typer.Option("--base", help="Build the base image instead")] = False,
    suite: Annotated[
        bool, typer.Option("--suite", help="Build images for the eval suite's seeds that need one")
    ] = False,
) -> None:
    """Build the check image (needs network; checks never do). Rebuild when deps change."""
    from pablo.config import find_config
    from pablo.sandbox import build_base, build_project, dep_files, make_sandbox, project_image

    settings = load_settings()
    cfg = settings.sandbox
    if suite:
        import tempfile

        from pablo.evals import load_suite, materialize

        seen: set[str] = set()
        with tempfile.TemporaryDirectory(prefix="pablo-suite-images-") as tmp:
            for task in load_suite():
                if task.repo in seen:
                    continue
                seen.add(task.repo)
                seed = materialize(task, Path(tmp))
                if not dep_files(seed):
                    continue
                ready, _ = make_sandbox(cfg, seed).ready()
                tag = project_image(cfg.image, seed)
                if ready:
                    console.print(f"{task.repo}: {tag} already built")
                    continue
                if build_project(cfg, seed).returncode != 0:
                    raise typer.Exit(1)
                console.print(f"{task.repo}: built [bold]{tag}[/]")
        return
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
