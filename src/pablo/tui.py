"""`pablo tui`: the cockpit in a terminal. Talks to the same harness API over HTTP,
so it works on Pablo itself or from your laptop over Tailscale."""

from __future__ import annotations

import time
from typing import Any, ClassVar

from rich.console import Group, RenderableType
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Footer, Input, Label, ListItem, ListView, Static
from textual.worker import get_current_worker

from pablo.client import HarnessAPI, HarnessError

Json = dict[str, Any]

LANE_COLORS = {
    "router": "#8fa7c2",
    "coder": "#4fd1bc",
    "judge": "#b9a3ff",
    "deep": "#f28fa8",
    "media": "#c4db72",
    "claude": "#d9cfc2",
}
ACCENT = "#f2b44c"
PASS = "#73d18f"
FAIL = "#f07a62"
MUTED = "#a39c92"
THRESHOLD = 0.75
ESCALATE_CONFIRM_S = 4.0


def lane_text(lane: str) -> Text:
    color = LANE_COLORS.get(lane, MUTED)
    return Text.assemble((f" {lane[:1].upper()} ", f"bold {color} on #24221f"), " ", (lane, "bold"))


def certainty_text(value: float | None) -> Text:
    if value is None:
        return Text("")
    if value >= THRESHOLD:
        return Text(f"◔ {value:.2f}", style=LANE_COLORS["judge"])
    return Text(f"◔ {value:.2f} · needs you", style=ACCENT)


def ribbon(busy: dict[str, bool], width: int = 60) -> Text:
    lanes = [n for n in LANE_COLORS if n != "claude"]
    weights = {n: (4 if busy.get(n) else 1) for n in lanes}
    total = sum(weights.values())
    out = Text()
    for n in lanes:
        cells = max(1, round(width * weights[n] / total))
        out.append("▀" * cells, style=LANE_COLORS[n] if busy.get(n) else "#24221f")
    return out


def render_run(run: Json) -> RenderableType:
    parts: list[RenderableType] = []
    head = lane_text(run["lane"])
    head.append(f"  {run['model']}", style=MUTED)
    head.append("   ")
    head.append_text(certainty_text(run.get("certainty")))
    parts.append(head)
    parts.append(Text(run["task"], style="bold"))
    if run.get("explanation"):
        parts.append(Text(run["explanation"]))
    verdict = run.get("verdict")
    if verdict:
        table = Table.grid(padding=(0, 3))
        for check in verdict.get("checks", []):
            ok = check["passed"]
            status = check.get("status", "passed" if ok else "failed")
            label = {"already failing": "Failed before too", "regressed": "Broken by diff"}.get(
                status, "Passed" if ok else "Failed"
            )
            color = PASS if ok else (MUTED if status == "already failing" else FAIL)
            table.add_row(
                Text(check["name"].upper(), style=MUTED),
                Text(label, style=f"bold {color}"),
                Text(f"exit {check['exit_code']} · {check['duration_s']}s", style=MUTED),
            )
        table.add_row(
            Text("DIFF", style=MUTED),
            Text(f"+{verdict['added']} −{verdict['removed']}", style="bold"),
            Text(", ".join(verdict.get("files", [])[:3]), style=MUTED),
        )
        if not verdict.get("applied"):
            table.add_row(
                Text("APPLY", style=MUTED),
                Text("Failed", style=f"bold {FAIL}"),
                Text(verdict.get("error", ""), style=MUTED),
            )
        parts.append(table)
    if run.get("diff"):
        parts.append(Syntax(run["diff"], "diff", theme="ansi_dark", background_color="#0e0d0c"))
    if run.get("question") and run["state"] == "needs_you":
        parts.append(Text(run["question"], style=f"bold {ACCENT}"))
    if run["state"] in ("ready", "needs_you"):
        parts.append(Text("a approve · r reject · e escalate to Claude", style=MUTED))
    else:
        parts.append(Text(run["state"].replace("_", " "), style=MUTED))
    return Group(*parts)


def render_plan(plan: Json | None) -> RenderableType:
    if not plan:
        return Text("No plan. Run `pablo init` in the project.", style=MUTED)
    phase = next(
        (p for p in plan["phases"] if not all(t["status"] == "done" for t in p["tasks"])), None
    )
    if phase is None:
        return Text("All phases done.", style=PASS)
    done = sum(t["status"] == "done" for t in phase["tasks"])
    out = Text.assemble(
        (f"PHASE {phase['id']} · {phase['title'].upper()}", MUTED),
        (f"  {done}/{len(phase['tasks'])}\n", MUTED),
    )
    for task in phase["tasks"]:
        style = {"done": PASS, "active": ACCENT, "needs_you": ACCENT}.get(task["status"], MUTED)
        out.append(f"{task['id']:<5}", style=MUTED)
        out.append(
            f"{task['title']}", style="bold" if task["status"] in ("active", "needs_you") else ""
        )
        out.append(f"  {task['status'].replace('_', ' ')}\n", style=style)
    out.append(f"Gate: {phase['gate']}", style=MUTED)
    return out


def render_brief(brief: Json | None) -> RenderableType:
    if not brief:
        return Text(
            "No brief yet. One is written each morning after the overnight window.", style=MUTED
        )
    parts: list[RenderableType] = [
        Text(f"MORNING BRIEF · {brief['day']}", style=MUTED),
        Text(brief["headline"], style="bold"),
    ]
    if brief.get("plan_line"):
        parts.append(Text(brief["plan_line"], style=MUTED))
    for item in brief.get("items", []):
        line = lane_text(item["lane"])
        line.append(f"  {item['task'][:60]}  ")
        needs = item["state"] == "needs_you"
        line.append("needs you" if needs else item["state"], style=ACCENT if needs else MUTED)
        parts.append(line)
    for job in brief.get("failed_jobs", []):
        parts.append(Text(f"{job['task']}: {job['error']}", style=FAIL))
    if brief.get("still_queued"):
        parts.append(Text(f"{brief['still_queued']} still queued for tonight.", style=MUTED))
    return Group(*parts)


class RunItem(ListItem):
    def __init__(self, run: Json) -> None:
        self.run = run
        label = lane_text(run["lane"])
        label.append(f"  {run['task'][:48]}")
        if run.get("certainty") is not None:
            label.append("  ")
            label.append_text(certainty_text(run["certainty"]))
        super().__init__(Label(label))
        if run["state"] == "needs_you":
            self.add_class("waiting")


class PabloTUI(App[None]):
    TITLE = "Pablo"
    AUTO_FOCUS = "#inbox"
    CSS = """
    Screen { background: #151413; color: #ede8e1; }
    #ribbon { height: 1; }
    #title { height: 1; padding: 0 1; color: #a39c92; }
    #body { height: 1fr; }
    #left { width: 46%; min-width: 40; padding: 0 1; }
    #right { width: 1fr; background: #1c1a18; padding: 1 2; }
    .label { color: #a39c92; text-style: bold; margin-top: 1; }
    .label.waiting-label { color: #f2b44c; }
    #inbox { height: auto; max-height: 50%; background: #151413; }
    #inbox > ListItem { background: #1c1a18; margin-bottom: 1; padding: 0 1; }
    #inbox > ListItem.waiting { background: #2a2216; }
    #inbox > ListItem.-highlight { background: #24221f; }
    #plan { margin-top: 1; background: #1c1a18; padding: 1; }
    #status { height: 1; padding: 0 1; color: #a39c92; }
    #composer { display: none; margin: 0 1; background: #24221f; border: tall #2f2c28; }
    #composer.open { display: block; }
    """
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("a", "approve", "Approve"),
        Binding("r", "reject", "Reject"),
        Binding("e", "escalate", "Escalate"),
        Binding("n", "new_task", "New task"),
        Binding("t", "tonight", "Tonight"),
        Binding("b", "brief", "Brief"),
        Binding("x", "run_next", "Run next"),
        Binding("g", "refresh", "Refresh"),
        Binding("escape", "close_composer", "Close", show=False),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, api: HarnessAPI, *, listen: bool = True) -> None:
        super().__init__()
        self.api = api
        self.listen = listen
        self.busy: dict[str, bool] = {}
        self.plan: Json | None = None
        self.inbox: list[Json] = []
        self._escalate_armed: tuple[str, float] | None = None
        self._compose_tonight = False

    def compose(self) -> ComposeResult:
        yield Static(ribbon({}), id="ribbon")
        yield Static("Pablo", id="title")
        with Horizontal(id="body"):
            with Vertical(id="left"):
                yield Label("NEEDS YOU · 0", id="inbox-label", classes="label waiting-label")
                yield ListView(id="inbox")
                yield Static(id="plan")
            with VerticalScroll(id="right"):
                yield Static(Text("Nothing selected. The team is on it.", style=MUTED), id="detail")
        yield Static("", id="status")
        yield Input(
            placeholder="Describe the task, Enter to send, Esc to close",
            id="composer",
            disabled=True,
        )
        yield Footer()

    def on_mount(self) -> None:
        self.refresh_data()
        if self.listen:
            self.listen_events()

    # ---- data ---------------------------------------------------------------
    @work(thread=True, exclusive=True, group="refresh")
    def refresh_data(self) -> None:
        try:
            inbox, plan = self.api.inbox(), self.api.plan()
        except HarnessError as exc:
            self.call_from_thread(self.set_status, str(exc), FAIL)
            return
        self.call_from_thread(self.show_data, inbox, plan)

    def show_data(self, inbox: list[Json], plan: Json | None) -> None:
        self.inbox, self.plan = inbox, plan
        if plan:
            self.query_one("#title", Static).update(
                Text.assemble(("Pablo  ", "bold"), (plan["project"], MUTED))
            )
        waiting = sum(r["state"] == "needs_you" for r in inbox)
        self.query_one("#inbox-label", Label).update(f"NEEDS YOU · {waiting}")
        view = self.query_one("#inbox", ListView)
        keep = view.index
        view.clear()
        for run in inbox:
            view.append(RunItem(run))
        if inbox:
            view.index = min(keep or 0, len(inbox) - 1)
            self.show_run(inbox[view.index])
        else:
            self.query_one("#detail", Static).update(
                Text("Nothing waiting. The team is on it.", style=MUTED)
            )
        self.query_one("#plan", Static).update(render_plan(plan))

    def show_run(self, run: Json) -> None:
        self.query_one("#detail", Static).update(render_run(run))

    def set_status(self, message: str, color: str = MUTED) -> None:
        self.query_one("#status", Static).update(Text(message, style=color))

    @work(thread=True, group="events")
    def listen_events(self) -> None:
        worker = get_current_worker()
        while not worker.is_cancelled:
            try:
                for event in self.api.events():
                    if worker.is_cancelled:
                        return
                    self.call_from_thread(self.on_harness_event, event)
            except Exception:  # Pablo restarting or connection dropped: wait, reconnect
                pass
            time.sleep(2)

    def on_harness_event(self, event: Json) -> None:
        kind, data = event["kind"], event.get("data", {})
        lane = data.get("lane")
        if lane:
            self.busy[lane] = kind not in ("judged", "decided", "error", "needs_you", "finished")
            self.query_one("#ribbon", Static).update(ribbon(self.busy, self.size.width))
        if kind == "status":
            self.set_status(f"● {data.get('text', '')}", LANE_COLORS.get(lane or "", MUTED))
        elif kind in ("judged", "decided", "needs_you", "error", "finished"):
            self.set_status("")
            self.refresh_data()

    # ---- selection ----------------------------------------------------------
    @on(ListView.Highlighted, "#inbox")
    def _highlighted(self, event: ListView.Highlighted) -> None:
        if isinstance(event.item, RunItem):
            self.show_run(event.item.run)

    def selected(self) -> Json | None:
        view = self.query_one("#inbox", ListView)
        item = view.highlighted_child
        return item.run if isinstance(item, RunItem) else None

    # ---- actions ------------------------------------------------------------
    @work(thread=True, group="decide")
    def decide(self, run_id: str, decision: str) -> None:
        try:
            self.api.decide(run_id, decision)
            done = {"approve": "approved", "reject": "rejected", "escalate": "escalated"}[decision]
            self.call_from_thread(self.set_status, f"{done} {run_id}")
        except HarnessError as exc:
            self.call_from_thread(self.set_status, str(exc), FAIL)
        self.call_from_thread(self.refresh_data)

    def action_approve(self) -> None:
        run = self.selected()
        if run:
            self.decide(run["id"], "approve")

    def action_reject(self) -> None:
        run = self.selected()
        if run:
            self.decide(run["id"], "reject")

    def action_escalate(self) -> None:
        """Escalation sends code off the machine, so it takes two presses."""
        run = self.selected()
        if not run or run["lane"] == "claude":
            return
        now = time.monotonic()
        armed = self._escalate_armed
        if armed and armed[0] == run["id"] and now - armed[1] < ESCALATE_CONFIRM_S:
            self._escalate_armed = None
            self.set_status("escalating to Claude…", LANE_COLORS["claude"])
            self.decide(run["id"], "escalate")
        else:
            self._escalate_armed = (run["id"], now)
            self.set_status("Press e again to send this task to Claude (leaves Pablo).", ACCENT)

    def action_tonight(self) -> None:
        self.action_new_task(tonight=True)

    @work(thread=True, group="brief")
    def action_brief(self) -> None:
        try:
            brief = self.api.brief()
        except HarnessError as exc:
            self.call_from_thread(self.set_status, str(exc), FAIL)
            return
        self.call_from_thread(self.show_brief, brief)

    def show_brief(self, brief: Json | None) -> None:
        self.query_one("#detail", Static).update(render_brief(brief))

    def action_new_task(self, tonight: bool = False) -> None:
        self._compose_tonight = tonight
        composer = self.query_one("#composer", Input)
        composer.placeholder = (
            "Queue for tonight, Enter to queue, Esc to close"
            if tonight
            else "Describe the task, Enter to send, Esc to close"
        )
        composer.disabled = False
        composer.add_class("open")
        composer.focus()

    def action_close_composer(self) -> None:
        composer = self.query_one("#composer", Input)
        composer.remove_class("open")
        composer.value = ""
        composer.disabled = True
        self.query_one("#inbox", ListView).focus()

    def action_refresh(self) -> None:
        self.refresh_data()

    def action_run_next(self) -> None:
        if not self.plan:
            return
        for phase in self.plan["phases"]:
            for task in phase["tasks"]:
                if task["status"] in ("needs_you", "active", "queued"):
                    self.start_run(task["title"], task["id"])
                    return

    @on(Input.Submitted, "#composer")
    def _submitted(self, event: Input.Submitted) -> None:
        task = event.value.strip()
        self.action_close_composer()
        if task and self._compose_tonight:
            self.queue_job(task)
        elif task:
            self.start_run(task, None)

    @work(thread=True, group="queue")
    def queue_job(self, task: str) -> None:
        try:
            job = self.api.add_job(task)
            self.call_from_thread(self.set_status, f"queued for tonight: {job['task'][:60]}")
        except HarnessError as exc:
            self.call_from_thread(self.set_status, str(exc), FAIL)

    @work(thread=True, group="runs")
    def start_run(self, task: str, task_id: str | None) -> None:
        self.call_from_thread(self.set_status, f"● routing: {task[:60]}", LANE_COLORS["router"])
        try:
            run = self.api.start(task, task_id=task_id)
            self.call_from_thread(
                self.set_status, f"{run['lane']} · {run['state'].replace('_', ' ')}"
            )
        except HarnessError as exc:
            self.call_from_thread(self.set_status, str(exc), FAIL)
        self.call_from_thread(self.refresh_data)


def run_tui(url: str) -> None:
    from pablo.client import HarnessClient

    PabloTUI(HarnessClient(url)).run()
