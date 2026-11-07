// Chat (and the Advisor, which is the same page in violet): conversations that remember.
// A reply can start a run; the run card shows the checks, and only the owner sees Apply.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError, type ChatMessage, type Conversation, type KeptFile, type Lane, type Me, type Memory, type PlanSize, type SearchHits } from "./api";
import { laneColor, laneName } from "./components";
import { ago, go, pickProject, PlanCard, ProjectPicker, RunCard, useComposer, useEvents, usePoll, useProject } from "./hud";
import { Markdown } from "./Markdown";

type Mode = "chat" | "advisor";
// Auto talks and works; Plan (you turn it on) talks it through and ends in a plan, never a run.
type Act = "auto" | "plan";

/** A choice remembered per browser; a private window just doesn't keep it. */
function useStored<T extends string>(key: string, allowed: readonly T[], fallback: T): [T, (v: T) => void] {
  const [value, setValue] = useState<T>(() => {
    try {
      const got = localStorage.getItem(key) as T | null;
      return got && allowed.includes(got) ? got : fallback;
    } catch {
      return fallback;
    }
  });
  const set = (v: T) => {
    setValue(v);
    try {
      localStorage.setItem(key, v);
    } catch {
      /* not remembered in a private window */
    }
  };
  return [value, set];
}

const KB = (n: number) => (n < 1024 * 1024 ? `${Math.max(1, Math.round(n / 1024))} KB` : `${(n / 1024 / 1024).toFixed(1)} MB`);

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
  // An unsent message belongs to its chat: it survives switching chats, tabs, or a reload.
  const draftKey = `pablo.draft.${mode}.${id || "new"}`;
  const setDraft = (text: string) => {
    setDraftState(text);
    try {
      if (text) localStorage.setItem(draftKey, text);
      else localStorage.removeItem(draftKey);
    } catch {
      /* not kept in a private window */
    }
  };
  useEffect(() => {
    try {
      setDraftState(localStorage.getItem(draftKey) ?? "");
    } catch {
      setDraftState("");
    }
  }, [draftKey]);
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
  const [act, setAct] = useStored<Act>("pablo.act", ["auto", "plan"], "auto");
  const [planSize, setPlanSize] = useStored<PlanSize>("pablo.planSize", ["minor", "major"], "minor");
  const [files, reloadFiles] = usePoll(api.files, 0);
  const [dropping, setDropping] = useState(false);
  const [fileNote, setFileNote] = useState("");
  const picker = useRef<HTMLInputElement>(null);
  const [menu, setMenu] = useState(false);
  const [confirm, setConfirm] = useState<string | null>(null);
  const [draft, setDraftState] = useState("");
  const [hasPlan, setHasPlan] = useState(false);
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
  const canAct = !advisor && lane !== "claude";
  const planning = canAct && act === "plan";
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
    setHasPlan(false);
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

  /** `as`: send in this plan size whatever the switch says (the spec card's Use this). */
  const send = async (text: string, confirmed = false, as?: PlanSize) => {
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
    setHasPlan(false);
    try {
      let cid = conv?.id;
      if (!cid) {
        const made = await api.newConversation(unfiltered ? "auto" : lane, "", advisor ? "" : startIn?.name ?? "");
        cid = made.id;
        go(advisor ? "advisor" : "chat", cid);
      }
      const turn = await api.sayStream(cid, text, { lane, do: null, confirm: confirmed, think: canThink && thinkOn, plan: as ?? (planning ? planSize : null) }, (piece) =>
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

  // A run card's "send to chat" lands here, under anything already typed.
  useComposer((text) => {
    setDraft(draft.trim() ? `${draft.trimEnd()}\n\n${text}` : text);
    box.current?.focus();
  });

  const keep = async (list: FileList | null) => {
    setFileNote("");
    for (const f of Array.from(list ?? [])) {
      try {
        await api.keepFile(f);
      } catch (e) {
        setFileNote(`${f.name}: ${(e as Error).message}`);
      }
    }
    reloadFiles();
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
  // Only a spec that is still the newest reply can be used: once a plan (or anything) follows it,
  // its buttons go.
  const lastReply = [...messages].reverse().find((m) => m.role === "assistant");
  const lastSpecId = lastReply?.content.includes("```spec\n") ? lastReply.id : undefined;
  const showPlan = !advisor && (planning || hasPlan);
  // Until /api/lanes answers, the menu has no options yet: show the choice by its name.
  const current = options.find((o) => o.key === lane) ?? options[0] ?? { key: lane, name: laneName(lane), desc: "", meta: "" };
  const accent = advisor ? "var(--lane-advisor)" : laneColor(lane);
  const frame = advisor ? "violet" : "";

  return (
    <div className={`h-chat ${showPlan ? "planning" : ""}`}>
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
          {messages.length === 0 && pending === null && planning && (
            <div className="h-frame h-pad" style={{ marginTop: 40, gap: 14 }}>
              <span className="h-cap" style={{ color: "var(--sys)" }}>Plan</span>
              <span style={{ fontSize: 20, fontWeight: 600 }}>What are you planning?</span>
              <div className="h-tiles" role="group" aria-label="Plan size">
                {(["minor", "major"] as PlanSize[]).map((p) => (
                  <button key={p} className="h-tile" aria-pressed={planSize === p} onClick={() => (setPlanSize(p), box.current?.focus())}>
                    <span className="h-mono" style={{ fontSize: 12, letterSpacing: ".12em" }}>{p.toUpperCase()}</span>
                    <span style={{ fontSize: 13.5, lineHeight: 1.5 }}>
                      {p === "minor"
                        ? "A change or a small feature. A question or two at most, then a step list."
                        : "Something bigger. An interview, one question at a time, then a spec to approve, then a plan in phases."}
                    </span>
                  </button>
                ))}
              </div>
              <span className="h-dim" style={{ fontSize: 13 }}>Say it in the box below. Nothing runs in Plan: when the plan is right, Approve builds it in Auto, a step at a time.</span>
            </div>
          )}
          {messages.length === 0 && pending === null && !planning && (
            <div className={`h-frame ${frame} h-pad`} style={{ marginTop: 40 }}>
              <span className="h-cap" style={{ color: accent }}>{advisor ? "Ask for the take the team won't give you" : "Start here"}</span>
              <span style={{ fontSize: 14, color: "var(--ink-body)", lineHeight: 1.6 }}>
                {advisor
                  ? "The advisor talks only. It has no stake in your choices and no guardrails. Bring it a plan, a run, or a decision."
                  : "Ask about the code, say what to change, or describe something bigger (\"let's build a tool that…\") and Pablo plans it with you, then builds it a step at a time when you say go. Every change shows its diff and checks here; only the owner can apply it. Turn on Plan to think it through first. Type \"remember: …\" to keep a note."}
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
                  {splitSpec(m.run_id ? m.content.replace(/\n*\[run [^\]]+\]\s*$/, "") : m.content).map((part, i) =>
                    part.spec ? (
                      <SpecCard
                        key={i}
                        spec={part.text}
                        latest={m.id === lastSpecId}
                        onUse={() => (setAct("plan"), setPlanSize("major"), void send("Use this spec. Write the plan.", false, "major"))}
                        onEdit={() => (setDraft(`Here's the spec with my changes:\n\n${part.text}`), box.current?.focus())}
                        onRefine={() => (setDraft("Keep refining: "), box.current?.focus())}
                      />
                    ) : (
                      <Markdown key={i} text={part.text} />
                    ),
                  )}
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
          {!advisor && conv && (
            <div className="h-plan-inline">
              <PlanCard project={conv.project} conversation={conv.id} me={me} onApprove={() => setAct("auto")} />
            </div>
          )}
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
              placeholder={advisor ? "Ask for the honest take…" : planning ? "What are you planning?  (Enter sends · Shift+Enter new line)" : "Ask, or describe a change…  (Enter sends · Shift+Enter new line)"}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  void send(draft);
                }
              }}
            />
            <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "flex-end" }}>
              {/* The thinking switch sits small, right above the model it changes. */}
              <div className="h-model-stack">
                {canThink && (
                  <button
                    className="h-toggle"
                    aria-pressed={thinkOn}
                    onClick={toggleThink}
                    title="The base model thinks before it answers and before it writes code. Slower: often a minute or more."
                  >
                    {thinkOn ? "●" : "○"} thinking
                  </button>
                )}
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
              </div>
              {canAct && (
                <div className="h-seg" role="group" aria-label="Mode">
                  {(["auto", "plan"] as Act[]).map((a) => (
                    <button key={a} aria-pressed={act === a} onClick={() => setAct(a)} title={a === "auto" ? "Talks and works: Pablo decides whether to start a run" : "Talk it through first: ends in a plan, never starts a run"}>
                      {a}
                    </button>
                  ))}
                </div>
              )}
              {planning && (
                <div className="h-seg" role="group" aria-label="Plan size">
                  {(["minor", "major"] as PlanSize[]).map((p) => (
                    <button key={p} aria-pressed={planSize === p} onClick={() => setPlanSize(p)} title={p === "minor" ? "A question or two, then the plan" : "An interview, one question at a time, then a spec and the plan"}>
                      {p}
                    </button>
                  ))}
                </div>
              )}
              <span className="h-dim" style={{ fontSize: 12 }}>
                {advisor ? "Talks only · local · never trained on" : lane === "claude" ? "Asks before sending · never gets your memory" : planning ? "Plans only · nothing runs" : "Runs wait for Apply"}
              </span>
              <button className={`h-btn solid ${advisor ? "violet" : ""}`} style={{ marginLeft: "auto" }} disabled={pending !== null || !draft.trim()} onClick={() => send(draft)} aria-label="Send">
                ↑
              </button>
            </div>
          </div>
        </div>
      </section>

      {showPlan && (
        <aside className="h-plan-col" aria-label="The plan">
          {conv && <PlanCard project={conv.project} conversation={conv.id} me={me} onApprove={() => setAct("auto")} onPlan={(p) => setHasPlan(Boolean(p))} />}
          {!hasPlan && (
            <div className="h-frame h-pad" style={{ gap: 8 }}>
              <span className="h-cap" style={{ color: "var(--sys)" }}>{`Plan · ${planSize}`}</span>
              <span style={{ fontSize: 13.5, lineHeight: 1.55, color: "var(--ink-body)" }}>
                {planSize === "major"
                  ? "The plan builds up here: first the spec you approve, then phases you can watch step by step."
                  : "The plan builds up here as a short list of steps."}
              </span>
              {(files ?? []).length > 0 && <span className="h-faint" style={{ fontSize: 12 }}>It reads your {(files ?? []).length} kept file{(files ?? []).length === 1 ? "" : "s"}.</span>}
            </div>
          )}
        </aside>
      )}

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
          {(files ?? []).map((f: KeptFile) => (
            <div key={f.id} style={{ display: "flex", gap: 6, alignItems: "center" }}>
              <span className="h-mono" style={{ flex: 1, fontSize: 12, color: "var(--ink-body)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={f.name}>
                ▤ {f.name}
              </span>
              <span className="h-mono h-faint" style={{ fontSize: 11 }}>{KB(f.size)}</span>
              <button className="h-faint" style={{ background: "none", border: 0, padding: 0 }} aria-label={`Forget: ${f.name}`} title="Forget" onClick={() => api.forgetFile(f.id).then(reloadFiles)}>
                ×
              </button>
            </div>
          ))}
          <button
            type="button"
            className={`h-drop ${dropping ? "on" : ""}`}
            onClick={() => picker.current?.click()}
            onDragOver={(e) => {
              e.preventDefault();
              setDropping(true);
            }}
            onDragLeave={() => setDropping(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDropping(false);
              void keep(e.dataTransfer.files);
            }}
          >
            📎 Drop files to keep · or browse
            <span className="h-faint" style={{ fontSize: 11 }}>Plans read them. Up to 5 MB each.</span>
          </button>
          <input ref={picker} type="file" multiple hidden onChange={(e) => (void keep(e.target.files), (e.target.value = ""))} />
          {fileNote && <span className="h-error" style={{ fontSize: 12 }}>{fileNote}</span>}
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
export const CONTROL = /^\s*(RUN|REMEMBER|PLAN|PHASE|STEP|BUILD|APPLY|DISCARD|SPEC):|^\s*END SPEC\s*$/;

/** A reply split around its ```spec blocks (a Major plan's spec, drawn as a card). */
function splitSpec(text: string): { spec: boolean; text: string }[] {
  const out: { spec: boolean; text: string }[] = [];
  const re = /```spec\n([\s\S]*?)\n```/g;
  let at = 0;
  for (let m = re.exec(text); m; m = re.exec(text)) {
    if (m.index > at) out.push({ spec: false, text: text.slice(at, m.index).trim() });
    out.push({ spec: true, text: m[1] });
    at = m.index + m[0].length;
  }
  if (at < text.length) out.push({ spec: false, text: text.slice(at).trim() });
  return out.filter((p) => p.text);
}

function SpecCard({ spec, latest, onUse, onEdit, onRefine }: { spec: string; latest: boolean; onUse: () => void; onEdit: () => void; onRefine: () => void }) {
  return (
    <div className="h-frame h-pad" style={{ gap: 10, margin: "4px 0" }}>
      <span className="h-cap" style={{ color: "var(--sys)" }}>Spec</span>
      <Markdown text={spec} />
      {latest && (
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <button className="h-btn solid" onClick={onUse}>USE THIS</button>
          <button className="h-btn" onClick={onEdit}>EDIT</button>
          <button className="h-btn" onClick={onRefine}>KEEP REFINING</button>
        </div>
      )}
    </div>
  );
}
function visibleWhileStreaming(text: string): string {
  return text
    .split("\n")
    .filter((line) => !CONTROL.test(line) && !/^\s*[A-Z]{1,8}$/.test(line)) // a control word still arriving
    .join("\n")
    .trim();
}
