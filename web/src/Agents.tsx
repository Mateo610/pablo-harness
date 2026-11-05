// Agents: every lane, what it's doing, what it spent, and the runs behind it.
import { useState } from "react";
import { api, type Lane, type LaneStat, type Resources } from "./api";
import { laneColor } from "./components";
import { ago, kTokens, STATE_COLOR, useEvents, usePoll } from "./hud";

const EMPTY: LaneStat = { runs: 0, approved: 0, ready: 0, needs_you: 0, failed: 0, tokens_in: 0, tokens_out: 0, chat_tokens_in: 0, chat_tokens_out: 0, latency_s: 0, calls: 0 };

export function Agents({ lanes, res }: { lanes: Lane[]; res: Resources | null }) {
  const [days, setDays] = useState(1);
  const [stats, reloadStats] = usePoll(() => api.stats(days), 15000, [days]);
  const [runs, reloadRuns] = usePoll(() => api.runs(30), 15000);
  const [repo] = usePoll(api.repo, 30000);
  const [doing, setDoing] = useState<Record<string, string>>({});

  useEvents((e) => {
    const lane = typeof e.data.lane === "string" ? e.data.lane : "";
    if (e.kind === "status" && lane) setDoing((d) => ({ ...d, [lane]: String(e.data.text ?? "working") }));
    if (["judged", "decided", "needs_you", "finished", "error"].includes(e.kind)) {
      if (lane) setDoing((d) => ({ ...d, [lane]: "" }));
      reloadRuns();
      reloadStats();
    }
  });

  const shown = lanes.filter((l) => l.name !== "media");
  const total = (s: LaneStat) => s.tokens_in + s.tokens_out + s.chat_tokens_in + s.chat_tokens_out;
  const spend = shown.map((l) => ({ lane: l.name, n: total(stats?.lanes[l.name] ?? EMPTY) }));
  const top = Math.max(1, ...spend.map((s) => s.n));
  const all = spend.reduce((n, s) => n + s.n, 0);

  return (
    <div className="h-page">
      <header className="h-head">
        <h1 className="h-title">AGENTS</h1>
        <div className="h-seg" role="group" aria-label="Period">
          {[1, 7, 30].map((d) => (
            <button key={d} aria-pressed={days === d} onClick={() => setDays(d)}>
              {d === 1 ? "today" : `${d} days`}
            </button>
          ))}
        </div>
        <span className="h-mono h-dim" style={{ marginLeft: "auto", fontSize: 12 }}>
          loaded: {res?.loaded.length ? res.loaded.join(" · ") : "—"}
        </span>
      </header>

      <section style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(170px, 1fr))", gap: 12 }} aria-label="Totals">
        <Total k="RUNS" v={String(stats?.runs ?? "—")} note="started here, in chat, and overnight" />
        <Total k="TOKENS" v={stats ? kTokens(all) : "—"} note="runs + chat, all local" color="var(--sys)" />
        <Total k="APPROVED" v={String(stats?.approved ?? "—")} note="applied to the repo" color="var(--status-pass)" />
        <Total k="RETRIED ON DEEP" v={String(stats?.retried ?? "—")} note="broken coder runs only" color="var(--lane-deep)" />
        <Total k="CLAUDE RUNS" v={String(stats?.claude ?? "—")} note="escalations; each asked first" color="var(--accent)" />
      </section>

      <section className="h-lanes" aria-label="Lanes">
        {shown.map((l) => {
          const s = stats?.lanes[l.name] ?? EMPTY;
          const loaded = res?.loaded.includes(l.name) ?? false;
          const busy = doing[l.name];
          const state = busy ? "WORKING" : loaded ? "LOADED" : l.kind === "remote" ? "REMOTE" : "UNLOADED";
          return (
            <article key={l.name} className="h-frame h-pad" style={{ borderTop: `2px solid ${laneColor(l.name)}` }}>
              <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                <span className="h-dot" style={{ width: 9, height: 9, background: loaded || busy ? laneColor(l.name) : "#2a3a44", boxShadow: loaded || busy ? `0 0 10px ${laneColor(l.name)}` : undefined }} aria-hidden="true" />
                <h2 style={{ margin: 0, fontSize: 15, letterSpacing: ".2em", color: laneColor(l.name) }}>{l.name.toUpperCase()}</h2>
                <span className="h-chip" style={{ marginLeft: "auto", color: busy ? "var(--sys)" : "var(--ink-muted)" }}>{state}</span>
              </div>
              <span className="h-mono h-dim" style={{ fontSize: 12 }}>
                {l.model} · {l.role}
              </span>
              <div style={{ position: "relative", minHeight: 36, padding: "8px 10px", background: "#030a0f", border: "1px solid #0d2630" }}>
                {busy && <span className="h-scan" aria-hidden="true" />}
                <span style={{ position: "relative", fontSize: 13 }}>{busy || (s.runs ? `${s.runs} run${s.runs === 1 ? "" : "s"} in this period` : "idle")}</span>
              </div>
              <dl className="h-stats3">
                <Stat k="TOKENS IN" v={kTokens(s.tokens_in + s.chat_tokens_in)} />
                <Stat k="TOKENS OUT" v={kTokens(s.tokens_out + s.chat_tokens_out)} />
                <Stat k="AVG CALL" v={s.calls ? `${s.latency_s}s` : "—"} />
                <Stat k="READY" v={String(s.ready)} />
                <Stat k="NEEDS YOU" v={String(s.needs_you)} />
                <Stat k="APPROVED" v={String(s.approved)} />
              </dl>
              <span className="h-mono h-faint" style={{ fontSize: 11 }}>
                {l.writes_code ? "writes code" : "talks only"} · {l.routable === false ? "not routed" : "routed"} · {l.train_ok ? "trains" : "never trained on"}
              </span>
            </article>
          );
        })}
      </section>

      <div className="h-cols">
        <section className="h-frame h-col-main" style={{ padding: "12px 6px 6px", overflowX: "auto" }} aria-label="Recent runs">
          <div style={{ padding: "0 10px 6px" }}>
            <h2 className="h-cap">Recent runs</h2>
          </div>
          <table className="h-table">
            <thead>
              <tr>
                <th scope="col">RUN</th>
                <th scope="col">TASK</th>
                <th scope="col">BY</th>
                <th scope="col">LANE</th>
                <th scope="col">DIFF</th>
                <th scope="col">CHECKS</th>
                <th scope="col">STATE</th>
                <th scope="col">WHEN</th>
              </tr>
            </thead>
            <tbody>
              {(runs ?? []).map((r) => {
                const v = r.verdict;
                const bad = v ? v.checks.filter((c) => !c.passed && c.status !== "already failing").length : 0;
                return (
                  <tr key={r.id}>
                    <td className="h-faint">{r.id.slice(0, 6)}</td>
                    <td className="task" title={r.task}>
                      {r.conversation_id ? (
                        <a href={`#/chat/${r.conversation_id}`} style={{ color: "var(--ink)", textDecoration: "underline", textDecorationColor: "var(--sys-line)" }}>
                          {r.task.split("\n")[0]}
                        </a>
                      ) : (
                        r.task.split("\n")[0]
                      )}
                    </td>
                    <td>{r.by ? (r.by === "owner" ? "you" : r.by.replace(/@.*/, "")) : "cli"}</td>
                    <td style={{ color: laneColor(r.lane) }}>{r.lane}</td>
                    <td className="h-diffstat">{v ? <><span className="add">+{v.added}</span> <span className="del">−{v.removed}</span></> : "—"}</td>
                    <td style={{ color: !v ? "var(--ink-faint)" : !v.applied || bad ? "var(--status-fail)" : "var(--status-pass)" }}>
                      {!v ? "—" : !v.applied ? "didn't apply" : bad ? `${bad} worse` : `${v.checks.length}/${v.checks.length} ✓`}
                    </td>
                    <td style={{ color: STATE_COLOR[r.state] }}>
                      {r.state.replace("_", " ")}
                      {r.certainty != null ? ` ${r.certainty.toFixed(2)}` : ""}
                    </td>
                    <td className="h-faint">{ago(r.created_at)}</td>
                  </tr>
                );
              })}
              {(runs ?? []).length === 0 && (
                <tr>
                  <td colSpan={8} className="h-dim">No runs yet.</td>
                </tr>
              )}
            </tbody>
          </table>
        </section>

        <aside className="h-col-side">
          <section className="h-frame h-pad" aria-label="Token spend by lane">
            <h2 className="h-cap">Token spend · {days === 1 ? "today" : `${days} days`}</h2>
            {spend.map((s) => (
              <div key={s.lane} style={{ display: "grid", gridTemplateColumns: "74px 1fr 58px", gap: 10, alignItems: "center" }}>
                <span className="h-mono" style={{ fontSize: 12, color: laneColor(s.lane) }}>{s.lane}</span>
                <div className="h-meter" aria-hidden="true">
                  <div style={{ width: `${Math.round((s.n / top) * 100)}%`, background: laneColor(s.lane) }} />
                </div>
                <span className="h-mono" style={{ fontSize: 12, textAlign: "right" }}>{kTokens(s.n)}</span>
              </div>
            ))}
            <span className="h-dim" style={{ fontSize: 12 }}>Local tokens cost power, not money.</span>
          </section>

          <section className="h-frame h-pad" aria-label="Changes since last push">
            <h2 className="h-cap">Since last push</h2>
            {repo?.git && repo.since_push ? (
              <>
                <span className="h-mono h-dim" style={{ fontSize: 12 }}>
                  {repo.name} · {repo.upstream || "no upstream"} · {repo.commit}
                </span>
                <span className="h-mono h-diffstat" style={{ fontSize: 22 }}>
                  <span className="add">+{repo.since_push.added}</span> <span className="del">−{repo.since_push.removed}</span>{" "}
                  <span className="h-dim" style={{ fontSize: 13 }}>
                    · {repo.since_push.files} files{repo.ahead ? ` · ${repo.ahead} commits unpushed` : ""}
                  </span>
                </span>
                {repo.since_push.changed.map((c) => (
                  <div key={c.path} className="h-mono h-diffstat" style={{ display: "flex", justifyContent: "space-between", fontSize: 12, gap: 8 }}>
                    <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{c.path}</span>
                    <span>
                      <span className="add">+{c.added}</span> <span className="del">−{c.removed}</span>
                    </span>
                  </div>
                ))}
              </>
            ) : (
              <span className="h-dim" style={{ fontSize: 12 }}>{repo ? "Not a git repo." : "Reading…"}</span>
            )}
          </section>
        </aside>
      </div>
    </div>
  );
}

function Total({ k, v, note, color }: { k: string; v: string; note: string; color?: string }) {
  return (
    <div className="h-frame h-pad" style={{ gap: 4 }}>
      <span className="h-cap" style={{ letterSpacing: ".22em" }}>{k}</span>
      <span className="h-mono" style={{ fontSize: 24, color: color ?? "var(--ink)" }}>{v}</span>
      <span className="h-dim" style={{ fontSize: 12 }}>{note}</span>
    </div>
  );
}

function Stat({ k, v }: { k: string; v: string }) {
  return (
    <div>
      <dt>{k}</dt>
      <dd>{v}</dd>
    </div>
  );
}
