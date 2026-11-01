// Phone remote: decisions first. Same harness API as the desktop cockpit.
import { useRef, useState } from "react";
import { api, type Lane, type Phase, type PlanTask, type Run } from "./api";
import { CertaintyGauge, LaneBadge, LaneRibbon, laneColor } from "./components";

type Tab = "inbox" | "plan" | "new";

export interface PhoneProps {
  lanes: Lane[];
  busy: Record<string, number>;
  inbox: Run[];
  phase: Phase | null;
  project: string;
  statusText: string;
  error: string;
  onApprove: (id: string) => void;
  onReject: (id: string) => void;
  onEscalate: (id: string) => void;
  onSend: (task: string, taskId?: string) => void;
}

export function Phone(props: PhoneProps) {
  const [tab, setTab] = useState<Tab>("inbox");
  const waiting = props.inbox.filter((r) => r.state === "needs_you").length;
  const done = props.phase?.tasks.filter((t) => t.status === "done").length ?? 0;

  return (
    <div className="phone">
      <LaneRibbon lanes={props.lanes} busy={props.busy} />
      <header className="phone-head">
        <span className="brand-name">Pablo</span>
        <span className="mono muted">{props.project}</span>
      </header>

      {props.error && (
        <div className="error-bar" role="alert">
          {props.error}
        </div>
      )}

      <main className="phone-main">
        {tab === "inbox" && (
          <>
            <div>
              <h1 className="phone-title">
                {props.inbox.length === 0
                  ? "Nothing needs you"
                  : waiting === 0
                    ? `${props.inbox.length} ready for you`
                    : `${waiting === 1 ? "One thing needs" : `${waiting} things need`} you`}
              </h1>
              {props.phase && (
                <p className="muted">
                  Phase {props.phase.id} · {done} of {props.phase.tasks.length} done
                </p>
              )}
            </div>
            {props.inbox.map((run) => (
              <PhoneCard key={run.id} run={run} {...props} />
            ))}
          </>
        )}

        {tab === "plan" && props.phase && <PhonePlan phase={props.phase} onRun={(t) => props.onSend(t.title, t.id)} />}
        {tab === "plan" && !props.phase && <p className="muted">No plan yet. Run `pablo init` on Pablo.</p>}

        {tab === "new" && <PhoneNew onSend={(task) => (props.onSend(task), setTab("inbox"))} />}
      </main>

      {props.statusText && (
        <div className="status-line mono phone-status">
          <span className="dot" style={{ background: laneColor("coder") }} />
          {props.statusText}
        </div>
      )}

      <HoldToTalk />

      <nav className="phone-nav" aria-label="Main">
        <button type="button" aria-current={tab === "inbox" ? "page" : undefined} onClick={() => setTab("inbox")}>
          <span className={`nav-count ${waiting ? "on" : ""}`}>{props.inbox.length}</span>
          Needs you
        </button>
        <button type="button" aria-current={tab === "plan" ? "page" : undefined} onClick={() => setTab("plan")}>
          <span aria-hidden="true">☰</span>
          Plan
        </button>
        <button type="button" aria-current={tab === "new" ? "page" : undefined} onClick={() => setTab("new")}>
          <span aria-hidden="true">＋</span>
          New task
        </button>
      </nav>
    </div>
  );
}

function PhoneCard({ run, onApprove, onReject, onEscalate }: { run: Run } & PhoneProps) {
  const [confirmEscalate, setConfirmEscalate] = useState(false);
  const timer = useRef<number | undefined>(undefined);
  const v = run.verdict;

  const escalate = () => {
    if (confirmEscalate) {
      window.clearTimeout(timer.current);
      setConfirmEscalate(false);
      onEscalate(run.id);
      return;
    }
    setConfirmEscalate(true);
    timer.current = window.setTimeout(() => setConfirmEscalate(false), 4000);
  };

  return (
    <section className={`phone-card ${run.state === "needs_you" ? "waiting" : ""}`}>
      <div className="run-head">
        <LaneBadge lane={run.lane} />
        <span className="grow" />
        {run.certainty !== null && <CertaintyGauge value={run.certainty} />}
      </div>
      <p className="phone-task">{run.task}</p>
      {run.question && run.state === "needs_you" && <p className="phone-question">{run.question}</p>}
      {v && (
        <div className="chips">
          {v.checks.map((c) => (
            <span key={c.name} className={`chip ${c.passed ? "pass" : "fail"}`}>
              {c.name} {c.passed ? "passed" : "failed"}
            </span>
          ))}
          <span className="chip mono">
            +{v.added} −{v.removed}
          </span>
        </div>
      )}
      <div className="phone-actions">
        <button type="button" className="btn primary" onClick={() => onApprove(run.id)}>
          Approve
        </button>
        <button type="button" className="btn" onClick={() => onReject(run.id)}>
          Reject
        </button>
        {run.lane !== "claude" && (
          <button
            type="button"
            className={`btn escalate ${confirmEscalate ? "armed" : ""}`}
            aria-label={confirmEscalate ? "Tap again to send to Claude" : "Escalate to Claude"}
            onClick={escalate}
          >
            {confirmEscalate ? "Send to Claude?" : "E"}
          </button>
        )}
      </div>
    </section>
  );
}

function PhonePlan({ phase, onRun }: { phase: Phase; onRun: (t: PlanTask) => void }) {
  return (
    <section className="panel">
      <h2 className="label">
        Phase {phase.id} · {phase.title}
      </h2>
      <ol className="tasks">
        {phase.tasks.map((t) => (
          <li key={t.id} className={`task ${t.status}`}>
            <span>
              {t.id} {t.title}
            </span>
            {t.status === "queued" ? (
              <button type="button" className="link-btn" onClick={() => onRun(t)}>
                Run
              </button>
            ) : (
              <span className={`small status-${t.status}`}>{t.status.replace("_", " ")}</span>
            )}
          </li>
        ))}
      </ol>
      <p className="gate small muted">Gate: {phase.gate}</p>
    </section>
  );
}

function PhoneNew({ onSend }: { onSend: (task: string) => void }) {
  const [draft, setDraft] = useState("");
  return (
    <form
      className="panel"
      onSubmit={(e) => {
        e.preventDefault();
        if (draft.trim()) onSend(draft.trim());
      }}
    >
      <label htmlFor="phone-task" className="label">
        New task
      </label>
      <textarea
        id="phone-task"
        className="phone-textarea"
        rows={5}
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        placeholder="What should the team do?"
      />
      <button type="submit" className="btn primary">
        Send to the team
      </button>
    </form>
  );
}

/** Records on Pablo's behalf; audio goes only to the harness, never a cloud speech API. */
function HoldToTalk() {
  const [state, setState] = useState<"idle" | "recording" | "sending">("idle");
  const [note, setNote] = useState("");
  const recorder = useRef<MediaRecorder | null>(null);
  const chunks = useRef<Blob[]>([]);

  const supported = typeof window !== "undefined" && window.isSecureContext && !!navigator.mediaDevices;

  const start = async () => {
    if (!supported) {
      setNote("Voice needs HTTPS. Open Pablo through `tailscale serve`.");
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      chunks.current = [];
      const rec = new MediaRecorder(stream);
      rec.ondataavailable = (e) => chunks.current.push(e.data);
      rec.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop());
        setState("sending");
        try {
          await api.voice(new Blob(chunks.current, { type: rec.mimeType }));
          setNote("");
        } catch (e) {
          setNote((e as Error).message);
        }
        setState("idle");
      };
      recorder.current = rec;
      rec.start();
      setState("recording");
      setNote("");
    } catch {
      setNote("Microphone permission was denied.");
    }
  };

  const stop = () => {
    if (recorder.current?.state === "recording") recorder.current.stop();
  };

  return (
    <div className="talk">
      <button
        type="button"
        className={`talk-btn ${state}`}
        onPointerDown={start}
        onPointerUp={stop}
        onPointerLeave={stop}
      >
        {state === "recording" ? "Listening… release to send" : state === "sending" ? "Sending…" : "Hold to talk to the team"}
      </button>
      {note && <p className="small muted talk-note">{note}</p>}
    </div>
  );
}
