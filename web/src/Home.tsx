// Home: the machine, your conversations, what needs you, what Pablo remembers, the repo.
import { api, type Conversation, type Me, type Resources, type Run } from "./api";
import { laneColor } from "./components";
import { ago, gb, go, kTokens, pct, STATE_COLOR, usePoll, useEvents } from "./hud";

export function Home({ me, res }: { me: Me | null; res: Resources | null }) {
  const [convs] = usePoll(api.conversations, 15000);
  const [inbox, reloadInbox] = usePoll(api.inbox, 20000);
  const [memories] = usePoll(() => api.memories("kept"), 30000);
  const [proposed] = usePoll(() => api.memories("proposed"), 30000);
  const [stats] = usePoll(() => api.stats(7), 30000);
  const [repo] = usePoll(api.repo, 60000);
  const [jobs] = usePoll(api.queue, 30000);
  useEvents((e) => {
    if (["needs_you", "judged", "decided"].includes(e.kind)) reloadInbox();
  });

  const needs = (inbox ?? []).filter((r) => r.state === "needs_you");
  const queued = (jobs ?? []).filter((j) => j.status === "queued").length;
  const hour = new Date().getHours();
  const greet = hour < 5 ? "Late night" : hour < 12 ? "Morning" : hour < 18 ? "Afternoon" : "Evening";
  const name = me && me.name !== "Owner" ? `, ${me.name.split(" ")[0]}` : "";
  const gpu = res?.gpus[0];
  const tokens = stats
    ? Object.values(stats.lanes).reduce((n, l) => n + l.tokens_in + l.tokens_out + l.chat_tokens_in + l.chat_tokens_out, 0)
    : 0;

  const start = () => api.newConversation("auto").then((c) => go("chat", c.id));
  const open = (c: Conversation) => go(c.lane === "advisor" ? "advisor" : "chat", c.id);

  return (
    <div className="h-page">
      <header className="h-head">
        <div style={{ display: "flex", flexDirection: "column", lineHeight: 1.1 }}>
          <span style={{ fontSize: 20, fontWeight: 700, letterSpacing: ".4em" }}>PABLO</span>
          <span className="h-mono h-dim" style={{ fontSize: 10, letterSpacing: ".2em", marginTop: 4 }}>
            {me ? `${me.role} · via ${me.via}` : "connecting…"}
          </span>
        </div>
        <span className="h-mono h-dim" style={{ marginLeft: "auto", fontSize: 12 }}>
          loaded: {res?.loaded.length ? res.loaded.join(" · ") : "—"}
        </span>
      </header>

      <div style={{ display: "flex", flexWrap: "wrap", gap: 14, alignItems: "flex-end", justifyContent: "space-between" }}>
        <div>
          <h1 style={{ margin: 0, fontSize: 30, fontWeight: 600 }}>
            {greet}
            {name}.
          </h1>
          <p className="h-dim" style={{ margin: "6px 0 0", fontSize: 15 }}>
            {(convs ?? []).length} conversations ·{" "}
            <span style={{ color: needs.length ? "var(--accent)" : undefined }}>{needs.length} need you</span> · {queued} queued
            for tonight
          </p>
        </div>
        <button className="h-btn solid" style={{ minHeight: 44 }} onClick={start}>
          + NEW CONVERSATION
        </button>
      </div>

      <section className="h-vitals" aria-label="Machine">
        <Vital label="GPU" device={gpu?.name.replace("NVIDIA GeForce ", "") ?? "—"} value={gpu ? String(gpu.util_pct) : "—"} unit="%" width={gpu ? `${gpu.util_pct}%` : "0%"} color="var(--sys)" note={gpu ? `${gpu.temp_c}°C · ${Math.round(gpu.power_w)} W` : res?.error || "reading…"} />
        <Vital label="VRAM" device={gpu ? `${gb(gpu.vram_total_mb)} GB` : "—"} value={gpu ? gb(gpu.vram_used_mb) : "—"} unit={gpu ? `/ ${gb(gpu.vram_total_mb)} GB` : ""} width={gpu ? pct(gpu.vram_used_mb, gpu.vram_total_mb) : "0%"} color="var(--lane-coder)" note={res?.loaded.length ? `on the GPU: ${res.loaded.filter((l) => l !== "router").join(", ") || "nothing"}` : ""} />
        <Vital label="CPU" device={res?.cpu_threads ? `${res.cpu_threads} threads` : "—"} value={res?.cpu_pct != null ? String(Math.round(res.cpu_pct)) : "—"} unit="%" width={`${Math.round(res?.cpu_pct ?? 0)}%`} color="var(--lane-router)" note="sandbox checks pinned to their own cores" />
        <Vital label="RAM" device={res?.ram_total_mb ? `${Math.round(res.ram_total_mb / 1024)} GB` : "—"} value={res ? gb(res.ram_used_mb) : "—"} unit={res ? `/ ${gb(res.ram_total_mb)} GB` : ""} width={res ? pct(res.ram_used_mb, res.ram_total_mb) : "0%"} color="var(--lane-advisor)" note="deep needs ~38 GB when it loads" />
      </section>

      <div className="h-cols">
        <section className="h-col-main" aria-label="Conversations">
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
            <h2 className="h-cap">Conversations</h2>
            <span className="h-mono h-faint" style={{ fontSize: 11 }}>click to open</span>
          </div>
          <div className="h-frame">
            {(convs ?? []).length === 0 && (
              <p className="h-dim" style={{ padding: 16, margin: 0 }}>
                No conversations yet. Start one, ask about the code, or tell Pablo what to change.
              </p>
            )}
            {(convs ?? []).slice(0, 12).map((c) => (
              <a key={c.id} className="h-row" href={`#/${c.lane === "advisor" ? "advisor" : "chat"}/${c.id}`} onClick={(e) => (e.preventDefault(), open(c))}>
                <span className="h-dot" style={{ background: laneColor(c.lane) }} aria-hidden="true" />
                <span style={{ flex: 1, minWidth: 0, display: "flex", gap: 10, alignItems: "baseline", flexWrap: "wrap" }}>
                  <span style={{ fontSize: 15, fontWeight: 600 }}>{c.title}</span>
                  <span className="h-chip" style={{ color: "var(--sys)" }}>{c.project || "—"}</span>
                </span>
                <span className="h-mono h-faint" style={{ fontSize: 11 }}>
                  {c.lane} · {ago(c.updated_at)}
                </span>
              </a>
            ))}
          </div>
        </section>

        <aside className="h-col-side">
          {needs.length > 0 && (
            <div className="h-frame amber h-pad">
              <span style={{ fontSize: 11, fontWeight: 700, letterSpacing: ".3em", color: "var(--accent)" }}>NEEDS YOU</span>
              {needs.slice(0, 3).map((r) => (
                <NeedsRow key={r.id} run={r} />
              ))}
            </div>
          )}

          <div className="h-frame h-pad">
            <div style={{ display: "flex", justifyContent: "space-between" }}>
              <span className="h-cap">Memory</span>
              <a href="#/chat" style={{ fontSize: 12, color: "var(--sys)" }}>
                manage in Chat
              </a>
            </div>
            <span style={{ fontSize: 13, color: "var(--ink-body)" }}>
              Notes Pablo keeps for you. Nothing is kept until you keep it.
              {(proposed ?? []).length > 0 && <b style={{ color: "var(--lane-advisor)" }}> {(proposed ?? []).length} waiting for you.</b>}
            </span>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
              {(memories ?? []).slice(0, 8).map((m) => (
                <span key={m.id} className="h-chip">
                  {m.text}
                </span>
              ))}
              {(memories ?? []).length === 0 && <span className="h-faint" style={{ fontSize: 12 }}>Say "remember: …" in a chat.</span>}
            </div>
          </div>

          <div className="h-frame h-pad">
            <span className="h-cap">Last 7 days</span>
            <dl className="h-dl">
              <dt>Runs</dt>
              <dd>{stats?.runs ?? "—"}</dd>
              <dt>Approved</dt>
              <dd>{stats?.approved ?? "—"}</dd>
              <dt>Retried on deep</dt>
              <dd>{stats?.retried ?? "—"}</dd>
              <dt>Tokens (all local)</dt>
              <dd>{stats ? kTokens(tokens) : "—"}</dd>
              <dt>Claude runs</dt>
              <dd style={{ color: stats?.claude ? "var(--accent)" : undefined }}>{stats?.claude ?? "—"}</dd>
            </dl>
          </div>

          <div className="h-frame h-pad">
            <span className="h-cap">Repo</span>
            {repo?.remote ? (
              <a href={repo.remote} target="_blank" rel="noreferrer" className="h-mono" style={{ fontSize: 13, color: "var(--sys)" }}>
                {repo.remote.replace("https://", "")} ↗
              </a>
            ) : (
              <span className="h-mono" style={{ fontSize: 13 }}>{repo?.name ?? "—"}</span>
            )}
            {repo?.git && (
              <span className="h-mono h-dim h-diffstat" style={{ fontSize: 12 }}>
                {repo.branch} · {repo.commit}
                {repo.since_push && (
                  <>
                    {" "}· since push <span className="add">+{repo.since_push.added}</span>{" "}
                    <span className="del">−{repo.since_push.removed}</span>
                    {repo.ahead ? ` · ${repo.ahead} unpushed` : ""}
                  </>
                )}
              </span>
            )}
          </div>
        </aside>
      </div>
    </div>
  );
}

function Vital(props: { label: string; device: string; value: string; unit: string; width: string; color: string; note: string }) {
  return (
    <div className="h-frame h-pad" style={{ gap: 8 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <span className="h-cap" style={{ letterSpacing: ".24em" }}>{props.label}</span>
        <span className="h-mono h-dim" style={{ fontSize: 11 }}>{props.device}</span>
      </div>
      <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
        <span className="h-big">{props.value}</span>
        <span className="h-mono h-dim" style={{ fontSize: 12 }}>{props.unit}</span>
      </div>
      <div className="h-meter" aria-hidden="true">
        <div style={{ width: props.width, background: props.color }} />
      </div>
      <span className="h-dim" style={{ fontSize: 12 }}>{props.note}</span>
    </div>
  );
}

function NeedsRow({ run }: { run: Run }) {
  return (
    <a href="#/work" style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      <span style={{ color: "#f6ead6", fontSize: 14.5, lineHeight: 1.4 }}>{run.task.split("\n")[0].slice(0, 90)}</span>
      <span className="h-mono" style={{ fontSize: 12, color: STATE_COLOR[run.state] }}>
        {run.lane}
        {run.certainty != null ? ` ${run.certainty.toFixed(2)}` : ""} · {run.question.slice(0, 70)}
      </span>
    </a>
  );
}
