// Voice: one orb, pick who you're talking to, talk. Speech is turned into text and back on
// Pablo; every turn lands in a conversation, so handoffs are ordinary runs you approve.
import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, type Me, type VoiceEvent } from "./api";
import { audioContext, Player, record, releaseMicrophone, rms, type Recorder } from "./audio";
import { ago, RunCard, usePoll } from "./hud";
import { Markdown } from "./Markdown";

const WHO = {
  coder: { label: "ORCHESTRATOR", model: "coder · Qwen3.6-35B", color: "#4fe0c0", deep: "#0b5a4c", glow: "#4fe0c08c", blurb: "Breaks what you say into tasks and hands them to lanes." },
  advisor: { label: "ADVISOR", model: "Mistral 3.2 24B heretic", color: "#b9a3ff", deep: "#3a2a8a", glow: "#b9a3ff8c", blurb: "No guardrails, no code, no handoffs. Talks it through and pushes back." },
  deep: { label: "DEEP", model: "gpt-oss-120b", color: "#ff8fb1", deep: "#7a2a48", glow: "#ff8fb180", blurb: "For the hard plan. Slow to answer: expect a pause." },
} as const;
type Who = keyof typeof WHO;
type Phase = "idle" | "listening" | "thinking" | "speaking";

const SPOKES = 64;
const SILENCE_MS = 900;
const SPEECH_LEVEL = 0.035;

interface Line {
  role: "user" | "assistant";
  text: string;
  lane: string;
  runId?: string | null;
  at: string;
}

export function Voice({ me }: { me: Me | null }) {
  const [who, setWho] = useState<Who>("coder");
  const [phase, setPhase] = useState<Phase>("idle");
  const [openMic, setOpenMic] = useState(false);
  const [conv, setConv] = useState("");
  const [lines, setLines] = useState<Line[]>([]);
  const [error, setError] = useState("");
  const [status] = usePoll(api.voiceStatus, 30000);
  const [gpu] = usePoll(api.gpu, 10000);
  const paused = gpu?.mode === "deep";
  const spokes = useRef<(HTMLElement | null)[]>([]);
  const rec = useRef<Recorder | null>(null);
  const player = useRef<Player | null>(null);
  const abort = useRef<AbortController | null>(null);
  const phaseRef = useRef<Phase>("idle");
  phaseRef.current = phase;
  const cur = WHO[who];

  const secure = window.isSecureContext;
  const ready = Boolean(status?.enabled && status.stt && status.tts && secure && !paused);

  // The ring follows whatever is sounding: your mic while you talk, Pablo while it answers.
  useEffect(() => {
    let raf = 0;
    const freq = new Uint8Array(128);
    const draw = () => {
      const p = phaseRef.current;
      const analyser = p === "listening" ? rec.current?.analyser : p === "speaking" ? player.current?.analyser : null;
      if (analyser) analyser.getByteFrequencyData(freq);
      const t = performance.now() / 1000;
      spokes.current.forEach((el, i) => {
        if (!el) return;
        let v: number;
        if (analyser) v = 0.15 + (freq[(i * 2) % 96] / 255) * 0.95;
        else if (p === "thinking") v = 0.2 + 0.25 * (0.5 + 0.5 * Math.sin(t * 3 + i * 0.35));
        else v = 0.14 + 0.05 * Math.abs(Math.sin(i * 1.7));
        el.style.transform = `scaleY(${v.toFixed(3)})`;
      });
      raf = requestAnimationFrame(draw);
    };
    raf = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(raf);
  }, []);

  useEffect(() => () => {
    rec.current?.cancel();
    player.current?.stop();
    abort.current?.abort();
    releaseMicrophone();
  }, []);

  const send = useCallback(
    async (wav: Blob) => {
      setPhase("thinking");
      setError("");
      const control = new AbortController();
      abort.current = control;
      const p = (player.current ??= new Player());
      p.reset();
      p.onIdle = () => {
        if (phaseRef.current === "speaking") setPhase("idle");
      };
      let reply = "";
      const at = new Date().toISOString();
      const onEvent = (e: VoiceEvent) => {
        if (e.type === "conversation") setConv(e.id);
        if (e.type === "heard") setLines((l) => [...l, { role: "user", text: e.text, lane: who, at }, { role: "assistant", text: "", lane: who, at }]);
        if (e.type === "delta") {
          reply += e.text;
          const shown = reply.split("\n").filter((x) => !/^\s*(RUN|REMEMBER):/.test(x)).join("\n");
          setLines((l) => [...l.slice(0, -1), { ...l[l.length - 1], text: shown }]);
        }
        if (e.type === "audio") {
          p.push(e.seq, e.data);
          setPhase("speaking");
        }
        if (e.type === "done") {
          setLines((l) => [...l.slice(0, -1), { ...l[l.length - 1], text: e.turn.reply.content.replace(/\n*\[run [^\]]+\]\s*$/, ""), runId: e.turn.reply.run_id }]);
          if (e.tts_error) setError(`Text-to-speech failed: ${e.tts_error}`);
          if (!p.playing) setPhase("idle");
        }
      };
      try {
        await api.voiceTurn(wav, who, conv, onEvent, control.signal);
      } catch (e) {
        if ((e as Error).name !== "AbortError") setError((e as ApiError).message);
        setLines((l) => (l.length && l[l.length - 1].role === "assistant" && !l[l.length - 1].text ? l.slice(0, -1) : l));
        setPhase("idle");
      }
    },
    [who, conv],
  );

  const startTalking = useCallback(async () => {
    if (!ready || rec.current) return;
    player.current?.stop(); // barge-in
    abort.current?.abort();
    try {
      rec.current = await record();
      setPhase("listening");
    } catch (e) {
      setError(`The browser didn't give Pablo the microphone: ${(e as Error).message}`);
    }
  }, [ready]);

  const stopTalking = useCallback(() => {
    const r = rec.current;
    rec.current = null;
    if (!r) return;
    const wav = r.stop();
    if (wav.size < 16000 * 2 * 0.35) {
      setPhase("idle"); // shorter than ~a third of a second: a tap, not speech
      return;
    }
    void send(wav);
  }, [send]);

  // Space bar: hold to talk.
  useEffect(() => {
    const down = (e: KeyboardEvent) => {
      if (e.code !== "Space" || e.repeat || openMic || (e.target as HTMLElement).closest("input, textarea")) return;
      e.preventDefault();
      void startTalking();
    };
    const up = (e: KeyboardEvent) => {
      if (e.code !== "Space" || openMic) return;
      e.preventDefault();
      stopTalking();
    };
    window.addEventListener("keydown", down);
    window.addEventListener("keyup", up);
    return () => {
      window.removeEventListener("keydown", down);
      window.removeEventListener("keyup", up);
    };
  }, [startTalking, stopTalking, openMic]);

  // Open mic: start when you speak, send after a short silence.
  useEffect(() => {
    if (!openMic || !ready) return;
    let alive = true;
    let quietSince = 0;
    let speaking = false;
    let watcher: { analyser: AnalyserNode } | null = null;
    const buf = new Uint8Array(256);
    const tick = async () => {
      if (!alive) return;
      if (!rec.current && phaseRef.current !== "thinking") {
        // Listen for the start of speech with a recorder that's thrown away if nothing comes.
        await startTalking();
        speaking = false;
        quietSince = performance.now();
        watcher = rec.current;
      }
      const r = rec.current;
      if (r && watcher) {
        const level = rms(r.analyser, buf);
        const now = performance.now();
        // While Pablo talks, its own voice can leak into the mic; interrupting takes a clear voice.
        const needed = phaseRef.current === "speaking" ? SPEECH_LEVEL * 3 : SPEECH_LEVEL;
        if (level > needed) {
          speaking = true;
          quietSince = now;
          if (phaseRef.current === "speaking") player.current?.stop();
        } else if (speaking && now - quietSince > SILENCE_MS) {
          stopTalking();
          watcher = null;
        } else if (!speaking && now - quietSince > 8000) {
          r.cancel(); // nothing said; start a fresh take so the recording stays short
          rec.current = null;
        }
      }
      if (alive) window.setTimeout(tick, 60);
    };
    void tick();
    return () => {
      alive = false;
      rec.current?.cancel();
      rec.current = null;
      setPhase((p) => (p === "listening" ? "idle" : p));
    };
  }, [openMic, ready, startTalking, stopTalking]);

  const newCall = () => {
    player.current?.stop();
    abort.current?.abort();
    setConv("");
    setLines([]);
    setPhase("idle");
  };

  const stateText = {
    idle: openMic ? "listening for you" : "hold to talk · or hold space",
    listening: "● hearing you",
    thinking: who === "deep" ? "deep is thinking…" : "thinking…",
    speaking: "● speaking",
  }[phase];

  return (
    <div style={{ display: "flex", minHeight: "100vh" }}>
      <section className="h-page" style={{ flex: 999, minWidth: 0 }}>
        <header className="h-head">
          <h1 className="h-title">VOICE</h1>
          {conv && (
            <a className="h-mono" style={{ fontSize: 12, color: "var(--sys)" }} href={`#/chat/${conv}`}>
              open as chat
            </a>
          )}
          <span style={{ marginLeft: "auto" }}>
            {paused ? (
              <span className="h-chip" style={{ color: "var(--lane-deep)", borderColor: "#5a2a3c" }}>Deep Work mode: voice paused so deep keeps the GPU</span>
            ) : (
              <VoiceHealth ready={ready} secure={secure} status={status} />
            )}
          </span>
        </header>

        <div style={{ flex: 1, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 20, padding: "8px 0" }}>
          <div className="h-seg" role="radiogroup" aria-label="Who you're talking to">
            {(Object.keys(WHO) as Who[]).map((k) => (
              <button key={k} role="radio" aria-checked={who === k} aria-pressed={who === k} onClick={() => setWho(k)} style={who === k ? { background: WHO[k].color, color: "#03070a" } : { color: WHO[k].color }}>
                {WHO[k].label}
              </button>
            ))}
          </div>

          <button
            className="h-orb"
            disabled={!ready || openMic}
            onPointerDown={(e) => {
              e.currentTarget.setPointerCapture(e.pointerId);
              void startTalking();
            }}
            onPointerUp={stopTalking}
            onPointerCancel={stopTalking}
            aria-label={`${cur.label}: ${stateText}. Hold to talk.`}
            style={{ cursor: ready && !openMic ? "pointer" : "default", touchAction: "none" }}
          >
            <span className="h-aura" style={{ background: `radial-gradient(circle, ${phase === "listening" ? "#ffb5478c" : cur.glow} 0%, transparent 65%)`, animationDuration: phase === "speaking" ? "2.2s" : "4.5s" }} />
            {Array.from({ length: SPOKES }, (_, i) => (
              <span key={i} className="h-spoke" style={{ transform: `rotate(${(360 / SPOKES) * i}deg) translateY(-112px)` }}>
                <i ref={(el) => (spokes.current[i] = el)} style={{ background: phase === "listening" ? "#ffb547" : cur.color, opacity: phase === "idle" ? 0.5 : 0.95 }} />
              </span>
            ))}
            <span className="h-core" style={{ background: `radial-gradient(circle at 38% 32%, #ffffff 0%, ${cur.color} 34%, ${cur.deep} 78%, #020406 100%)`, boxShadow: `0 0 50px ${cur.glow}, inset 0 0 30px #00000080` }} />
          </button>

          <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 6, textAlign: "center", maxWidth: 480 }}>
            <span style={{ fontSize: 15, letterSpacing: ".34em", fontWeight: 700, color: cur.color }}>{cur.label}</span>
            <span className="h-mono h-dim" style={{ fontSize: 12 }}>{cur.model}</span>
            <span className="h-mono" style={{ fontSize: 12, color: phase === "listening" ? "var(--accent)" : phase === "idle" ? "var(--ink-faint)" : cur.color }}>{stateText}</span>
            <span className="h-dim" style={{ fontSize: 13, lineHeight: 1.5 }}>{cur.blurb}</span>
          </div>

          <div className="h-frame h-pad" style={{ flexDirection: "row", flexWrap: "wrap", alignItems: "center", maxWidth: 760, width: "100%" }}>
            <div className="h-seg" role="group" aria-label="Mic mode">
              <button aria-pressed={!openMic} onClick={() => setOpenMic(false)}>hold to talk</button>
              <button aria-pressed={openMic} onClick={() => setOpenMic(true)} disabled={!ready}>open mic</button>
            </div>
            <span className="h-dim" style={{ fontSize: 13, flex: "1 1 240px" }}>
              Talk over it to interrupt. Switching who you talk to keeps the transcript.
            </span>
            <button className="h-btn" onClick={newCall}>NEW CALL</button>
          </div>
          {error && <div className="h-error" style={{ maxWidth: 760, width: "100%" }}>{error}</div>}
        </div>
      </section>

      <aside className="h-rail-r" style={{ flex: "0 0 400px" }} aria-label="Transcript">
        <span className="h-cap">Transcript</span>
        {lines.length === 0 && <span className="h-faint" style={{ fontSize: 12.5 }}>What you say and what it answers shows up here. Changes it hands off appear as runs you approve.</span>}
        {lines.map((l, i) => (
          <div key={i} style={{ display: "flex", flexDirection: "column", gap: 6, paddingLeft: 10, borderLeft: `2px solid ${l.role === "user" ? "var(--accent)" : WHO[(l.lane as Who) in WHO ? (l.lane as Who) : "coder"].color}` }}>
            <span className="h-mono" style={{ fontSize: 11, letterSpacing: ".12em", color: l.role === "user" ? "var(--accent)" : WHO[(l.lane as Who) in WHO ? (l.lane as Who) : "coder"].color }}>
              {l.role === "user" ? (me?.name && me.name !== "Owner" ? me.name.toUpperCase() : "YOU") : WHO[(l.lane as Who) in WHO ? (l.lane as Who) : "coder"].label} · {ago(l.at)}
            </span>
            {l.text ? <div style={{ fontSize: 14 }}><Markdown text={l.text} /></div> : <span className="h-thinking">…</span>}
            {l.runId && <RunCard id={l.runId} me={me} />}
          </div>
        ))}
      </aside>
    </div>
  );
}

function VoiceHealth({ ready, secure, status }: { ready: boolean; secure: boolean; status: { enabled: boolean; stt: boolean; tts: boolean } | null }) {
  if (ready) return <span className="h-chip" style={{ color: "var(--status-pass)", borderColor: "#1f5a3c" }}>● live · audio stays on Pablo</span>;
  const why = !secure
    ? "the mic needs HTTPS: run `pablo cert`, then open the https address"
    : !status
      ? "checking voice…"
      : !status.enabled
        ? "voice isn't set up on Pablo yet (M9b)"
        : !status.stt
          ? "speech-to-text isn't answering"
          : "text-to-speech isn't answering";
  return <span className="h-chip" style={{ color: "var(--accent)", borderColor: "#5a3d12" }}>{why}</span>;
}

// Keep the audio context warm after the first click so replies can play without a gesture.
if (typeof window !== "undefined") {
  window.addEventListener("pointerdown", () => audioContext(), { once: true });
}
