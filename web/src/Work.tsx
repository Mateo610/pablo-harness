// Work (C14d): every chat's plan across projects, then the Workbench's tools (inbox, the
// project plan, compare, hunk review, replay), kept as they were.
import { App } from "./App";
import { api, type BoardRow, type TaskStatus } from "./api";
import { ago, useEvents, usePoll } from "./hud";

const STATE_WORD: Record<BoardRow["state"], string> = { running: "building", paused: "paused", draft: "draft", done: "done" };
const STEP_COLOR: Record<TaskStatus, string> = {
  done: "var(--status-pass)",
  skipped: "var(--ink-faint)",
  active: "var(--sys)",
  needs_you: "var(--accent)",
  blocked: "var(--status-fail)",
  queued: "var(--line)",
};
const STEP_WORD: Partial<Record<TaskStatus, string>> = { needs_you: "waiting for you", active: "building", blocked: "stopped", queued: "next" };

export function Work() {
  const [rows, reload] = usePoll(api.planBoard, 8000);
  useEvents((e) => {
    if (["decided", "judged", "needs_you", "finished"].includes(e.kind)) reload();
  });
  const list = rows ?? [];
  const waiting = list.filter((r) => r.current?.status === "needs_you").length;
  const building = list.filter((r) => r.state === "running").length;
  const clashes = list.filter((r) => r.overlaps.length > 0).length;
  return (
    <div className="h-work">
      <div className="h-page">
        <header className="h-head">
          <h1 className="h-title">WORK</h1>
          <span className="h-mono h-dim" style={{ fontSize: 12 }}>
            {list.length} plan{list.length === 1 ? "" : "s"} · {building} building ·{" "}
            <span style={{ color: waiting ? "var(--accent)" : undefined }}>{waiting} waiting for you</span>
            {clashes > 0 && <> · <span style={{ color: "var(--accent)" }}>{clashes} sharing files</span></>}
          </span>
        </header>
        <section aria-label="Plans" style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <span className="h-cap">Plans, from every chat</span>
          {rows === null && <span className="h-dim">Loading…</span>}
          {rows !== null && list.length === 0 && (
            <div className="h-frame h-pad">
              <span style={{ fontSize: 14 }}>No plans yet.</span>
              <span className="h-dim" style={{ fontSize: 13 }}>Turn on Plan in Chat and say what you're planning. Each chat's plan shows here while it builds, and for a day after it's done.</span>
            </div>
          )}
          {list.map((r) => (
            <PlanRow key={`${r.project}/${r.conversation}`} row={r} />
          ))}
        </section>
      </div>
      <section className="h-workbench" aria-label="Workbench">
        <div className="h-page" style={{ paddingBottom: 0 }}>
          <span className="h-cap">Workbench · the project plan, inbox, compare, hunks and replay</span>
        </div>
        <App />
      </section>
    </div>
  );
}

function PlanRow({ row: r }: { row: BoardRow }) {
  const Title = r.mine ? "a" : "span";
  return (
    <article className={`h-frame h-pad h-planrow ${r.current?.status === "needs_you" ? "amber" : ""}`} style={{ gap: 8 }}>
      <div style={{ display: "flex", gap: 10, alignItems: "baseline", flexWrap: "wrap" }}>
        <span className="h-chip" style={{ color: "var(--sys)" }}>{r.project_title}</span>
        {r.size && <span className="h-mono h-dim" style={{ fontSize: 11 }}>{r.size.toUpperCase()}</span>}
        <Title {...(r.mine ? { href: `#/chat/${r.conversation}` } : {})} style={{ fontSize: 15, fontWeight: 600, flex: 1, minWidth: 200, color: "var(--ink)" }}>
          {r.goal || "Untitled plan"}
        </Title>
        <span className="h-mono" style={{ fontSize: 11.5, color: r.state === "running" ? "var(--sys)" : "var(--ink-muted)" }}>
          {STATE_WORD[r.state]} · {r.done}/{r.total}
        </span>
      </div>
      <div className="h-steps" role="img" aria-label={`${r.done} of ${r.total} steps finished`}>
        {r.steps.map((s) => (
          <span key={s.id} title={`${s.id} · ${s.status.replace("_", " ")}`} style={{ background: STEP_COLOR[s.status] }} />
        ))}
      </div>
      <div style={{ display: "flex", gap: 14, flexWrap: "wrap", fontSize: 13 }}>
        {r.current ? (
          <span style={{ color: r.current.status === "needs_you" ? "var(--accent)" : "var(--ink-body)" }}>
            Step {r.current.id} · {r.current.title}
            <span className="h-mono h-faint" style={{ fontSize: 11 }}> · {STEP_WORD[r.current.status] ?? r.current.status}</span>
          </span>
        ) : (
          <span className="h-dim">Every step finished.</span>
        )}
        <span className="h-faint" style={{ marginLeft: "auto", fontSize: 12 }}>
          {r.mine ? "your chat" : `by ${r.by}`} · “{r.title}” · {r.updated ? ago(r.updated) : ""}
        </span>
      </div>
      {r.overlaps.map((o) => (
        <div key={o.conversation || o.files.join()} className="h-overlap">
          ⚠ Shares {o.files.length === 1 ? "a file" : `${o.files.length} files`} with{" "}
          {o.conversation ? <a href={`#/chat/${o.conversation}`}>“{o.title}”</a> : o.title}: <span className="h-mono">{o.files.join(", ")}</span>
        </div>
      ))}
    </article>
  );
}
