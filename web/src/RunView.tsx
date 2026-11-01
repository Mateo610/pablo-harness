// One run in the thread: evidence, hunk-by-hunk review, decision buttons, replay.
import { useEffect, useMemo, useRef, useState } from "react";
import { ApiError, api, type HarnessEvent, type Run, type Verdict } from "./api";
import { CertaintyGauge, EvidenceCards, LaneBadge, laneColor, stateLabel } from "./components";
import { parseDiff } from "./hunks";

export interface RunActions {
  onApproved: (run: Run) => void;
  onReject: () => void;
  onEscalate: () => void;
  onError: (message: string) => void;
}

export function RunView({ run, compact = false, ...actions }: { run: Run; compact?: boolean } & RunActions) {
  const open = run.state === "ready" || run.state === "needs_you";
  const files = useMemo(() => parseDiff(run.diff), [run.diff]);
  const allIds = useMemo(() => files.flatMap((f) => f.hunks.map((h) => h.id)), [files]);
  const [kept, setKept] = useState<Set<string>>(() => new Set(allIds));
  const [blocked, setBlocked] = useState<{ message: string; verdict: Verdict } | null>(null);
  const [replaying, setReplaying] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => setKept(new Set(allIds)), [allIds]);

  const partial = kept.size < allIds.length;
  const toggle = (id: string) => {
    setBlocked(null);
    setKept((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const approve = (force = false) => {
    setBusy(true);
    api
      .approve(run.id, partial ? [...kept] : undefined, force)
      .then((r) => {
        setBlocked(null);
        actions.onApproved(r);
      })
      .catch((e: unknown) => {
        if (e instanceof ApiError && e.verdict) setBlocked({ message: e.message, verdict: e.verdict });
        else actions.onError((e as Error).message);
      })
      .finally(() => setBusy(false));
  };

  return (
    <article id={`run-${run.id}`} className={`run ${run.state === "needs_you" ? "waiting" : ""}`}>
      <div className="run-head">
        <LaneBadge lane={run.lane} model={compact ? undefined : run.model} />
        <span className="grow" />
        {run.certainty !== null && <CertaintyGauge value={run.certainty} settled={!open} />}
      </div>
      {run.explanation && <p className="run-text">{run.explanation}</p>}
      {run.verdict && <EvidenceCards verdict={run.verdict} />}

      {files.map((f) =>
        f.hunks.map((h, i) => {
          const keep = kept.has(h.id);
          return (
            <div key={h.id} className={`hunk ${keep ? "" : "dropped"}`}>
              <div className="hunk-head">
                <span className="mono">
                  {f.path} · hunk {i + 1} of {f.hunks.length}
                </span>
                <span className="mono muted small tabular">
                  +{h.added} −{h.removed}
                </span>
                <span className="grow" />
                {open && allIds.length > 1 && (
                  <button type="button" className="hunk-toggle" aria-pressed={keep} onClick={() => toggle(h.id)}>
                    {keep ? "Keep" : "Dropped"}
                  </button>
                )}
              </div>
              {keep && (
                <pre className="diff">
                  <div className="diff-line hunk">{h.header}</div>
                  {h.lines.map((line, j) => (
                    <div
                      key={j}
                      className={`diff-line ${line.startsWith("+") ? "add" : line.startsWith("-") ? "del" : ""}`}
                    >
                      {line || " "}
                    </div>
                  ))}
                </pre>
              )}
            </div>
          );
        }),
      )}

      {run.question && run.state === "needs_you" && <p className="question">{run.question}</p>}

      {blocked && (
        <div className="blocked" role="alert">
          <p>{blocked.message}</p>
          <EvidenceCards verdict={blocked.verdict} />
          <button type="button" className="btn" onClick={() => approve(true)}>
            Apply anyway
          </button>
        </div>
      )}

      {open ? (
        <div className="actions">
          <button type="button" className="btn primary" disabled={busy || (allIds.length > 0 && kept.size === 0)} onClick={() => approve()}>
            {partial ? `Apply ${kept.size} of ${allIds.length} hunks` : run.diff ? "Approve and apply" : "Accept"}
          </button>
          <button type="button" className="btn" onClick={actions.onReject}>
            Reject
          </button>
          {run.lane !== "claude" && (
            <button type="button" className="btn escalate" onClick={actions.onEscalate}>
              Escalate to Claude
            </button>
          )}
          <span className="grow" />
          <button type="button" className="link-btn" onClick={() => setReplaying((r) => !r)}>
            {replaying ? "Hide replay" : "Replay"}
          </button>
        </div>
      ) : (
        <div className="actions">
          <p className="muted small">
            {stateLabel(run)}
            {run.applied_hunks && ` · applied ${run.applied_hunks.length} of ${allIds.length} hunks`}
          </p>
          <span className="grow" />
          <button type="button" className="link-btn" onClick={() => setReplaying((r) => !r)}>
            {replaying ? "Hide replay" : "Replay"}
          </button>
        </div>
      )}
      {replaying && <Replay runId={run.id} />}
    </article>
  );
}

const REDUCED = typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

function Replay({ runId }: { runId: string }) {
  const [events, setEvents] = useState<HarnessEvent[] | null>(null);
  const [shown, setShown] = useState(0);
  const timer = useRef<number | undefined>(undefined);

  useEffect(() => {
    api
      .events(runId)
      .then((evs) => {
        setEvents(evs);
        setShown(REDUCED ? evs.length : 0);
      })
      .catch(() => setEvents([]));
    return () => window.clearInterval(timer.current);
  }, [runId]);

  const play = () => {
    if (!events) return;
    window.clearInterval(timer.current);
    setShown(0);
    timer.current = window.setInterval(() => {
      setShown((n) => {
        if (n + 1 >= events.length) window.clearInterval(timer.current);
        return Math.min(n + 1, events.length);
      });
    }, 500);
  };

  if (events === null) return <p className="muted small">Loading replay…</p>;
  if (events.length === 0) return <p className="muted small">No events recorded for this run.</p>;
  const t0 = new Date(events[0].at).getTime();

  return (
    <section className="replay" aria-label="Run replay">
      <div className="row-between">
        <span className="label">Replay · {events.length} steps</span>
        <span className="replay-controls">
          <button type="button" className="link-btn" onClick={play}>
            Play
          </button>
          <button type="button" className="link-btn" onClick={() => setShown(events.length)}>
            Show all
          </button>
        </span>
      </div>
      <input
        type="range"
        min={0}
        max={events.length}
        value={shown}
        onChange={(e) => setShown(Number(e.target.value))}
        aria-label="Replay position"
      />
      <ol className="replay-steps">
        {events.slice(0, shown).map((e, i) => (
          <li key={i}>
            <span className="mono muted tabular">+{Math.round((new Date(e.at).getTime() - t0) / 1000)}s</span>
            <span className="dot" style={{ background: laneColor(String(e.data.lane ?? "")) }} />
            <span className="strong">{e.kind}</span>
            <span className="muted">{describe(e)}</span>
          </li>
        ))}
      </ol>
    </section>
  );
}

function describe(e: HarnessEvent): string {
  const d = e.data as Record<string, unknown>;
  if (typeof d.text === "string") return d.text;
  if (typeof d.summary === "string") return d.summary;
  if (typeof d.reason === "string") return `${d.lane} · ${d.reason}`;
  if (typeof d.certainty === "number") return `certainty ${d.certainty.toFixed(2)} → ${String(d.state)}`;
  if (typeof d.decision === "string") return d.decision.replace("_", " ");
  if (typeof d.state === "string") return d.state.replace("_", " ");
  if (typeof d.question === "string") return d.question;
  return "";
}
