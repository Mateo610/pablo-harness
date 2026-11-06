// Pablo design-system components: LaneBadge, CertaintyGauge, EvidenceCards, LaneRibbon, DiffView.
import type { Brief, Check, Job, Lane, Resources, Run, Verdict } from "./api";

const LANE_VAR: Record<string, string> = {
  router: "var(--lane-router)",
  coder: "var(--lane-coder)",
  judge: "var(--lane-judge)",
  advisor: "var(--lane-advisor)",
  auto: "var(--sys)",
  deep: "var(--lane-deep)",
  media: "var(--lane-media)",
  claude: "var(--lane-escalate)",
};

export const laneColor = (lane: string) => LANE_VAR[lane] ?? "var(--ink-muted)";

// People see the model's own name ("Qwen3.6 35B-A3B"), not the lane ("coder"). The shell fills
// this from /api/lanes once; until then the lane name stands in.
const NAMES: Record<string, string> = { auto: "Hybrid" };
export function setLaneNames(lanes: Lane[]) {
  for (const l of lanes) if (l.display) NAMES[l.name] = l.display;
}
export const laneName = (lane: string) => NAMES[lane] ?? lane.charAt(0).toUpperCase() + lane.slice(1);

export function LaneBadge({ lane, model }: { lane: string; model?: string }) {
  const escalate = lane === "claude";
  return (
    <span className="lane-badge">
      <span
        className="lane-avatar"
        style={{
          color: laneColor(lane),
          boxShadow: escalate ? undefined : `inset 0 0 0 1px ${laneColor(lane)}`,
          border: escalate ? `1px dashed ${laneColor(lane)}` : undefined,
        }}
        aria-hidden="true"
      >
        {lane.charAt(0).toUpperCase()}
      </span>
      <span className="lane-name">{laneName(lane)}</span>
      {model && <span className="mono muted">{model}</span>}
    </span>
  );
}

/** 270° arc. At or above the threshold it draws in the judge color; below, amber: needs you. */
export function CertaintyGauge({
  value,
  threshold = 0.75,
  settled = false,
}: {
  value: number;
  threshold?: number;
  /** After you decide, the gauge stops asking for attention. */
  settled?: boolean;
}) {
  const high = value >= threshold || settled;
  const color = settled ? "var(--ink-faint)" : high ? "var(--lane-judge)" : "var(--accent)";
  const arc = "M7.6 20.4A9 9 0 1 1 20.4 20.4";
  return (
    <span className="gauge" style={{ color: settled ? "var(--ink-muted)" : high ? "var(--ink)" : "var(--accent)" }}>
      <svg width="20" height="20" viewBox="0 0 28 28" role="img" aria-label={`Judge certainty ${value.toFixed(2)}`}>
        <path d={arc} fill="none" stroke="var(--surface-300)" strokeWidth="3" strokeLinecap="round" />
        <path
          d={arc}
          fill="none"
          stroke={color}
          strokeWidth="3"
          strokeLinecap="round"
          pathLength={100}
          strokeDasharray={`${Math.round(value * 100)} 100`}
        />
      </svg>
      <span className="mono tabular">
        {value.toFixed(2)}
        {!high && " · needs you"}
      </span>
    </span>
  );
}

function SandboxCard({ verdict }: { verdict: Verdict }) {
  const notes = verdict.notes ?? [];
  if (!verdict.sandbox && notes.length === 0) return null;
  return (
    <div className="evidence-card">
      <span className="label">Sandbox</span>
      {verdict.sandbox && <span className="mono small">{verdict.sandbox}</span>}
      {notes.map((note) => (
        <span className="muted small" key={note}>
          {note}
        </span>
      ))}
    </div>
  );
}

function checkLabel(c: Check): string {
  if (c.passed) return "Passed";
  switch (c.status) {
    case "already failing":
      return "Failed before this diff too";
    case "still failing":
      return "Failed, and may be worse";
    case "regressed":
      return "Broken by this diff";
    default:
      return "Failed";
  }
}

export function EvidenceCards({ verdict }: { verdict: Verdict }) {
  if (!verdict.applied) {
    return (
      <div className="evidence">
        <div className="evidence-card">
          <span className="label">Diff</span>
          <span className="result fail">Did not apply</span>
          <span className="mono muted small">{verdict.error}</span>
        </div>
        <SandboxCard verdict={verdict} />
      </div>
    );
  }
  return (
    <div className="evidence">
      {verdict.checks.map((c) => (
        <div className="evidence-card" key={c.name}>
          <span className="label">{c.name}</span>
          <span className={`result ${c.passed ? "pass" : c.status === "already failing" ? "" : "fail"}`}>
            {checkLabel(c)}
          </span>
          <span className="mono muted small tabular">
            exit {c.exit_code} · {c.duration_s}s
            {c.before ? ` · before: ${c.before}${c.before_failures != null ? ` (${c.before_failures})` : ""}` : ""}
          </span>
        </div>
      ))}
      <div className="evidence-card">
        <span className="label">Diff</span>
        <span className="result">
          +{verdict.added} −{verdict.removed} · {verdict.files.length} file{verdict.files.length === 1 ? "" : "s"}
        </span>
        <span className="mono muted small">{verdict.files.slice(0, 2).join(", ")}</span>
      </div>
      <SandboxCard verdict={verdict} />
    </div>
  );
}

export function DiffView({ diff }: { diff: string }) {
  return (
    <pre className="diff" aria-label="Proposed diff">
      {diff.split("\n").map((line, i) => {
        const kind = line.startsWith("+") && !line.startsWith("+++")
          ? "add"
          : line.startsWith("-") && !line.startsWith("---")
            ? "del"
            : line.startsWith("@@")
              ? "hunk"
              : "";
        return (
          <div key={i} className={`diff-line ${kind}`}>
            {line || " "}
          </div>
        );
      })}
    </pre>
  );
}

/** The signature: one band per lane, wide when busy, graphite when idle. */
export function LaneRibbon({ lanes, busy }: { lanes: Lane[]; busy: Record<string, number> }) {
  return (
    <div className="ribbon" role="img" aria-label="Lane activity">
      {lanes
        .filter((l) => l.name !== "claude")
        .map((l) => {
          const weight = busy[l.name] ?? 0;
          return (
            <span
              key={l.name}
              style={{
                flexGrow: weight ? 1 + weight * 2 : 1,
                background: weight ? laneColor(l.name) : "var(--surface-300)",
              }}
            />
          );
        })}
    </div>
  );
}

export function stateLabel(run: Run): string {
  return {
    running: "Working",
    ready: "Ready for you",
    needs_you: "Needs you",
    approved: "Approved",
    rejected: "Rejected",
    failed: "Failed",
  }[run.state];
}

/** "Tonight": what the slow lanes will work on while you sleep. */
export function TonightList({ jobs, onCancel }: { jobs: Job[]; onCancel: (id: string) => void }) {
  const visible = jobs.filter((j) => j.status === "queued" || j.status === "running" || j.status === "failed");
  if (visible.length === 0) {
    return <p className="muted small">Nothing queued. Use Tonight in the composer to queue slow work.</p>;
  }
  return (
    <ul className="tonight">
      {visible.map((j) => (
        <li key={j.id} className="tonight-item">
          <span className="tonight-bar" style={{ background: laneColor(j.lane ?? "deep") }} />
          <span className="tonight-text">
            <span>{j.task}</span>
            <span className="muted small">
              {j.lane ?? "deep"} ·{" "}
              {j.status === "running" ? "running now" : j.status === "failed" ? `failed: ${j.error}` : "queued"}
            </span>
          </span>
          {j.status === "queued" && (
            <button type="button" className="icon-btn" aria-label={`Remove "${j.task}" from tonight`} onClick={() => onCancel(j.id)}>
              ×
            </button>
          )}
        </li>
      ))}
    </ul>
  );
}

export function BriefCard({ brief, onDismiss }: { brief: Brief; onDismiss: () => void }) {
  return (
    <section className="brief" aria-label="Morning brief">
      <div className="row-between">
        <span className="label">Morning brief · {brief.day}</span>
        <button type="button" className="link-btn" onClick={onDismiss}>
          Dismiss
        </button>
      </div>
      <h2 className="brief-headline">{brief.headline}</h2>
      {brief.plan_line && <p className="muted brief-plan">{brief.plan_line}</p>}
      {brief.items.length > 0 && (
        <ul className="brief-items">
          {brief.items.map((item) => (
            <li key={item.run_id}>
              <a href={`#run-${item.run_id}`} className="brief-item">
                <LaneBadge lane={item.lane} />
                <span className="grow">{item.task}</span>
                <span className={`small ${item.state === "needs_you" ? "accent" : "muted"}`}>
                  {item.state === "needs_you" ? "needs you" : item.state}
                </span>
              </a>
            </li>
          ))}
        </ul>
      )}
      {brief.failed_jobs.map((f, i) => (
        <p key={i} className="small fail-text">
          {f.task}: {f.error}
        </p>
      ))}
      {brief.still_queued > 0 && <p className="muted small">{brief.still_queued} still queued for tonight.</p>}
    </section>
  );
}

export function todayLocal(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

const gb = (mb: number) => (mb / 1024).toFixed(1);

function Meter({ label, used, total, unit, warnAt = 0.9 }: { label: string; used: number; total: number; unit: string; warnAt?: number }) {
  const frac = total > 0 ? Math.min(1, used / total) : 0;
  return (
    <div className="meter">
      <div className="row-between small">
        <span className="muted">{label}</span>
        <span className="mono tabular">
          {used} / {total} {unit}
        </span>
      </div>
      <div className="meter-track" role="meter" aria-label={label} aria-valuemin={0} aria-valuemax={total} aria-valuenow={used}>
        <span className={`meter-fill ${frac >= warnAt ? "hot" : ""}`} style={{ width: `${frac * 100}%` }} />
      </div>
    </div>
  );
}

/** Pablo's hardware, live. Polled every few seconds by the app. */
export function ResourcesPanel({ res }: { res: Resources | null }) {
  if (!res) return <p className="muted small">Reading Pablo…</p>;
  const gpu = res.gpus[0];
  return (
    <div className="resources">
      {gpu ? (
        <>
          <Meter label={gpu.name.replace("NVIDIA GeForce ", "")} used={Number(gb(gpu.vram_used_mb))} total={Number(gb(gpu.vram_total_mb))} unit="GB" />
          <div className="row-between small">
            <span className="muted">GPU load</span>
            <span className="mono tabular">
              {gpu.util_pct}% · {gpu.temp_c}°C · {Math.round(gpu.power_w)} W
            </span>
          </div>
        </>
      ) : (
        <p className="muted small">No GPU reading{res.error ? `: ${res.error}` : ""}</p>
      )}
      {res.ram_total_mb > 0 && (
        <Meter label="RAM" used={Number(gb(res.ram_used_mb))} total={Number(gb(res.ram_total_mb))} unit="GB" />
      )}
      <div className="row-between small">
        <span className="muted">Loaded</span>
        <span className="mono">{res.loaded.length ? res.loaded.join(", ") : "not tracked until M4"}</span>
      </div>
    </div>
  );
}

export function resourcesLine(res: Resources | null): string {
  if (!res) return "";
  const gpu = res.gpus[0];
  const parts = [];
  if (gpu) parts.push(`VRAM ${gb(gpu.vram_used_mb)}/${gb(gpu.vram_total_mb)}`);
  if (res.ram_total_mb) parts.push(`RAM ${Math.round(res.ram_used_mb / 1024)}/${Math.round(res.ram_total_mb / 1024)}`);
  return parts.join(" · ");
}
