// Voice: one orb, pick who you talk to. The voice lane (local speech-to-text and speech) is M9b;
// until then this page shows the shape, and the orb animates so you can see it move.
import { useState } from "react";

const WHO = {
  orchestrator: { label: "ORCHESTRATOR", model: "coder · Qwen3.6-35B", color: "#4fe0c0", deep: "#0b5a4c", glow: "#4fe0c08c", blurb: "Breaks what you say into tasks and hands them to lanes." },
  advisor: { label: "ADVISOR", model: "Mistral 3.2 24B heretic", color: "#b9a3ff", deep: "#3a2a8a", glow: "#b9a3ff8c", blurb: "No guardrails, no code, no handoffs. Talks it through and pushes back." },
  deep: { label: "DEEP", model: "gpt-oss-120b", color: "#ff8fb1", deep: "#7a2a48", glow: "#ff8fb180", blurb: "For the hard plan. Slow replies; it speaks a short summary first." },
} as const;
type Who = keyof typeof WHO;

const BARS = Array.from({ length: 64 }, (_, i) => {
  const w = Math.abs(Math.sin(i * 1.7 + 0.4));
  return { r: (360 / 64) * i, d: `${(0.55 + w * 0.7).toFixed(2)}s`, delay: `${(-((i * 0.137) % 1.2)).toFixed(2)}s` };
});

export function Voice() {
  const [who, setWho] = useState<Who>("orchestrator");
  const [live, setLive] = useState(false);
  const cur = WHO[who];
  return (
    <div className="h-page" style={{ minHeight: "100vh" }}>
      <header className="h-head">
        <h1 className="h-title">VOICE</h1>
        <span className="h-chip" style={{ marginLeft: "auto", color: "var(--accent)", borderColor: "#5a3d12" }}>
          voice lane not set up yet · M9b
        </span>
      </header>
      <div style={{ flex: 1, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 22, padding: "20px 0" }}>
        <div className="h-seg" role="radiogroup" aria-label="Who you're talking to">
          {(Object.keys(WHO) as Who[]).map((k) => (
            <button key={k} role="radio" aria-checked={who === k} aria-pressed={who === k} onClick={() => setWho(k)} style={who === k ? { background: WHO[k].color, color: "#03070a" } : { color: WHO[k].color }}>
              {WHO[k].label}
            </button>
          ))}
        </div>
        <button className={`h-orb ${live ? "live" : ""}`} onClick={() => setLive((l) => !l)} aria-pressed={live} aria-label={`${cur.label}: ${live ? "speaking" : "listening"}. Tap to preview.`}>
          <span className="h-aura" style={{ background: `radial-gradient(circle, ${cur.glow} 0%, transparent 65%)` }} />
          {BARS.map((b) => (
            <span key={b.r} className="h-spoke" style={{ transform: `rotate(${b.r}deg) translateY(-112px)` }}>
              <i style={{ background: cur.color, ["--d" as string]: b.d, ["--delay" as string]: b.delay }} />
            </span>
          ))}
          <span className="h-core" style={{ background: `radial-gradient(circle at 38% 32%, #ffffff 0%, ${cur.color} 34%, ${cur.deep} 78%, #020406 100%)`, boxShadow: `0 0 50px ${cur.glow}, inset 0 0 30px #00000080` }} />
        </button>
        <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 6, textAlign: "center", maxWidth: 480 }}>
          <span style={{ fontSize: 15, letterSpacing: ".34em", fontWeight: 700, color: cur.color }}>{cur.label}</span>
          <span className="h-mono h-dim" style={{ fontSize: 12 }}>{cur.model}</span>
          <span className="h-dim" style={{ fontSize: 13, lineHeight: 1.5 }}>{cur.blurb}</span>
        </div>
        <div className="h-frame h-pad" style={{ maxWidth: 560, textAlign: "left" }}>
          <span className="h-cap">What's missing</span>
          <span style={{ fontSize: 13.5, color: "var(--ink-body)", lineHeight: 1.6 }}>
            Speech-to-text and a voice, both running on Pablo's CPU so audio never leaves the box. When they land, holding
            to talk sends your words to the model you picked, in a conversation you can open in Chat. Until then, the
            orb is a preview: tap it. Type in <a href="#/chat" style={{ color: "var(--sys)" }}>Chat</a> or the{" "}
            <a href="#/advisor" style={{ color: "var(--lane-advisor)" }}>Advisor</a>.
          </span>
        </div>
      </div>
    </div>
  );
}
