// Shared bits for cockpit v2: routing, polling, the event bus, run cards, small formatters.
import { useEffect, useRef, useState } from "react";
import { api, ApiError, subscribe, type GpuLease, type GpuMode, type GpuState, type HarnessEvent, type Me, type Plan, type Project, type Run } from "./api";
import { DiffView, laneColor, stateLabel, laneName } from "./components";

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

/** The project new conversations and voice calls start in. Per browser; "" is the default. */
const PROJECT_KEY = "pablo.project";
const projectListeners = new Set<(p: string) => void>();
function readProject(): string {
  try {
    return localStorage.getItem(PROJECT_KEY) ?? "";
  } catch {
    return "";
  }
}
export function useProject(): [string, (p: string) => void] {
  const [project, set] = useState(readProject);
  useEffect(() => {
    projectListeners.add(set);
    return () => void projectListeners.delete(set);
  }, []);
  const choose = (p: string) => {
    try {
      localStorage.setItem(PROJECT_KEY, p);
    } catch {
      /* private window: it just isn't remembered */
    }
    projectListeners.forEach((l) => l(p));
  };
  return [project, choose];
}

/** Which project to show: the chosen one if it still exists, else the default. */
export function pickProject(projects: Project[] | null, chosen: string): Project | null {
  if (!projects?.length) return null;
  return projects.find((p) => p.name === chosen) ?? projects.find((p) => p.default) ?? projects[0];
}

export function ProjectPicker({ projects, value, onChange }: { projects: Project[] | null; value: string; onChange: (p: string) => void }) {
  const current = pickProject(projects, value);
  if (!projects || projects.length < 2) return current ? <span className="h-chip" style={{ color: "var(--sys)" }}>{current.title}</span> : null;
  return (
    <label className="h-mono h-dim" style={{ fontSize: 12, display: "inline-flex", gap: 6, alignItems: "center" }}>
      project
      <select className="h-input" style={{ padding: "4px 8px", width: "auto" }} value={current?.name ?? ""} onChange={(e) => onChange(e.target.value)}>
        {projects.map((p) => (
          <option key={p.name} value={p.name}>
            {p.title}
            {p.mode === "unfiltered" ? " · no guardrails" : ""}
          </option>
        ))}
      </select>
    </label>
  );
}

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
          {laneName(run.lane)}
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
      {run.spec && run.spec.status !== "skipped" && (
        <span
          className="h-chip"
          title={run.spec.source}
          style={{
            alignSelf: "flex-start",
            color: run.spec.status === "failed" ? "var(--accent)" : run.spec.status === "confirmed" ? "var(--status-pass)" : "var(--ink-muted)",
            borderColor: run.spec.status === "failed" ? "#5a3d12" : run.spec.status === "confirmed" ? "#1f5a3c" : "var(--line)",
          }}
        >
          from your request · {run.spec.status === "failed" ? `fails: ${run.spec.changed?.asserts[0] ?? run.spec.reason}` : run.spec.status}
        </span>
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

const MODE_NOTE: Record<GpuMode, string> = {
  interactive: "Coder stays hot. Deep retries wait for tonight.",
  balanced: "Retries and night work start after a quiet couple of minutes.",
  deep: "Deep can take the GPU any time. Voice is paused.",
};

/** The owner's GPU mode switch; teammates see it read-only. */
export function ModeSwitch({ me, gpu, onChange }: { me: Me | null; gpu: GpuState | null; onChange: () => void }) {
  const [busy, setBusy] = useState(false);
  const mode = gpu?.mode ?? "balanced";
  const owner = Boolean(me?.can_approve);
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
      <div className="h-seg" role="group" aria-label="GPU mode">
        {(["interactive", "balanced", "deep"] as GpuMode[]).map((m) => (
          <button
            key={m}
            aria-pressed={mode === m}
            disabled={!owner || busy}
            title={owner ? MODE_NOTE[m] : "Only the owner changes the GPU mode"}
            onClick={() => {
              setBusy(true);
              api.setGpuMode(m).then(onChange).finally(() => setBusy(false));
            }}
          >
            {m === "deep" ? "DEEP WORK" : m.toUpperCase()}
          </button>
        ))}
      </div>
      <span className="h-dim" style={{ fontSize: 12, maxWidth: 360 }}>{MODE_NOTE[mode]}</span>
    </div>
  );
}

const PRIORITY_COLOR: Record<string, string> = {
  voice: "var(--accent)",
  chat: "var(--sys)",
  run: "var(--lane-coder)",
  retry: "var(--lane-deep)",
  night: "var(--ink-muted)",
};

/** Who has the GPU and who's waiting, most urgent first. */
export function GpuQueue({ gpu }: { gpu: GpuState | null }) {
  if (!gpu) return null;
  const row = (l: GpuLease, i: number, held: boolean) => (
    <div key={`${held}-${i}`} className="h-mono" style={{ display: "flex", gap: 10, fontSize: 12, alignItems: "baseline" }}>
      <span style={{ color: held ? "var(--status-pass)" : "var(--ink-faint)", width: 54 }}>{held ? "● has it" : "waiting"}</span>
      <span style={{ color: PRIORITY_COLOR[l.priority], width: 44 }}>{l.priority}</span>
      <span style={{ flex: 1 }}>{l.model.replace("pablo/", "")}</span>
      <span className="h-dim">{l.who ? (l.who === "owner" ? "you" : l.who.replace(/@.*/, "")) : "pablo"}</span>
      <span className="h-faint">{Math.round(l.for_s)}s</span>
    </div>
  );
  return (
    <section className="h-frame h-pad" aria-label="GPU queue">
      <h2 className="h-cap">GPU · {gpu.mode === "deep" ? "deep work" : gpu.mode}</h2>
      {gpu.holders.length === 0 && gpu.waiting.length === 0 && !gpu.elsewhere?.length && (
        <span className="h-dim" style={{ fontSize: 12 }}>
          Free{gpu.resident ? ` · ${gpu.resident.replace("pablo/", "")} still loaded` : ""}.
        </span>
      )}
      {gpu.holders.map((l, i) => row(l, i, true))}
      {gpu.waiting.map((l, i) => row(l, i, false))}
      {(gpu.elsewhere ?? []).map((e, i) => (
        <div key={`x-${i}`} className="h-mono" style={{ display: "flex", gap: 10, fontSize: 12, alignItems: "baseline" }}>
          <span style={{ color: e.state === "holding" ? "var(--status-pass)" : "var(--ink-faint)", width: 54 }}>{e.state === "holding" ? "● has it" : "waiting"}</span>
          <span style={{ color: PRIORITY_COLOR[e.priority] ?? "var(--ink-muted)", width: 44 }}>{e.priority}</span>
          <span style={{ flex: 1 }}>{e.model.replace("pablo/", "")}</span>
          <span className="h-dim">another process{e.who ? ` · ${e.who}` : ""}</span>
        </div>
      ))}
      <span className="h-faint" style={{ fontSize: 11.5 }}>
        Background work {gpu.background_ok ? "may start now" : gpu.mode === "interactive" ? "waits for tonight" : `waits for ${Math.round(gpu.idle_needed_s)} s of quiet`}.
      </span>
    </section>
  );
}

const STEP_MARK: Record<string, string> = { done: "✓", active: "…", needs_you: "◆", blocked: "!", queued: "·" };
const STEP_WORD: Record<string, string> = { done: "done", active: "building", needs_you: "waiting for you", blocked: "stopped", queued: "next" };

/** Plan mode: the project's plan, the step waiting for you, and Build. Polls while it builds. */
export function PlanCard({ project, me, onChange }: { project: string; me: Me | null; onChange?: () => void }) {
  const [plan, reload] = usePoll<Plan | null>(() => api.plan(project).catch(() => null), 4000, [project]);
  const [error, setError] = useState("");
  useEvents((e) => {
    if (["decided", "judged", "needs_you"].includes(e.kind)) reload();
  });
  if (!plan || !plan.state) return null;
  const steps = plan.phases.flatMap((p) => p.tasks);
  const waiting = steps.find((t) => t.status === "needs_you" && t.run);
  const canBuild = plan.state === "draft" || plan.state === "paused";
  const build = () =>
    api
      .planGo(project)
      .then(() => (setError(""), reload(), onChange?.()))
      .catch((e: Error) => setError(e.message));
  return (
    <section className="h-frame h-pad" aria-label="Plan" style={{ gap: 10 }}>
      <div style={{ display: "flex", gap: 10, alignItems: "baseline", flexWrap: "wrap" }}>
        <span className="h-cap" style={{ color: "var(--sys)" }}>Plan</span>
        <span style={{ fontSize: 14.5, fontWeight: 600, flex: 1, minWidth: 0 }}>{plan.goal || "Untitled"}</span>
        <span className="h-mono h-dim" style={{ fontSize: 11 }}>
          {plan.state} · {steps.filter((t) => t.status === "done").length}/{steps.length}
        </span>
      </div>
      <ol style={{ margin: 0, padding: 0, listStyle: "none", display: "flex", flexDirection: "column", gap: 6 }}>
        {steps.map((t) => (
          <li key={t.id} style={{ display: "flex", gap: 10, alignItems: "baseline", fontSize: 13.5 }}>
            <span className="h-mono" style={{ width: 16, color: t.status === "needs_you" ? "var(--accent)" : t.status === "done" ? "var(--status-pass)" : t.status === "blocked" ? "var(--status-fail)" : "var(--ink-muted)" }} aria-hidden="true">
              {STEP_MARK[t.status] ?? "·"}
            </span>
            <span style={{ flex: 1, minWidth: 0, color: t.status === "done" ? "var(--ink-muted)" : undefined }}>
              {t.id}. {t.title}
            </span>
            <span className="h-mono h-faint" style={{ fontSize: 11 }}>{STEP_WORD[t.status] ?? t.status}</span>
          </li>
        ))}
      </ol>
      {waiting?.run && <RunCard id={waiting.run} me={me} />}
      {error && <span style={{ color: "var(--status-fail)", fontSize: 13 }}>{error}</span>}
      {canBuild && (
        <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
          <button className="h-btn solid" onClick={build}>
            {plan.state === "draft" ? "BUILD IT" : "KEEP GOING"}
          </button>
          <span className="h-dim" style={{ fontSize: 12 }}>
            {plan.state === "draft" ? "Each step waits for your approval before the next starts." : "Retries the stopped step, then carries on."}
          </span>
        </div>
      )}
    </section>
  );
}
