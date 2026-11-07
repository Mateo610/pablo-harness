// Work: the review desk. Every change waiting for a decision, grouped by project and by where
// it came from; one open at a time with its history, its evidence, and buttons that say what
// they do. Home is the overview and Chat is where you ask; Work is where you decide.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError, api, type HarnessEvent, type Me, type Run, type Verdict } from "./api";
import { laneColor, laneName, stateLabel } from "./components";
import { ago, go, STATE_COLOR, useEvents } from "./hud";
import { parseDiff } from "./hunks";

type Filter = "needs" | "ready" | "stale" | "decided";
type Outcome = { ok: boolean; text: string; at: string };

const FILTERS: { key: Filter; label: string }[] = [
  { key: "needs", label: "NEEDS YOU" },
  { key: "ready", label: "READY" },
  { key: "stale", label: "STALE" },
  { key: "decided", label: "DECIDED" },
];

const firstLine = (task: string) => task.split("\n")[0];
const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : word === word.toUpperCase() ? "S" : "s"}`;

/** Where a run came from, as a group label inside its project. */
function origin(run: Run): string {
  if (run.conversation_title) {
    return `${run.conversation_lane === "advisor" ? "Advisor" : "Chat"} · ${run.conversation_title}`;
  }
  if (run.task_id) return "Plan steps";
  if (run.retried_from) return "Retries";
  if (run.by === "") return "Terminal and overnight";
  if (run.by && run.by !== "owner") return `Started by ${run.by}`;
  return "Started directly";
}

function lanePart(run: Run): string {
  return run.self_judged ? `${laneName("advisor")} · self-judged` : laneName(run.lane);
}

export function Work({ me, focus }: { me: Me | null; focus: string }) {
  const [inbox, setInbox] = useState<Run[] | null>(null);
  const [decided, setDecided] = useState<Run[]>([]);
  const [filter, setFilter] = useState<Filter>("needs");
  const [picked, setPicked] = useState(focus);
  const [folded, setFolded] = useState<Set<string>>(new Set());
  const [outcomes, setOutcomes] = useState<Record<string, Outcome>>({});
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    api
      .inbox()
      .then((rs) => {
        setInbox(rs);
        setError("");
      })
      .catch((e: Error) => setError(`Couldn't load the queue: ${e.message}`));
    api
      .runs(120)
      .then((rs) => setDecided(rs.filter((r) => ["approved", "rejected", "failed"].includes(r.state)).slice(0, 60)))
      .catch(() => undefined);
  }, []);

  useEffect(load, [load]);
  const timer = useRef<number | undefined>(undefined);
  useEvents((e: HarnessEvent) => {
    if (!["judged", "decided", "needs_you", "finished", "error"].includes(e.kind)) return;
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(load, 400); // a burst of events is one reload
  });
  useEffect(() => {
    if (focus) setPicked(focus);
  }, [focus]);

  const lists = useMemo(() => {
    const all = inbox ?? [];
    return {
      needs: all.filter((r) => !r.stale && r.state === "needs_you"),
      ready: all.filter((r) => !r.stale && r.state === "ready"),
      stale: all.filter((r) => r.stale),
      decided,
    } satisfies Record<Filter, Run[]>;
  }, [inbox, decided]);

  // Open on whichever list has something, the first time the queue arrives.
  const chose = useRef(false);
  useEffect(() => {
    if (chose.current || inbox === null) return;
    chose.current = true;
    if (focus) {
      const where = (Object.keys(lists) as Filter[]).find((k) => lists[k].some((r) => r.id === focus));
      if (where) setFilter(where);
      return;
    }
    if (!lists.needs.length) setFilter(lists.ready.length ? "ready" : lists.stale.length ? "stale" : "decided");
  }, [inbox, lists, focus]);

  const visible = lists[filter];
  const groups = useMemo(() => groupRuns(visible), [visible]);
  const ordered = useMemo(() => groups.flatMap((g) => g.origins.flatMap((o) => o.runs)), [groups]);
  const everyRun = useMemo(() => [...(inbox ?? []), ...decided], [inbox, decided]);
  const current = everyRun.find((r) => r.id === picked) ?? ordered[0] ?? null;

  const pick = (id: string) => {
    setPicked(id);
    window.history.replaceState(null, "", `#/work/${id}`);
  };

  const step = (by: number) => {
    if (!ordered.length) return;
    const i = current ? ordered.findIndex((r) => r.id === current.id) : -1;
    const next = ordered[Math.max(0, Math.min(ordered.length - 1, i + by))];
    if (next) pick(next.id);
  };

  const record = (id: string, ok: boolean, text: string) =>
    setOutcomes((o) => ({ ...o, [id]: { ok, text, at: new Date().toISOString() } }));

  const discardStale = () => {
    setBusy(true);
    api
      .discardStale()
      .then((r) => {
        setError("");
        record("__stale", true, `Discarded ${plural(r.discarded.length, "stale run")}. No repo changed.`);
        load();
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setBusy(false));
  };

  const owner = Boolean(me?.can_approve);
  const count = (k: Filter) => (k === "decided" ? "" : ` ${lists[k].length}`);

  return (
    <div className="w-desk">
      <aside className="w-queue" aria-label="Changes waiting">
        <div className="w-queue-head">
          <h1 className="h-title" style={{ fontSize: 16 }}>
            WORK
          </h1>
          <span className="h-faint" style={{ fontSize: 12 }}>
            decide on changes
          </span>
        </div>
        <div className="h-seg w-filters" role="group" aria-label="Show">
          {FILTERS.map((f) => (
            <button key={f.key} aria-pressed={filter === f.key} onClick={() => setFilter(f.key)}>
              {f.label}
              {count(f.key)}
            </button>
          ))}
        </div>
        {filter === "stale" && lists.stale.length > 0 && owner && (
          <div className="w-note">
            <span>Replaced by a retry, beaten by another version, or waiting more than 3 days.</span>
            <button className="h-btn danger" disabled={busy} onClick={discardStale}>
              DISCARD ALL {lists.stale.length}
            </button>
          </div>
        )}
        {outcomes.__stale && <div className="w-done ok">{outcomes.__stale.text}</div>}
        {error && <div className="h-error">{error}</div>}
        {inbox === null && !error && <p className="h-dim">Loading…</p>}
        {inbox !== null && visible.length === 0 && <p className="h-dim w-empty">{EMPTY[filter]}</p>}
        {groups.map((g) => {
          const closed = folded.has(g.project);
          return (
            <section key={g.project} className="w-group">
              <button
                className="w-project"
                aria-expanded={!closed}
                onClick={() =>
                  setFolded((s) => {
                    const n = new Set(s);
                    if (n.has(g.project)) n.delete(g.project);
                    else n.add(g.project);
                    return n;
                  })
                }
              >
                <span className="w-caret" aria-hidden="true">
                  {closed ? "▸" : "▾"}
                </span>
                <span className="w-grow">{g.project}</span>
                <span className="h-mono h-dim">{g.total}</span>
              </button>
              {!closed &&
                g.origins.map((o) => (
                  <div key={o.label} className="w-origin">
                    <div className="w-origin-label" title={o.label}>
                      {o.label}
                    </div>
                    {o.runs.map((r) => (
                      <button key={r.id} className="w-item" aria-current={current?.id === r.id ? "true" : undefined} onClick={() => pick(r.id)}>
                        <span className="w-dot" style={{ background: STATE_COLOR[r.state] ?? "var(--ink-faint)" }} aria-hidden="true" />
                        <span className="w-grow">
                          <span className="w-task">{firstLine(r.task)}</span>
                          <span className="w-meta">
                            {lanePart(r)}
                            {r.certainty != null ? ` · ${r.certainty.toFixed(2)}` : ""} · {ago(r.created_at)}
                            {outcomes[r.id] ? ` · ${outcomes[r.id].ok ? "done" : "failed"}` : ""}
                          </span>
                        </span>
                      </button>
                    ))}
                  </div>
                ))}
            </section>
          );
        })}
        <p className="h-faint w-keys">j / k move · a apply · x discard</p>
      </aside>

      <main className="w-detail">
        {current ? (
          <Detail
            key={current.id}
            run={current}
            me={me}
            outcome={outcomes[current.id]}
            onOutcome={(ok, text) => record(current.id, ok, text)}
            onChanged={load}
            onNext={() => step(1)}
            onPrev={() => step(-1)}
            hasNext={ordered.length > 0 && ordered[ordered.length - 1]?.id !== current.id}
          />
        ) : (
          <div className="h-page">
            <p className="h-dim">{inbox === null ? "Loading…" : "Nothing to decide. Start work in Chat or Voice; it lands here when it needs you."}</p>
          </div>
        )}
      </main>
    </div>
  );
}

const EMPTY: Record<Filter, string> = {
  needs: "Nothing needs you. Runs the judge isn't sure about land here.",
  ready: "Nothing ready. Runs that passed their checks and the judge land here.",
  stale: "Nothing stale.",
  decided: "Nothing decided yet.",
};

type Group = { project: string; total: number; origins: { label: string; runs: Run[] }[] };

function groupRuns(runs: Run[]): Group[] {
  const byProject = new Map<string, Map<string, Run[]>>();
  for (const r of runs) {
    const project = r.project || "default";
    const origins = byProject.get(project) ?? new Map<string, Run[]>();
    byProject.set(project, origins);
    const label = origin(r);
    origins.set(label, [...(origins.get(label) ?? []), r]);
  }
  return [...byProject.entries()].map(([project, origins]) => ({
    project,
    total: [...origins.values()].reduce((n, rs) => n + rs.length, 0),
    origins: [...origins.entries()].map(([label, rs]) => ({ label, runs: rs })),
  }));
}

function Detail({
  run,
  me,
  outcome,
  onOutcome,
  onChanged,
  onNext,
  onPrev,
  hasNext,
}: {
  run: Run;
  me: Me | null;
  outcome?: Outcome;
  onOutcome: (ok: boolean, text: string) => void;
  onChanged: () => void;
  onNext: () => void;
  onPrev: () => void;
  hasNext: boolean;
}) {
  const files = useMemo(() => parseDiff(run.diff), [run.diff]);
  const allIds = useMemo(() => files.flatMap((f) => f.hunks.map((h) => h.id)), [files]);
  const [kept, setKept] = useState<Set<string>>(() => new Set(allIds));
  const [busy, setBusy] = useState<"" | "apply" | "discard" | "claude">("");
  const [blocked, setBlocked] = useState<{ message: string; verdict: Verdict } | null>(null);
  const open = run.state === "ready" || run.state === "needs_you";
  const owner = Boolean(me?.can_approve);
  const canDiscard = open && (owner || (me !== null && run.by === me.profile));
  const partial = kept.size < allIds.length;
  const project = run.project || "the project";
  const fileCount = run.verdict?.files.length ?? files.length;

  const apply = (force = false) => {
    setBusy("apply");
    api
      .approve(run.id, partial ? [...kept] : undefined, force)
      .then((r) => {
        setBlocked(null);
        const what = partial ? `${kept.size} of ${allIds.length} hunks` : plural(fileCount, "file");
        onOutcome(true, `Applied ${what} to ${r.project || project}. The files changed in its working tree; nothing was committed.`);
        onChanged();
      })
      .catch((e: unknown) => {
        if (e instanceof ApiError && e.verdict) setBlocked({ message: e.message, verdict: e.verdict });
        onOutcome(false, `Not applied: ${(e as Error).message}`);
      })
      .finally(() => setBusy(""));
  };
  const discard = () => {
    setBusy("discard");
    api
      .reject(run.id)
      .then(() => {
        onOutcome(true, `Discarded. Nothing in ${project} changed.`);
        onChanged();
      })
      .catch((e: Error) => onOutcome(false, `Not discarded: ${e.message}`))
      .finally(() => setBusy(""));
  };
  const toClaude = () => {
    setBusy("claude");
    api
      .escalate(run.id)
      .then((r) => {
        onOutcome(true, `Sent to Claude as run ${r.id}. It shows up here when it's done.`);
        onChanged();
      })
      .catch((e: Error) => onOutcome(false, `Not sent: ${e.message}`))
      .finally(() => setBusy(""));
  };

  // Keyboard: j/k move, a apply, x discard. Not while typing.
  const keys = useRef({ apply, discard, onNext, onPrev, open, owner, canDiscard });
  keys.current = { apply, discard, onNext, onPrev, open, owner, canDiscard };
  useEffect(() => {
    const on = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (t?.closest("input, textarea, [contenteditable='true']") || e.metaKey || e.ctrlKey || e.altKey) return;
      const k = keys.current;
      if (e.key === "j") k.onNext();
      else if (e.key === "k") k.onPrev();
      else if (e.key === "a" && k.open && k.owner) k.apply();
      else if (e.key === "x" && k.canDiscard) k.discard();
      else return;
      e.preventDefault();
    };
    window.addEventListener("keydown", on);
    return () => window.removeEventListener("keydown", on);
  }, []);

  const convoPage = run.conversation_lane === "advisor" ? "advisor" : "chat";
  const v = run.verdict;

  return (
    <div className="h-page w-page">
      <header className="w-head">
        <div className="w-crumbs">
          <span className="h-chip">{run.project || "default project"}</span>
          {run.conversation_id ? (
            <a className="h-chip w-link" href={`#/${convoPage}/${run.conversation_id}`}>
              from {convoPage === "advisor" ? "Advisor" : "Chat"}: {run.conversation_title || "conversation"} ↗
            </a>
          ) : run.task_id ? (
            <span className="h-chip">plan step {run.task_id}</span>
          ) : null}
          {run.retried_from && (
            <button className="h-chip w-link" onClick={() => go("work", run.retried_from ?? "")}>
              retry of {run.retried_from}
            </button>
          )}
          {run.escalated_from && (
            <button className="h-chip w-link" onClick={() => go("work", run.escalated_from ?? "")}>
              escalated from {run.escalated_from}
            </button>
          )}
          <span className="h-chip" style={{ color: laneColor(run.self_judged ? "advisor" : run.lane) }}>
            {lanePart(run)} · {run.model.replace("pablo/", "")}
          </span>
          <span className="h-chip">{run.by === "owner" ? "you" : run.by || "terminal"} · {ago(run.created_at)}</span>
          <span className="h-mono h-faint">run {run.id}</span>
        </div>
        <h2 className="w-title">{firstLine(run.task)}</h2>
        {run.task.includes("\n") && (
          <details className="w-more">
            <summary>Full task</summary>
            <pre>{run.task}</pre>
          </details>
        )}
      </header>

      <section className={`h-frame h-pad w-decide ${run.state === "needs_you" ? "amber" : ""}`} aria-label="Decision">
        <div className="w-state">
          <span style={{ color: STATE_COLOR[run.state], letterSpacing: ".2em", fontWeight: 700, fontSize: 12 }}>
            {stateLabel(run).toUpperCase()}
          </span>
          {run.certainty != null && (
            <span className="h-mono h-dim">
              judge {run.certainty.toFixed(2)}
              {run.self_judged ? " (judged by the model that wrote it)" : ""}
            </span>
          )}
          {run.stale && <span className="h-chip">stale · {run.stale_reason}</span>}
        </div>
        {(run.question || run.judge_summary) && <p className="w-say">{run.question || run.judge_summary}</p>}
        {outcome && (
          <div className={`w-done ${outcome.ok ? "ok" : "bad"}`} role="status">
            {outcome.text}
          </div>
        )}
        {blocked && (
          <div className="h-error">
            {blocked.message}
            <div style={{ marginTop: 8 }}>
              <button className="h-btn danger" onClick={() => apply(true)} disabled={busy !== ""}>
                APPLY ANYWAY
              </button>
            </div>
          </div>
        )}
        {open ? (
          <>
            <div className="w-buttons">
              {owner && (
                <button className="h-btn amber solid" disabled={busy !== "" || (allIds.length > 0 && kept.size === 0)} onClick={() => apply()}>
                  {busy === "apply"
                    ? "APPLYING…"
                    : !run.diff
                      ? "ACCEPT"
                      : partial
                        ? `APPLY ${kept.size} OF ${allIds.length} HUNKS TO ${project.toUpperCase()}`
                        : `APPLY ${plural(fileCount, "FILE")} TO ${project.toUpperCase()}`}
                </button>
              )}
              {canDiscard && (
                <button className="h-btn danger" disabled={busy !== ""} onClick={discard}>
                  {busy === "discard" ? "DISCARDING…" : "DISCARD"}
                </button>
              )}
              {owner && !run.self_judged && run.lane !== "claude" && (
                <button className="h-btn" disabled={busy !== ""} onClick={toClaude} title="Claude runs outside Pablo">
                  {busy === "claude" ? "SENDING…" : "SEND TO CLAUDE"}
                </button>
              )}
            </div>
            <p className="h-faint w-explain">
              Apply writes the change into {project}'s files (uncommitted, so you can still review it in git). Discard drops it
              {owner ? "" : ". Only the owner can apply"}; nothing in the repo changes.
              {!run.self_judged && run.lane !== "claude" && owner ? " Send to Claude asks Claude for its own version; this one stays here." : ""}
            </p>
          </>
        ) : (
          <div className="w-buttons">
            <span className="h-dim" style={{ fontSize: 13 }}>
              {run.state === "approved"
                ? run.applied_hunks
                  ? `Applied ${run.applied_hunks.length} of ${allIds.length} hunks.`
                  : "Applied."
                : run.state === "rejected"
                  ? "Discarded. Nothing in the repo changed."
                  : run.state === "failed"
                    ? "Failed before it reached you."
                    : "Still working."}
            </span>
            {hasNext && (
              <button className="h-btn" onClick={onNext}>
                NEXT →
              </button>
            )}
          </div>
        )}
      </section>

      <div className="h-cols">
        <div className="h-col-main">
          {run.explanation && <p className="w-say">{run.explanation}</p>}
          {files.length === 0 && <p className="h-dim">No diff: this run didn't change any files.</p>}
          {files.map((f) =>
            f.hunks.map((h, i) => {
              const keep = kept.has(h.id);
              return (
                <div key={h.id} className={`w-hunk ${keep ? "" : "dropped"}`}>
                  <div className="w-hunk-head">
                    <span className="h-mono">
                      {f.path} · hunk {i + 1} of {f.hunks.length}
                    </span>
                    <span className="h-mono h-dim">
                      +{h.added} −{h.removed}
                    </span>
                    <span className="w-grow" />
                    {open && owner && allIds.length > 1 && (
                      <button
                        className="h-btn"
                        style={{ minHeight: 28 }}
                        aria-pressed={keep}
                        onClick={() => {
                          setBlocked(null);
                          setKept((prev) => {
                            const next = new Set(prev);
                            if (next.has(h.id)) next.delete(h.id);
                            else next.add(h.id);
                            return next;
                          });
                        }}
                      >
                        {keep ? "KEEP" : "DROPPED"}
                      </button>
                    )}
                  </div>
                  {keep && (
                    <pre className="w-diff">
                      <div className="hunk">{h.header}</div>
                      {h.lines.map((line, j) => (
                        <div key={j} className={line.startsWith("+") ? "add" : line.startsWith("-") ? "del" : ""}>
                          {line || " "}
                        </div>
                      ))}
                    </pre>
                  )}
                </div>
              );
            }),
          )}
        </div>
        <aside className="h-col-side">
          <section className="h-frame h-pad">
            <div className="w-side-title">FILES IN THIS CHANGE</div>
            {v && v.files.length ? (
              <ul className="w-files">
                {v.files.map((f) => (
                  <li key={f} className="h-mono">
                    {f}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="h-dim">No files.</p>
            )}
            {v && (
              <p className="h-mono h-dim" style={{ fontSize: 12 }}>
                <span style={{ color: "var(--status-pass)" }}>+{v.added}</span> <span style={{ color: "var(--status-fail)" }}>−{v.removed}</span>
              </p>
            )}
          </section>
          <section className="h-frame h-pad">
            <div className="w-side-title">CHECKS</div>
            {!v ? (
              <p className="h-dim">Not checked yet.</p>
            ) : !v.applied ? (
              <p style={{ color: "var(--status-fail)" }}>The diff didn't apply: {v.error}</p>
            ) : v.checks.length === 0 ? (
              <p className="h-dim">This project has no checks.</p>
            ) : (
              <ul className="w-files">
                {v.checks.map((c) => (
                  <li key={c.name} style={{ color: c.passed ? "var(--status-pass)" : c.status === "already failing" ? "var(--ink-muted)" : "var(--status-fail)" }}>
                    {c.passed ? "✓" : c.status === "already failing" ? "–" : "✗"} {c.name} · {c.status ?? (c.passed ? "passed" : "failed")}
                  </li>
                ))}
              </ul>
            )}
          </section>
          <Timeline runId={run.id} state={run.state} />
        </aside>
      </div>
    </div>
  );
}

/** What happened to this run, in order: routed, written, checked, judged, decided. */
function Timeline({ runId, state }: { runId: string; state: string }) {
  const [events, setEvents] = useState<HarnessEvent[] | null>(null);
  const [all, setAll] = useState(false);
  useEffect(() => {
    api
      .events(runId)
      .then(setEvents)
      .catch(() => setEvents([]));
  }, [runId, state]);
  useEvents((e) => {
    if (e.run_id === runId) setEvents((prev) => (prev ? [...prev, e] : prev));
  });
  if (events === null) return null;
  const t0 = events.length ? new Date(events[0].at).getTime() : 0;
  const shown = all ? events : events.slice(-8);
  return (
    <section className="h-frame h-pad">
      <div className="w-side-title">HISTORY</div>
      {events.length === 0 ? (
        <p className="h-dim">No events recorded for this run.</p>
      ) : (
        <ol className="w-timeline">
          {shown.map((e, i) => (
            <li key={i}>
              <span className="h-mono h-faint">+{Math.round((new Date(e.at).getTime() - t0) / 1000)}s</span>
              <span className="w-dot" style={{ background: laneColor(String(e.data.lane ?? "")) }} aria-hidden="true" />
              <span>
                <b>{e.kind}</b> <span className="h-dim">{describe(e)}</span>
              </span>
            </li>
          ))}
        </ol>
      )}
      {events.length > 8 && (
        <button className="h-btn" style={{ minHeight: 28, marginTop: 8 }} onClick={() => setAll((a) => !a)}>
          {all ? "SHOW LAST 8" : `SHOW ALL ${events.length}`}
        </button>
      )}
    </section>
  );
}

function describe(e: HarnessEvent): string {
  const d = e.data as Record<string, unknown>;
  if (typeof d.text === "string") return d.text;
  if (typeof d.summary === "string") return d.summary;
  if (typeof d.reason === "string") return `${String(d.lane ?? "")} · ${d.reason}`;
  if (typeof d.certainty === "number") return `certainty ${d.certainty.toFixed(2)} → ${String(d.state)}`;
  if (typeof d.decision === "string") return d.decision.replace("_", " ");
  if (typeof d.state === "string") return d.state.replace("_", " ");
  if (typeof d.question === "string") return d.question;
  return "";
}
