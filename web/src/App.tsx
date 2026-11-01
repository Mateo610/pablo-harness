import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, subscribe, type Brief, type HarnessEvent, type Job, type Lane, type Plan, type PlanTask, type Run } from "./api";
import {
  BriefCard,
  CertaintyGauge,
  DiffView,
  EvidenceCards,
  LaneBadge,
  LaneRibbon,
  TonightList,
  laneColor,
  stateLabel,
  todayLocal,
} from "./components";
import { Palette } from "./Palette";
import { Phone } from "./Phone";

type Mode = "plan" | "build" | "quick";

export function App() {
  const [lanes, setLanes] = useState<Lane[]>([]);
  const [plan, setPlan] = useState<Plan | null>(null);
  const [runs, setRuns] = useState<Run[]>([]);
  const [status, setStatus] = useState<HarnessEvent | null>(null);
  const [busy, setBusy] = useState<Record<string, number>>({});
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [draft, setDraft] = useState("");
  const [mode, setMode] = useState<Mode>("build");
  const [error, setError] = useState("");
  const [jobs, setJobs] = useState<Job[]>([]);
  const [brief, setBrief] = useState<Brief | null>(null);
  const [briefDismissed, setBriefDismissed] = useState(() => readDismissed());
  const composer = useRef<HTMLTextAreaElement>(null);
  const narrow = useNarrow();
  const openBriefRef = useRef<() => void>(() => undefined);

  const refresh = useCallback(async () => {
    const [inbox] = await Promise.all([
      api.inbox(),
      api.plan().then(setPlan).catch(() => setPlan(null)),
      api.queue().then(setJobs).catch(() => undefined),
      api.brief().then(setBrief),
    ]);
    setRuns((prev) => {
      const byId = new Map(prev.map((r) => [r.id, r]));
      inbox.forEach((r) => byId.set(r.id, r));
      return [...byId.values()].sort((a, b) => a.created_at.localeCompare(b.created_at));
    });
  }, []);

  useEffect(() => {
    api.lanes().then(setLanes).catch((e: Error) => setError(e.message));
    refresh().catch((e: Error) => setError(e.message));
    return subscribe((event) => {
      if (event.kind === "status") setStatus(event);
      if (["judged", "decided", "error", "finished"].includes(event.kind)) setStatus(null);
      if (event.data.lane) {
        const lane = event.data.lane;
        const done = ["judged", "decided", "error", "needs_you", "finished"].includes(event.kind);
        setBusy((b) => ({ ...b, [lane]: done ? 0 : 1 }));
      }
      if (event.kind === "queue") api.queue().then(setJobs).catch(() => undefined);
      if (event.kind === "brief") api.brief().then(setBrief);
      if (["judged", "decided", "needs_you", "finished"].includes(event.kind)) {
        api.run(event.run_id).then((run) => setRuns((prev) => upsert(prev, run))).catch(() => undefined);
      }
    });
  }, [refresh]);

  useEffect(() => {
    let lastG = 0;
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPaletteOpen((o) => !o);
        return;
      }
      const target = e.target as HTMLElement | null;
      const typing = target?.closest("input, textarea, [contenteditable='true'], [cmdk-root]");
      if (typing || e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === "n") {
        e.preventDefault();
        composer.current?.focus();
      } else if (e.key === "g") {
        lastG = Date.now();
      } else if (e.key === "m" && Date.now() - lastG < 1000) {
        openBriefRef.current();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const inbox = useMemo(() => runs.filter((r) => r.state === "needs_you" || r.state === "ready"), [runs]);
  const phase = plan?.phases.find((p) => !p.tasks.every((t) => t.status === "done")) ?? null;
  const nextTask =
    phase?.tasks.find((t) => t.status === "needs_you") ??
    phase?.tasks.find((t) => t.status === "active") ??
    phase?.tasks.find((t) => t.status === "queued") ??
    null;

  const act = (fn: () => Promise<Run>) => {
    setError("");
    fn()
      .then((run) => {
        setRuns((prev) => upsert(prev, run));
        return refresh();
      })
      .catch((e: Error) => setError(e.message));
  };

  const send = (task: string, taskId?: string) => {
    if (!task.trim()) return;
    const lane = mode === "plan" ? "deep" : undefined;
    setDraft("");
    act(() => api.start(task, lane, taskId));
  };

  const runTask = (t: PlanTask) => send(t.title, t.id);

  const queueTonight = (task: string, taskId?: string) => {
    if (!task.trim()) return;
    setDraft("");
    setError("");
    api
      .addJob(task, mode === "quick" ? "coder" : undefined, taskId)
      .then(() => api.queue().then(setJobs))
      .catch((e: Error) => setError(e.message));
  };

  const cancelJob = (id: string) =>
    api
      .cancelJob(id)
      .then(() => api.queue().then(setJobs))
      .catch((e: Error) => setError(e.message));

  const dismissBrief = () => {
    if (!brief) return;
    setBriefDismissed(brief.day);
    try {
      localStorage.setItem("pablo.briefDismissed", brief.day);
    } catch {
      /* storage can be unavailable; dismissal just won't persist */
    }
  };

  const openBrief = () => {
    setBriefDismissed("");
    if (!brief || brief.day !== todayLocal()) {
      api.makeBrief().then(setBrief).catch((e: Error) => setError(e.message));
    }
  };

  openBriefRef.current = openBrief;

  const showBrief = brief !== null && brief.day === todayLocal() && briefDismissed !== brief.day;

  if (narrow) {
    return (
      <Phone
        lanes={lanes}
        busy={busy}
        inbox={inbox}
        phase={phase}
        project={plan?.project ?? ""}
        statusText={status ? String(status.data.text ?? "") : ""}
        error={error}
        onApprove={(id) => act(() => api.approve(id))}
        onReject={(id) => act(() => api.reject(id))}
        onEscalate={(id) => act(() => api.escalate(id))}
        onSend={(task, taskId) => send(task, taskId)}
        jobs={jobs}
        brief={showBrief ? brief : null}
        onQueue={(task) => queueTonight(task)}
        onCancelJob={cancelJob}
        onDismissBrief={dismissBrief}
      />
    );
  }

  return (
    <div className="app">
      <LaneRibbon lanes={lanes} busy={busy} />
      <header className="header">
        <div className="brand">
          <span className="brand-name">Pablo</span>
          <span className="mono muted">{plan?.project ?? "no plan yet"}</span>
        </div>
        <button type="button" className="search" onClick={() => setPaletteOpen(true)}>
          <span className="grow">Search or run a command</span>
          <kbd>Ctrl K</kbd>
        </button>
        <span className="mono muted tabular">{lanes.length} lanes</span>
      </header>

      {error && (
        <div className="error-bar" role="alert">
          {error}
        </div>
      )}

      <div className="columns">
        <aside className="side" aria-label="Needs you and plan">
          <section className="panel">
            <h2 className="label accent">Needs you · {inbox.length}</h2>
            {inbox.length === 0 && <p className="muted">Nothing waiting. The team is on it.</p>}
            {inbox.map((r) => (
              <a key={r.id} href={`#run-${r.id}`} className={`inbox-item ${r.state === "needs_you" ? "waiting" : ""}`}>
                <LaneBadge lane={r.lane} />
                <span className="inbox-text">
                  <span>{r.task}</span>
                  <span className="muted small">
                    {stateLabel(r)}
                    {r.certainty !== null && ` · certainty ${r.certainty.toFixed(2)}`}
                  </span>
                </span>
              </a>
            ))}
          </section>

          {phase && (
            <section className="panel">
              <div className="row-between">
                <h2 className="label">
                  Phase {phase.id} · {phase.title}
                </h2>
                <span className="mono muted tabular small">
                  {phase.tasks.filter((t) => t.status === "done").length}/{phase.tasks.length}
                </span>
              </div>
              <div className="progress">
                {phase.tasks.map((t) => (
                  <span key={t.id} className={`seg ${t.status}`} />
                ))}
              </div>
              <ol className="tasks">
                {phase.tasks.map((t) => (
                  <li key={t.id} className={`task ${t.status}`}>
                    <span>
                      {t.id} {t.title}
                    </span>
                    {t.status === "queued" ? (
                      <button type="button" className="link-btn" onClick={() => runTask(t)}>
                        Run
                      </button>
                    ) : (
                      <span className={`small status-${t.status}`}>{t.status.replace("_", " ")}</span>
                    )}
                  </li>
                ))}
              </ol>
              <p className="gate small muted">Gate: {phase.gate}</p>
            </section>
          )}

          <section className="panel">
            <h2 className="label">Tonight</h2>
            <TonightList jobs={jobs} onCancel={cancelJob} />
          </section>
        </aside>

        <main className="chat">
          <div className="thread">
            {showBrief && brief && <BriefCard brief={brief} onDismiss={dismissBrief} />}
            {runs.length === 0 && (
              <div className="empty">
                <h1 className="display">What should the team build?</h1>
                <p className="muted">Describe a task below, or press Ctrl K.</p>
              </div>
            )}
            {runs.map((r) => (
              <RunView
                key={r.id}
                run={r}
                onApprove={() => act(() => api.approve(r.id))}
                onReject={() => act(() => api.reject(r.id))}
                onEscalate={() => act(() => api.escalate(r.id))}
              />
            ))}
          </div>

          <form
            className="composer"
            onSubmit={(e) => {
              e.preventDefault();
              send(draft);
            }}
          >
            {status && (
              <div className="status-line mono">
                <span className="dot" style={{ background: laneColor(status.data.lane ?? "") }} />
                {String(status.data.text ?? "")}
              </div>
            )}
            <div className="composer-box">
              <label htmlFor="composer" className="sr">
                Message the team
              </label>
              <textarea
                id="composer"
                ref={composer}
                rows={2}
                value={draft}
                placeholder="Describe the task"
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    send(draft);
                  }
                }}
              />
              <div className="modes" role="group" aria-label="Mode">
                {(["plan", "build", "quick"] as Mode[]).map((m) => (
                  <button key={m} type="button" aria-pressed={mode === m} onClick={() => setMode(m)}>
                    {m[0].toUpperCase() + m.slice(1)}
                  </button>
                ))}
              </div>
              <button
                type="button"
                className="btn tonight-btn"
                title="Queue for tonight instead of running now"
                onClick={() => queueTonight(draft)}
              >
                Tonight
              </button>
              <button type="submit" className="send" aria-label="Send">
                ↑
              </button>
            </div>
          </form>
        </main>

        <aside className="side" aria-label="Team">
          <section className="panel">
            <h2 className="label">Team, live</h2>
            {lanes.map((l) => (
              <div key={l.name} className="team-row">
                <span
                  className="team-mark"
                  style={
                    l.name === "claude"
                      ? { borderLeft: `2px dashed ${laneColor(l.name)}`, width: 0 }
                      : { background: busy[l.name] ? laneColor(l.name) : "var(--line)" }
                  }
                />
                <span className="team-text">
                  <span className="strong">{l.name[0].toUpperCase() + l.name.slice(1)}</span>
                  <span className="muted small">
                    {busy[l.name] ? "working" : l.name === "claude" ? "only when you escalate" : l.role}
                  </span>
                </span>
              </div>
            ))}
          </section>
        </aside>
      </div>

      <Palette
        open={paletteOpen}
        onOpenChange={setPaletteOpen}
        inbox={inbox}
        nextTask={nextTask}
        actions={{
          approve: (id) => act(() => api.approve(id)),
          reject: (id) => act(() => api.reject(id)),
          escalate: (id) => act(() => api.escalate(id)),
          runTask,
          focusComposer: () => composer.current?.focus(),
          openBrief,
          runQueueNow: () => {
            api
              .runQueueNow()
              .then(() => refresh())
              .catch((e: Error) => setError(e.message));
          },
        }}
      />
    </div>
  );
}

function RunView({
  run,
  onApprove,
  onReject,
  onEscalate,
}: {
  run: Run;
  onApprove: () => void;
  onReject: () => void;
  onEscalate: () => void;
}) {
  const open = run.state === "ready" || run.state === "needs_you";
  return (
    <>
      <div className="user-msg">{run.task}</div>
      <article id={`run-${run.id}`} className={`run ${run.state === "needs_you" ? "waiting" : ""}`}>
        <div className="run-head">
          <LaneBadge lane={run.lane} model={run.model} />
          <span className="grow" />
          {run.certainty !== null && <CertaintyGauge value={run.certainty} />}
        </div>
        {run.explanation && <p className="run-text">{run.explanation}</p>}
        {run.verdict && <EvidenceCards verdict={run.verdict} />}
        {run.diff && <DiffView diff={run.diff} />}
        {run.question && run.state === "needs_you" && <p className="question">{run.question}</p>}
        {open ? (
          <div className="actions">
            <button type="button" className="btn primary" onClick={onApprove}>
              Approve and apply
            </button>
            <button type="button" className="btn" onClick={onReject}>
              Reject
            </button>
            {run.lane !== "claude" && (
              <button type="button" className="btn escalate" onClick={onEscalate}>
                Escalate to Claude
              </button>
            )}
          </div>
        ) : (
          <p className="muted small">{stateLabel(run)}</p>
        )}
      </article>
    </>
  );
}

function readDismissed(): string {
  try {
    return localStorage.getItem("pablo.briefDismissed") ?? "";
  } catch {
    return "";
  }
}

function useNarrow(query = "(max-width: 720px)"): boolean {
  const [narrow, setNarrow] = useState(() => window.matchMedia(query).matches);
  useEffect(() => {
    const mq = window.matchMedia(query);
    const onChange = () => setNarrow(mq.matches);
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, [query]);
  return narrow;
}

function upsert(list: Run[], run: Run): Run[] {
  const i = list.findIndex((r) => r.id === run.id);
  if (i === -1) return [...list, run];
  const next = list.slice();
  next[i] = run;
  return next;
}
