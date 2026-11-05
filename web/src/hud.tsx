// Shared bits for cockpit v2: routing, polling, the event bus, run cards, small formatters.
import { useEffect, useRef, useState } from "react";
import { api, ApiError, subscribe, type HarnessEvent, type Me, type Run } from "./api";
import { DiffView, laneColor, stateLabel } from "./components";

export type Route = { page: string; id: string };

export function parseHash(hash = window.location.hash): Route {
  const [page = "home", id = ""] = hash.replace(/^#\/?/, "").split("/");
  return { page: page || "home", id };
}

export function useRoute(): Route {
  const [route, setRoute] = useState(parseHash);
  useEffect(() => {
    const on = () => setRoute(parseHash());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return route;
}

export const go = (page: string, id = "") => {
  window.location.hash = id ? `#/${page}/${id}` : `#/${page}`;
};

/** Calls `load` now and every `ms`; keeps the last good value. */
export function usePoll<T>(load: () => Promise<T>, ms: number, deps: unknown[] = []): [T | null, () => void] {
  const [value, setValue] = useState<T | null>(null);
  const ref = useRef(load);
  ref.current = load;
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let alive = true;
    const run = () => ref.current().then((v) => alive && setValue(v)).catch(() => undefined);
    run();
    const id = ms > 0 ? window.setInterval(run, ms) : 0;
    return () => {
      alive = false;
      if (id) window.clearInterval(id);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ms, tick, ...deps]);
  return [value, () => setTick((t) => t + 1)];
}

// One EventSource for the whole cockpit; pages listen through useEvents.
const listeners = new Set<(e: HarnessEvent) => void>();
let close: (() => void) | null = null;

export function useEvents(on: (e: HarnessEvent) => void): void {
  const ref = useRef(on);
  ref.current = on;
  useEffect(() => {
    const fn = (e: HarnessEvent) => ref.current(e);
    listeners.add(fn);
    if (!close) close = subscribe((e) => listeners.forEach((l) => l(e)));
    return () => {
      listeners.delete(fn);
      if (listeners.size === 0 && close) {
        close();
        close = null;
      }
    };
  }, []);
}

export const gb = (mb: number) => (mb / 1024).toFixed(1);
export const pct = (used: number, total: number) => (total ? `${Math.min(100, Math.round((used / total) * 100))}%` : "0%");

export function kTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)} M`;
  if (n >= 1000) return `${Math.round(n / 1000)}k`;
  return String(n);
}

export function ago(iso: string): string {
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return "now";
  if (s < 3600) return `${Math.floor(s / 60)} min`;
  if (s < 86400) return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  if (s < 7 * 86400) return new Date(iso).toLocaleDateString([], { weekday: "short" });
  return new Date(iso).toLocaleDateString([], { month: "short", day: "numeric" });
}

export const STATE_COLOR: Record<string, string> = {
  running: "var(--sys)",
  ready: "var(--status-pass)",
  needs_you: "var(--accent)",
  approved: "var(--ink-muted)",
  rejected: "var(--ink-faint)",
  failed: "var(--status-fail)",
};

export function initials(name: string): string {
  const parts = name.replace(/@.*/, "").split(/[\s._-]+/).filter(Boolean);
  return ((parts[0]?.[0] ?? "?") + (parts[1]?.[0] ?? "")).toUpperCase();
}

/** A run inside a conversation: what it did, what the checks said, and your move. */
export function RunCard({ id, me, initial }: { id: string; me: Me | null; initial?: Run | null }) {
  const [run, setRun] = useState<Run | null>(initial ?? null);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    if (!initial) api.run(id).then(setRun).catch(() => undefined);
  }, [id, initial]);
  useEvents((e) => {
    if (e.run_id === id && ["judged", "decided", "needs_you", "finished", "error"].includes(e.kind)) {
      api.run(id).then(setRun).catch(() => undefined);
    }
  });
  if (!run) return <div className="h-frame h-pad h-dim">Loading run {id}…</div>;
  const v = run.verdict;
  const open_ = run.state === "ready" || run.state === "needs_you";
  const canReject = open_ && (me?.can_approve || (me && run.by === me.profile));
  const act = (fn: () => Promise<Run>) => {
    setBusy(true);
    setError("");
    fn()
      .then(setRun)
      .catch((e: ApiError) => setError(e.message))
      .finally(() => setBusy(false));
  };
  return (
    <div className={`h-frame h-pad h-runcard ${run.state === "needs_you" ? "amber" : ""}`}>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 10, alignItems: "center" }}>
        <span style={{ fontSize: 11, letterSpacing: ".24em", fontWeight: 700, color: STATE_COLOR[run.state] }}>
          {stateLabel(run).toUpperCase()}
        </span>
        <span className="h-mono" style={{ fontSize: 12, color: laneColor(run.lane) }}>
          {run.lane} · {run.model.replace("pablo/", "")}
        </span>
        {run.retried_from && <span className="h-mono h-faint" style={{ fontSize: 12 }}>retry of {run.retried_from}</span>}
        <span className="h-mono h-dim h-diffstat" style={{ marginLeft: "auto", fontSize: 12 }}>
          {run.certainty != null && (
            <>
              certainty <b style={{ color: "var(--ink)" }}>{run.certainty.toFixed(2)}</b> ·{" "}
            </>
          )}
          {v && (
            <>
              <span className="add">+{v.added}</span> <span className="del">−{v.removed}</span> · {v.files.length} file
              {v.files.length === 1 ? "" : "s"}
            </>
          )}
        </span>
      </div>
      {run.state === "running" && <span className="h-thinking">working…</span>}
      {v && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
          {v.applied ? (
            v.checks.map((c) => (
              <span key={c.name} className="h-chip" style={{ borderColor: c.passed ? "#1f5a3c" : c.status === "already failing" ? "var(--line)" : "#5a2626", color: c.passed ? "var(--status-pass)" : c.status === "already failing" ? "var(--ink-muted)" : "var(--status-fail)" }}>
                {c.passed ? "✓" : c.status === "already failing" ? "–" : "✗"} {c.name} · {c.status ?? (c.passed ? "passed" : "failed")}
              </span>
            ))
          ) : (
            <span className="h-chip" style={{ color: "var(--status-fail)" }}>diff did not apply</span>
          )}
        </div>
      )}
      {(run.question || run.judge_summary) && (
        <span style={{ fontSize: 13.5, color: run.state === "needs_you" ? "#f6ead6" : "var(--ink-body)", whiteSpace: "normal" }}>
          {run.question || run.judge_summary}
        </span>
      )}
      {open && run.diff && <DiffView diff={run.diff} />}
      {error && <div className="h-error">{error}</div>}
      <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
        {open_ && me?.can_approve && (
          <button className="h-btn solid" disabled={busy || !run.diff} onClick={() => act(() => api.approve(run.id))}>
            APPLY
          </button>
        )}
        {run.diff && (
          <button className="h-btn" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
            {open ? "HIDE DIFF" : "SHOW DIFF"}
          </button>
        )}
        {canReject && (
          <button className="h-btn danger" disabled={busy} onClick={() => act(() => api.reject(run.id))}>
            REJECT
          </button>
        )}
        <a className="h-btn" href={`#/work`} title="Hunk-by-hunk review, replay, and compare live in Work">
          REVIEW HUNKS
        </a>
      </div>
      {open_ && !me?.can_approve && (
        <span className="h-faint" style={{ fontSize: 12 }}>Only the owner can apply changes to the repo.</span>
      )}
    </div>
  );
}
