// Chat (and the Advisor, which is the same page in violet): conversations that remember.
// A reply can start a run; the run card shows the checks, and only the owner sees Apply.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError, type ChatMessage, type Conversation, type Lane, type Me, type Memory, type SearchHits } from "./api";
import { laneColor, laneName } from "./components";
import { ago, go, pickProject, PlanCard, ProjectPicker, RunCard, useEvents, usePoll, useProject } from "./hud";
import { Markdown } from "./Markdown";

type Mode = "chat" | "advisor";
type Act = "auto" | "talk" | "do";

interface Option {
  key: string;
  name: string;
  desc: string;
  meta: string;
  group?: string;
}

function modelOptions(lanes: Lane[], me: Me | null): Option[] {
  const by = new Map(lanes.map((l) => [l.name, l]));
  const out: Option[] = [];
  const add = (key: string, lane: string, name: string, desc: string, group?: string) => {
    const l = by.get(lane);
    if (l) out.push({ key, name, desc, meta: l.display || l.model.replace("pablo/", ""), group });
  };
  add("coder", "coder", "Base", "Fastest. Good for most things. Answers alone: if a change breaks, it comes to you.");
  add("advisor", "advisor", "No guardrails", "Unfiltered. Talks here; builds only in a no-guardrails project.", "UNFILTERED");
  add("deep", "deep", "Best", "Our strongest model. Slow: expect a pause, and minutes for big changes.");
  const coder = by.get("coder");
  const deep = by.get("deep");
  if (coder && deep)
    out.push({
      key: "auto",
      name: "Hybrid",
      desc: "Our system: the base model builds, checks and a judge review it, and Best retries anything that breaks.",
      meta: `${coder.display || "coder"} → ${deep.display || "deep"}`,
    });
  if (me?.can_approve) add("claude", "claude", "Claude", "Leaves Pablo. Asks before every send; never gets your memory.", "OFF THE BOX");
  return out;
}

export function ChatPage({ mode, id, me, lanes }: { mode: Mode; id: string; me: Me | null; lanes: Lane[] }) {
  const advisor = mode === "advisor";
  const [convs, reloadConvs] = usePoll(api.conversations, 20000);
  const [kept, reloadKept] = usePoll(() => api.memories("kept"), 0);
  const [proposed, reloadProposed] = usePoll(() => api.memories("proposed"), 0);
  const [conv, setConv] = useState<Conversation | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [pending, setPending] = useState<string | null>(null);
  const [streamed, setStreamed] = useState("");
  const [status, setStatus] = useState("");
  const [lane, setLane] = useState(advisor ? "advisor" : "auto");
  // Extended thinking: Base and Hybrid only, remembered per browser. Slower, more careful.
  const [thinkOn, setThinkOn] = useState(() => {
    try {
      return localStorage.getItem("pablo.think") === "1";
    } catch {
      return false;
    }
  });
  const toggleThink = () =>
    setThinkOn((on) => {
      try {
        localStorage.setItem("pablo.think", on ? "0" : "1");
      } catch {
        /* not remembered in a private window */
      }
      return !on;
    });
  const [act, setAct] = useState<Act>("auto");
  const [menu, setMenu] = useState(false);
  const [confirm, setConfirm] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<SearchHits | null>(null);
  const [note, setNote] = useState("");
  const [deleting, setDeleting] = useState(false);
  const bottom = useRef<HTMLDivElement>(null);
  const box = useRef<HTMLTextAreaElement>(null);

  const [projects] = usePoll(api.projects, 60000);
  const [chosen, setChosen] = useProject();
  const [convUnfiltered, setConvUnfiltered] = useState(false);
  const startIn = pickProject(projects, chosen);
  // An open conversation keeps its own project; a new one starts in the chosen project.
  const unfiltered = conv ? convUnfiltered : startIn?.mode === "unfiltered";
  const projectName = conv ? conv.project : startIn?.name ?? "";
  const canThink = !advisor && !unfiltered && (lane === "coder" || lane === "auto") && Boolean(lanes.find((l) => l.name === "coder")?.thinking);
  const options = useMemo(() => {
    if (!unfiltered) return modelOptions(lanes, me);
    const raw = lanes.find((l) => l.name === "advisor");
    return [{ key: "auto", name: "No guardrails", desc: "This project runs on one model end to end: it talks, plans, writes and reviews. Nothing else is used.", meta: raw?.display || "advisor" }];
  }, [lanes, me, unfiltered]);
  const mine = (convs ?? []).filter((c) => (advisor ? c.lane === "advisor" : c.lane !== "advisor"));

  const load = useCallback((cid: string) => {
    api
      .conversation(cid)
      .then((r) => {
        setConv(r.conversation);
        setMessages(r.messages);
        setConvUnfiltered(Boolean(r.unfiltered));
        if (!advisor) setLane(r.conversation.lane);
      })
      .catch((e: ApiError) => setError(e.status === 404 ? "That conversation is gone." : e.message));
  }, [advisor]);

  useEffect(() => {
    setError("");
    setConfirm(null);
    setDeleting(false);
    if (id) load(id);
    else {
      setConv(null);
      setMessages([]);
      setLane(advisor ? "advisor" : "auto");
    }
  }, [id, load, advisor]);

  useEffect(() => {
    // Braces matter: newer browsers return a Promise here, and React would call it as cleanup.
    void bottom.current?.scrollIntoView({ block: "end" });
  }, [messages, pending, streamed, proposed]);

  useEvents((e) => {
    if (pending !== null && e.kind === "status" && typeof e.data.text === "string") setStatus(e.data.text);
  });

  const send = async (text: string, confirmed = false) => {
    if (!text.trim() || pending !== null) return;
    if (lane === "claude" && !confirmed) {
      setConfirm(text);
      return;
    }
    setConfirm(null);
    setError("");
    setPending(text);
    setStreamed("");
    setStatus("");
    setDraft("");
    try {
      let cid = conv?.id;
      if (!cid) {
        const made = await api.newConversation(unfiltered ? "auto" : lane, "", advisor ? "" : startIn?.name ?? "");
        cid = made.id;
        go(advisor ? "advisor" : "chat", cid);
      }
      const doIt = advisor || lane === "claude" ? null : act === "do" ? true : act === "talk" ? false : null;
      const turn = await api.sayStream(cid, text, { lane, do: doIt, confirm: confirmed, think: canThink && thinkOn }, (piece) =>
        setStreamed((s) => s + piece),
      );
      setConv(turn.conversation);
      // Reload rather than append: a new conversation's page may already have fetched these.
      const fresh = await api.conversation(cid);
      setMessages(fresh.messages);
      if (turn.proposed.length) reloadProposed();
      if (turn.kept.length) reloadKept();
      reloadConvs();
    } catch (e) {
      setError((e as Error).message);
      setDraft(text);
    } finally {
      setPending(null);
      setStreamed("");
      setStatus("");
      box.current?.focus();
    }
  };

  const search = (q: string) => {
    setQuery(q);
    if (q.trim().length < 3) return setHits(null);
    api.search(q).then(setHits).catch(() => setHits(null));
  };

  const decide = (m: Memory, keep: boolean) =>
    (keep ? api.keepMemory(m.id) : api.rejectMemory(m.id)).then(() => {
      reloadProposed();
      reloadKept();
    });

  const here = (proposed ?? []).filter((m) => m.source_conversation === conv?.id);
  // Until /api/lanes answers, the menu has no options yet: show the choice by its name.
  const current = options.find((o) => o.key === lane) ?? options[0] ?? { key: lane, name: laneName(lane), desc: "", meta: "" };
  const accent = advisor ? "var(--lane-advisor)" : laneColor(lane);
  const frame = advisor ? "violet" : "";

  return (
    <div className="h-chat">
      <aside className="h-list" aria-label={advisor ? "Advisor sessions" : "Conversations"}>
        <button className={`h-btn ${advisor ? "violet" : ""}`} style={{ justifyContent: "center" }} onClick={() => go(mode)}>
          + {advisor ? "NEW SESSION" : "NEW"}
        </button>
        <input className="h-input" type="search" placeholder="Search everything you've said…" value={query} onChange={(e) => search(e.target.value)} aria-label="Search conversations" />
        {advisor && (
          <span className="h-mono h-faint" style={{ fontSize: 11, lineHeight: 1.5, padding: "0 4px" }}>
            Kept apart from Chat. Never trained on. It can't change your code.
          </span>
        )}
        {hits ? (
          <>
            <span className="h-cap" style={{ padding: "4px 8px" }}>Found</span>
            {hits.memories.map((m) => (
              <span key={m.id} className="h-chip" style={{ margin: "0 8px" }}>◆ {m.text}</span>
            ))}
            {hits.messages.map((h) => (
              <a key={h.id} className="h-item" href={`#/chat/${h.conversation_id}`} style={{ flexDirection: "column", alignItems: "flex-start", gap: 2 }}>
                <span style={{ width: "100%" }}>{h.title}</span>
                <span className="h-faint" style={{ fontSize: 12, whiteSpace: "normal" }}>{h.content.slice(0, 90)}</span>
              </a>
            ))}
            {!hits.messages.length && !hits.memories.length && <span className="h-faint" style={{ fontSize: 12, padding: 8 }}>Nothing matches.</span>}
          </>
        ) : (
          <>
            {mine.length === 0 && <span className="h-faint" style={{ fontSize: 12, padding: 8 }}>No conversations yet.</span>}
            {mine.map((c) => (
              <a key={c.id} className="h-item" href={`#/${mode}/${c.id}`} aria-current={c.id === conv?.id ? "page" : undefined}>
                <span>{c.title}</span>
                <span className="h-dot" style={{ width: 6, height: 6, background: laneColor(c.lane) }} aria-hidden="true" />
              </a>
            ))}
          </>
        )}
      </aside>

      <section className="h-thread">
        <header className="h-head" style={{ padding: "14px 28px", borderBottomColor: advisor ? "#1c1636" : undefined }}>
          {advisor && <span className="h-bot-mark orb" style={{ width: 40, height: 40 }} aria-hidden="true" />}
          <h1 style={{ margin: 0, fontSize: 18, fontWeight: 600 }}>{conv?.title ?? (advisor ? "Advisor" : "New conversation")}</h1>
          {conv ? (
            <span className="h-chip" style={{ color: "var(--sys)" }}>
              {projects?.find((p) => p.name === conv.project)?.title ?? conv.project}
            </span>
          ) : (
            !advisor && <ProjectPicker projects={projects} value={chosen} onChange={setChosen} />
          )}
          {unfiltered && !advisor && (
            <span className="h-chip" style={{ color: "var(--lane-advisor)" }}>
              no guardrails · one model
            </span>
          )}
          {advisor && <span className="h-mono h-dim" style={{ fontSize: 12 }}>Mistral Small 3.2 24B heretic · on Pablo</span>}
          {conv && (
            <span style={{ marginLeft: "auto", display: "flex", gap: 8 }}>
              {deleting ? (
                <>
                  <button className="h-btn danger" onClick={() => api.deleteConversation(conv.id).then(() => (reloadConvs(), go(mode)))}>
                    DELETE FOR GOOD
                  </button>
                  <button className="h-btn" onClick={() => setDeleting(false)}>KEEP</button>
                </>
              ) : (
                <button className="h-btn" onClick={() => setDeleting(true)} title="Deletes its messages, search entries, and notes it produced">
                  DELETE
                </button>
              )}
            </span>
          )}
        </header>

        <div className="h-msgs">
          {messages.length === 0 && pending === null && (
            <div className={`h-frame ${frame} h-pad`} style={{ marginTop: 40 }}>
              <span className="h-cap" style={{ color: accent }}>{advisor ? "Ask for the take the team won't give you" : "Start here"}</span>
              <span style={{ fontSize: 14, color: "var(--ink-body)", lineHeight: 1.6 }}>
                {advisor
                  ? "The advisor talks only. It has no stake in your choices and no guardrails. Bring it a plan, a run, or a decision."
                  : "Ask about the code, say what to change, or describe something bigger (\"let's build a tool that…\") and Pablo plans it with you, then builds it a step at a time when you say go. Every change shows its diff and checks here; only the owner can apply it. Type \"remember: …\" to keep a note."}
              </span>
            </div>
          )}
          {messages.map((m) =>
            m.role === "user" ? (
              <div key={m.id} className="h-user">{m.content}</div>
            ) : (
              <div key={m.id} className="h-bot">
                <span className={`h-bot-mark ${m.lane === "advisor" ? "orb" : ""}`} style={m.lane === "advisor" ? undefined : { borderColor: laneColor(m.lane), color: laneColor(m.lane) }} aria-hidden="true">
                  {m.lane === "advisor" ? "" : "P"}
                </span>
                <div className="h-bot-body">
                  <span className="h-mono h-faint" style={{ fontSize: 11 }}>
                    {laneName(m.lane)} · {ago(m.created_at)}
                  </span>
                  <Markdown text={m.run_id ? m.content.replace(/\n*\[run [^\]]+\]\s*$/, "") : m.content} />
                  {m.run_id && <RunCard id={m.run_id} me={me} />}
                </div>
              </div>
            ),
          )}
          {pending !== null && (
            <>
              <div className="h-user">{pending}</div>
              <div className="h-bot">
                <span className={`h-bot-mark ${lane === "advisor" ? "orb" : ""}`} aria-hidden="true">
                  {lane === "advisor" ? "" : "P"}
                </span>
                <div className="h-bot-body" aria-live="polite">
                  {visibleWhileStreaming(streamed) ? (
                    <div>
                      <Markdown text={visibleWhileStreaming(streamed)} />
                      <span className="h-caret" aria-hidden="true" />
                    </div>
                  ) : (
                    <span className="h-thinking">{status || (lane === "deep" ? `${laneName("deep")} is thinking (this takes a while)…` : canThink && thinkOn ? "thinking it through (extended)…" : "thinking…")}</span>
                  )}
                </div>
              </div>
            </>
          )}
          {here.length > 0 && (
            <div className="h-frame violet h-pad">
              <span className="h-cap" style={{ color: "var(--lane-advisor)" }}>Remember this?</span>
              {here.map((m) => (
                <div key={m.id} style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                  <span style={{ flex: 1, fontSize: 14 }}>{m.text}</span>
                  <button className="h-btn violet solid" onClick={() => decide(m, true)}>KEEP</button>
                  <button className="h-btn" onClick={() => decide(m, false)}>NOT THIS</button>
                </div>
              ))}
            </div>
          )}
          {error && <div className="h-error">{error}</div>}
          {!advisor && projectName && <PlanCard project={projectName} me={me} />}
          <div ref={bottom} />
        </div>

        <div className="h-composer">
          {confirm !== null && (
            <div className="h-frame amber h-pad" style={{ maxWidth: 900, margin: "0 auto 10px" }}>
              <span style={{ fontSize: 14 }}>This leaves Pablo for Claude. Your memory and other conversations stay here; only this conversation goes.</span>
              <div style={{ display: "flex", gap: 8 }}>
                <button className="h-btn amber solid" onClick={() => send(confirm, true)}>SEND TO CLAUDE</button>
                <button className="h-btn" onClick={() => setConfirm(null)}>CANCEL</button>
              </div>
            </div>
          )}
          <div className={`h-frame ${frame}`} style={{ maxWidth: 900, margin: "0 auto", padding: "12px 14px", display: "flex", flexDirection: "column", gap: 10 }}>
            <textarea
              ref={box}
              rows={2}
              value={draft}
              aria-label="Message"
              placeholder={advisor ? "Ask for the honest take…" : "Ask, or describe a change…  (Enter sends · Shift+Enter new line)"}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  void send(draft);
                }
              }}
            />
            <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center" }}>
              {!advisor && (
                <div style={{ position: "relative" }}>
                  <button className="h-btn" style={{ color: accent, borderColor: "var(--sys-line)" }} onClick={() => setMenu((o) => !o)} aria-haspopup="listbox" aria-expanded={menu}>
                    <span className="h-dot" style={{ background: accent }} aria-hidden="true" />
                    {current.name} <span className="h-dim">▾</span>
                  </button>
                  {menu && (
                    <div className="h-menu" role="listbox" aria-label="Model">
                      {options.map((o) => (
                        <div key={o.key}>
                          {o.group && <div className="h-cap" style={{ padding: "8px 10px 4px", borderTop: "1px solid var(--line)", marginTop: 4, fontSize: 10.5 }}>{o.group}</div>}
                          <button
                            className="h-opt"
                            role="option"
                            aria-selected={o.key === lane}
                            onClick={() => {
                              if (o.key === "advisor") go("advisor");
                              else setLane(o.key);
                              setMenu(false);
                            }}
                          >
                            <span className="h-dot" style={{ marginTop: 6, background: laneColor(o.key) }} aria-hidden="true" />
                            <span style={{ flex: 1, display: "flex", flexDirection: "column", gap: 2 }}>
                              <span style={{ fontSize: 14, fontWeight: 600, color: laneColor(o.key) }}>{o.name}</span>
                              <span style={{ fontSize: 12.5, color: "#a9c4cf" }}>{o.desc}</span>
                              <span className="h-mono h-faint" style={{ fontSize: 11 }}>{o.meta}</span>
                            </span>
                            <span style={{ color: "var(--sys)" }}>{o.key === lane ? "✓" : ""}</span>
                          </button>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}
              {canThink && (
                <button
                  className="h-btn"
                  aria-pressed={thinkOn}
                  onClick={toggleThink}
                  title="The base model thinks before it answers and before it writes code. Slower: often a minute or more."
                  style={thinkOn ? { color: "var(--sys)", borderColor: "var(--sys-line)" } : undefined}
                >
                  {thinkOn ? "● " : "○ "}EXTENDED THINKING
                </button>
              )}
              {!advisor && lane !== "claude" && (
                <div className="h-seg" role="group" aria-label="What to do">
                  {(["auto", "talk", "do"] as Act[]).map((a) => (
                    <button key={a} aria-pressed={act === a} onClick={() => setAct(a)} title={a === "auto" ? "Pablo decides whether to start a run" : a === "talk" ? "Only talk; never start a run" : "Start a run with these words as the task"}>
                      {a === "auto" ? "auto" : a === "talk" ? "just talk" : "do it"}
                    </button>
                  ))}
                </div>
              )}
              <span className="h-dim" style={{ fontSize: 12 }}>
                {advisor ? "Talks only · local · never trained on" : lane === "claude" ? "Asks before sending · never gets your memory" : "Runs wait for Apply"}
              </span>
              <button className={`h-btn solid ${advisor ? "violet" : ""}`} style={{ marginLeft: "auto" }} disabled={pending !== null || !draft.trim()} onClick={() => send(draft)} aria-label="Send">
                ↑
              </button>
            </div>
          </div>
        </div>
      </section>

      <aside className="h-rail-r" aria-label="What Pablo remembers">
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <span className="h-cap">Remembered</span>
          {(kept ?? []).length === 0 && <span className="h-faint" style={{ fontSize: 12 }}>Nothing yet.</span>}
          {(kept ?? []).map((m) => (
            <div key={m.id} style={{ display: "flex", gap: 6, alignItems: "flex-start" }}>
              <span style={{ flex: 1, fontSize: 13, lineHeight: 1.45, color: "var(--ink-body)" }}>{m.text}</span>
              <button className="h-faint" style={{ background: "none", border: 0, padding: 0 }} aria-label={`Forget: ${m.text}`} title="Forget" onClick={() => api.forgetMemory(m.id).then(reloadKept)}>
                ×
              </button>
            </div>
          ))}
          <form
            onSubmit={(e) => {
              e.preventDefault();
              if (note.trim()) api.remember(note.trim()).then(() => (setNote(""), reloadKept()));
            }}
          >
            <input className="h-input" value={note} onChange={(e) => setNote(e.target.value)} placeholder="+ remember something" aria-label="Remember something" />
          </form>
        </div>
        {conv && (
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            <span className="h-cap">This conversation</span>
            <span className="h-mono" style={{ fontSize: 12 }}>{messages.length} messages</span>
            <span className="h-mono" style={{ fontSize: 12 }}>{messages.filter((m) => m.run_id).length} runs</span>
            <span className="h-mono h-dim" style={{ fontSize: 12 }}>started {ago(conv.created_at)}</span>
          </div>
        )}
        <span className="h-faint" style={{ fontSize: 11.5, lineHeight: 1.5 }}>
          Your notes and other conversations go into local prompts only. Claude never sees them.
        </span>
      </aside>
    </div>
  );
}

/** While a reply streams, hide the control lines (RUN:, PLAN:, …) the harness acts on afterwards. */
export const CONTROL = /^\s*(RUN|REMEMBER|PLAN|STEP|BUILD|APPLY|DISCARD):/;
function visibleWhileStreaming(text: string): string {
  return text
    .split("\n")
    .filter((line) => !CONTROL.test(line) && !/^\s*[A-Z]{1,8}$/.test(line)) // a control word still arriving
    .join("\n")
    .trim();
}
