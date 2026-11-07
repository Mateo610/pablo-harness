"""Conversations and memory: multi-turn chat per profile, notes you approve, search.

Everything lives in one SQLite file on Pablo (`~/.pablo/pablo.db`) with FTS5 full-text search.
No embedding model yet: keyword search is offline and good enough until a measurement says
otherwise.

Rules this module keeps:
- Every read and write is scoped to a profile. One profile never sees another's chats or notes.
- A memory note the model suggests is only `proposed` until you keep it. "remember: ..." typed
  by you is kept at once (you said it).
- Memory notes and past conversations never go to a remote lane (Claude). A send to Claude
  needs `confirm=True` every time.
- A chat turn is not a trace and never training data. The runs a chat starts are ordinary runs:
  verified, judged, and only written to your repo by Orchestrator.approve.
- The advisor (Chat's violet tab and its voice) works alone: in any project it talks, plans and
  builds on the no-guardrails model, which also judges its own changes. Its runs never reach Base,
  Best or Claude and never train anything. Without a workspace (`pablo chat`) it only talks.
"""

from __future__ import annotations

import re
import sqlite3
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pablo.config import Lane
from pablo.context import Budget, build_coder_context, clip, tokens
from pablo.gateway import Delta, stream_complete
from pablo.gpu import Priority
from pablo.gpu import job as gpu_job
from pablo.plan import Plan
from pablo.plans import PlanBusy, Plans, plan_text
from pablo.repomap import load_or_build

if TYPE_CHECKING:
    from pablo.gateway import Message
    from pablo.orchestrator import Orchestrator, Run
    from pablo.projects import Workspace

Role = Literal["owner", "member"]
MemoryStatus = Literal["proposed", "kept", "rejected"]
OWNER = "owner"
AUTO = "auto"  # the router picks the run's lane; the coder does the talking
ADVISOR = "advisor"  # the no-guardrails model, on its own

SCHEMA = """
create table if not exists profiles (
  id text primary key, name text not null, role text not null, created_at text not null
);
create table if not exists conversations (
  id text primary key, profile_id text not null references profiles(id),
  title text not null, project text not null default '', lane text not null default 'auto',
  created_at text not null, updated_at text not null
);
create table if not exists messages (
  id integer primary key autoincrement,
  conversation_id text not null references conversations(id) on delete cascade,
  role text not null, lane text not null default '', content text not null,
  run_id text, created_at text not null
);
create table if not exists memories (
  id text primary key, profile_id text not null references profiles(id),
  scope text not null, text text not null, status text not null,
  source_conversation text, created_at text not null, decided_at text
);
create table if not exists usage (
  at text not null, profile_id text not null, lane text not null,
  tokens_in integer not null, tokens_out integer not null
);
create index if not exists conv_by_profile on conversations(profile_id, updated_at);
create index if not exists msg_by_conv on messages(conversation_id, id);
create virtual table if not exists messages_fts using fts5(
  content, content='messages', content_rowid='id'
);
create trigger if not exists messages_ai after insert on messages begin
  insert into messages_fts(rowid, content) values (new.id, new.content);
end;
create trigger if not exists messages_ad after delete on messages begin
  insert into messages_fts(messages_fts, rowid, content) values ('delete', old.id, old.content);
end;
create virtual table if not exists memories_fts using fts5(
  text, id unindexed, profile_id unindexed
);
"""

CHAT_PROMPT = """You are Pablo, a coding assistant running on the user's own server.

First decide what the message asks for:
- A question (what, how, why, which, is there, do we have, explain, what would it take) or
  something to think through: answer it, briefly. Do not start any work, even if a change looks
  useful; you can offer it in words ("Want me to add a test for that?").
- One change, even one that touches several files (a rename and everything that uses it, a fix
  and its test): one RUN line.
- A larger piece of work made of stages that can each be built and tested on its own (a new
  project, a feature with several parts): a plan.

For one change, do not write the code here. Reply with one short sentence and then a line:
RUN: <a complete task for the coder: what to change, where, and how to know it's done>
The harness runs it, checks it, and shows the user the diff. Only one RUN line per reply.

For a plan, one line each:
PLAN: <what done looks like, in one sentence>
STEP: <a complete task for the coder that builds on the steps before it>
Write 3 to 8 STEP lines. Keep every name, signature, file and behavior the user asked for
exactly as they said it. For a new project, the first step sets up the package the user named
and a test runner with one passing test; later steps add a test for what they build. The user
reviews the plan with you; to change it, write the whole plan again. When the user approves
it (go, looks good, build it), reply with one short sentence and a line: BUILD: go
Don't write the plan again then. Each step waits for the user's approval before the next starts.

When the user says to apply the change that is waiting for them, add a line: APPLY: it
When they say to throw it away, add a line: DISCARD: it
Never write APPLY or DISCARD unless the user just said so.

When the user tells you something lasting about how they work or what the project needs,
add a line: REMEMBER: <the fact, in a few words>. They decide whether it's kept."""

ADVISOR_PROMPT = """You are the advisor: a blunt second opinion with no stake in the team's
choices. Say what you actually think, including what the user doesn't want to hear. You never
write or change code and you never score anything. Keep it short unless asked to go deep."""

ADVISOR_BUILDS = (
    "You are the advisor, running on the user's own server with no guardrails: blunt, with no "
    "stake in anyone's choices, and you say what you actually think, including what the user "
    "doesn't want to hear. You also do the work yourself: you write the changes and review "
    "them. No other model is involved.\n\n" + CHAT_PROMPT.split("\n\n", 1)[1]
)

SPOKEN = """

This turn is a voice call: your reply is read aloud. Talk like a person on a call: one to three
short sentences, no code, no lists, no markdown, no file paths unless asked. A question still
gets only an answer: if you offer to do something, wait for a yes before any RUN line. The RUN,
PLAN, STEP and REMEMBER lines still work and are never read aloud, so a larger piece of work
still gets its whole plan, written in full, after your spoken sentences."""

_RUN = re.compile(r"^\s*RUN:\s*(.+?)\s*$", re.M)
_PLAN = re.compile(r"^\s*PLAN:\s*(.+?)\s*$", re.M)
_STEP = re.compile(r"^\s*STEP:\s*(.+?)\s*$", re.M)
_BUILD = re.compile(r"^\s*BUILD:.*$", re.M)
_APPLY = re.compile(r"^\s*APPLY:.*$", re.M)
_DISCARD = re.compile(r"^\s*DISCARD:.*$", re.M)
_CONTROL = re.compile(r"^\s*(PLAN|STEP|BUILD|APPLY|DISCARD):.*$\n?", re.M)
# The model acts on these only when the user's own words say so: a model that writes APPLY on
# its own can't merge anything.
_SAYS_GO = re.compile(
    r"\b(go|go ahead|start|build( it)?|do it|yes|yeah|yep|sure|ok(ay)?|let'?s"
    r"|ship it|sounds good)\b",
    re.I,
)
_SAYS_APPLY = re.compile(
    r"\b(apply|approve|merge|ship|accept|keep|land|commit|yes|yeah|yep|do it|go ahead"
    r"|looks good|looks great|perfect|great)\b",
    re.I,
)
_CHANGE_WORDS = re.compile(
    r"\b(but|instead|change|except|without|drop|remove|add|rename|split)\b", re.I
)
# A question, not a request phrased as one ("can you add…?" is a request).
_QUESTION = re.compile(
    r"^\s*(what|what's|whats|how|why|which|where|when|who|is|are|does|do|did|should|would|"
    r"explain|tell me|describe)\b",
    re.I,
)
_REQUEST = re.compile(r"\b(can you|could you|would you|will you|please|go ahead|do it)\b", re.I)


def is_question(text: str) -> bool:
    t = text.strip()
    return bool(_QUESTION.search(t)) and not _REQUEST.search(t)


_SAYS_DISCARD = re.compile(r"\b(discard|reject|throw|toss|drop|scrap|undo|no|nope|don'?t)\b", re.I)
_REMEMBER = re.compile(r"^\s*REMEMBER:\s*(.+?)\s*$", re.M)
_SAID_REMEMBER = re.compile(r"^\s*remember(?:\s+that)?\s*[:,-]?\s+(.+)$", re.I | re.S)
_WORD = re.compile(r"[A-Za-z0-9_]{3,}")
_STOP = frozenset(
    [
        "the",
        "and",
        "for",
        "with",
        "that",
        "this",
        "you",
        "are",
        "was",
        "have",
        "what",
        "how",
        "can",
        "not",
        "but",
        "from",
        "your",
        "about",
        "into",
        "should",
        "would",
        "could",
        "there",
        "their",
        "them",
        "then",
        "than",
        "when",
        "where",
        "which",
        "while",
        "will",
        "just",
        "also",
    ]
)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def fts_query(text: str, limit: int = 12) -> str:
    """Any user text as a safe FTS5 query: its distinct words, OR'ed, each quoted."""
    words: list[str] = []
    for w in _WORD.findall(text.lower()):
        if w not in _STOP and w not in words:
            words.append(w)
    return " OR ".join(f'"{w}"' for w in words[:limit])


@dataclass
class Profile:
    id: str
    name: str
    role: Role
    created_at: str = ""


@dataclass
class Conversation:
    id: str
    profile_id: str
    title: str
    project: str = ""
    lane: str = AUTO
    created_at: str = ""
    updated_at: str = ""


@dataclass
class ChatMessage:
    id: int
    conversation_id: str
    role: str
    content: str
    lane: str = ""
    run_id: str | None = None
    created_at: str = ""


@dataclass
class Memory:
    id: str
    profile_id: str
    scope: str  # "profile" or "project:<name>"
    text: str
    status: MemoryStatus
    source_conversation: str | None = None
    created_at: str = ""
    decided_at: str | None = None


class Store:
    """The SQLite file. Thread-safe: the API calls it from a thread pool."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("pragma foreign_keys = on")
        self._db.execute("pragma journal_mode = wal")
        self._db.executescript(SCHEMA)

    def _q(self, sql: str, args: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        with self._lock:
            rows = self._db.execute(sql, args).fetchall()
            self._db.commit()
            return rows

    # profiles -------------------------------------------------------------------------------

    def ensure_profile(self, pid: str, name: str = "", role: Role = "member") -> Profile:
        self._q(
            "insert or ignore into profiles(id, name, role, created_at) values (?, ?, ?, ?)",
            (pid, name or pid, role, _now()),
        )
        return self.profile(pid)

    def profile(self, pid: str) -> Profile:
        rows = self._q("select * from profiles where id = ?", (pid,))
        if not rows:
            raise KeyError(f"no profile {pid}")
        return Profile(**dict(rows[0]))

    # conversations --------------------------------------------------------------------------

    def create(
        self, profile: str, title: str = "", *, lane: str = AUTO, project: str = ""
    ) -> Conversation:
        now = _now()
        conv = Conversation(uuid.uuid4().hex[:10], profile, title or "New conversation",
                            project, lane, now, now)  # fmt: skip
        self._q(
            "insert into conversations values (?, ?, ?, ?, ?, ?, ?)",
            (conv.id, conv.profile_id, conv.title, conv.project, conv.lane, now, now),
        )
        return conv

    def conversation(self, cid: str, profile: str) -> Conversation:
        rows = self._q(
            "select * from conversations where id = ? and profile_id = ?", (cid, profile)
        )
        if not rows:
            raise KeyError(f"no conversation {cid}")
        return Conversation(**dict(rows[0]))

    def conversations(self, profile: str, limit: int = 50) -> list[Conversation]:
        rows = self._q(
            "select * from conversations where profile_id = ? order by updated_at desc limit ?",
            (profile, limit),
        )
        return [Conversation(**dict(r)) for r in rows]

    def rename(self, cid: str, profile: str, title: str) -> None:
        self.conversation(cid, profile)
        self._q("update conversations set title = ? where id = ?", (title[:120], cid))

    def delete(self, cid: str, profile: str) -> None:
        """Gone for good: messages, their search index, and notes that came from it."""
        self.conversation(cid, profile)
        for row in self._q(
            "select id from memories where source_conversation = ? and profile_id = ?",
            (cid, profile),
        ):
            self.forget(row["id"], profile)
        self._q("delete from messages where conversation_id = ?", (cid,))
        self._q("delete from conversations where id = ?", (cid,))

    def messages(self, cid: str) -> list[ChatMessage]:
        rows = self._q("select * from messages where conversation_id = ? order by id", (cid,))
        return [ChatMessage(**dict(r)) for r in rows]

    def add_message(
        self, cid: str, role: str, content: str, *, lane: str = "", run_id: str | None = None
    ) -> ChatMessage:
        now = _now()
        with self._lock:
            cur = self._db.execute(
                "insert into messages(conversation_id, role, lane, content, run_id, created_at) "
                "values (?, ?, ?, ?, ?, ?)",
                (cid, role, lane, content, run_id, now),
            )
            self._db.execute("update conversations set updated_at = ? where id = ?", (now, cid))
            self._db.commit()
            mid = int(cur.lastrowid or 0)
        return ChatMessage(mid, cid, role, content, lane, run_id, now)

    def record_usage(self, profile: str, lane: str, tokens_in: int, tokens_out: int) -> None:
        """Token counts only: chat text is never copied out of the conversation."""
        self._q("insert into usage values (?, ?, ?, ?, ?)",
                (_now(), profile, lane, tokens_in, tokens_out))  # fmt: skip

    def conversation_for_run(self, run_id: str, profile: str) -> tuple[str, str] | None:
        """(conversation id, lane) of your conversation that started this run, if any."""
        rows = self._q(
            "select c.id, c.lane from messages m join conversations c on c.id = m.conversation_id "
            "where m.run_id = ? and c.profile_id = ? limit 1",
            (run_id, profile),
        )
        return (rows[0]["id"], rows[0]["lane"]) if rows else None

    def usage_since(self, stamp: str) -> list[dict[str, Any]]:
        return [dict(r) for r in self._q("select * from usage where at >= ?", (stamp,))]

    # memory ---------------------------------------------------------------------------------

    def remember(
        self,
        profile: str,
        text: str,
        *,
        scope: str = "profile",
        status: MemoryStatus = "kept",
        source: str | None = None,
    ) -> Memory:
        now = _now()
        mem = Memory(uuid.uuid4().hex[:10], profile, scope, text.strip()[:500], status, source,
                     now, now if status != "proposed" else None)  # fmt: skip
        self._q(
            "insert into memories values (?, ?, ?, ?, ?, ?, ?, ?)",
            (mem.id, profile, scope, mem.text, status, source, now, mem.decided_at),
        )
        if status == "kept":
            self._q("insert into memories_fts(text, id, profile_id) values (?, ?, ?)",
                    (mem.text, mem.id, profile))  # fmt: skip
        return mem

    def memories(
        self, profile: str, *, status: MemoryStatus | None = None, scopes: list[str] | None = None
    ) -> list[Memory]:
        sql, args = "select * from memories where profile_id = ?", [profile]
        if status:
            sql, args = sql + " and status = ?", [*args, status]
        if scopes:
            sql += f" and scope in ({','.join('?' * len(scopes))})"
            args += scopes
        rows = self._q(sql + " order by created_at desc", tuple(args))
        return [Memory(**dict(r)) for r in rows]

    def decide(self, mid: str, profile: str, status: MemoryStatus) -> Memory:
        rows = self._q("select * from memories where id = ? and profile_id = ?", (mid, profile))
        if not rows:
            raise KeyError(f"no memory {mid}")
        mem = Memory(**dict(rows[0]))
        self._q("update memories set status = ?, decided_at = ? where id = ?",
                (status, _now(), mid))  # fmt: skip
        self._q("delete from memories_fts where id = ?", (mid,))
        if status == "kept":
            self._q("insert into memories_fts(text, id, profile_id) values (?, ?, ?)",
                    (mem.text, mid, profile))  # fmt: skip
        mem.status = status
        return mem

    def forget(self, mid: str, profile: str) -> None:
        """Deleted, not marked: nothing of it stays in the file or the index."""
        self._q("delete from memories_fts where id = ? and profile_id = ?", (mid, profile))
        self._q("delete from memories where id = ? and profile_id = ?", (mid, profile))

    # search ---------------------------------------------------------------------------------

    def search(self, profile: str, text: str, limit: int = 20) -> dict[str, list[dict[str, Any]]]:
        q = fts_query(text)
        if not q:
            return {"messages": [], "memories": []}
        msgs = self._q(
            "select m.id, m.conversation_id, m.role, m.content, m.created_at, c.title "
            "from messages_fts f join messages m on m.id = f.rowid "
            "join conversations c on c.id = m.conversation_id "
            "where messages_fts match ? and c.profile_id = ? order by bm25(messages_fts) limit ?",
            (q, profile, limit),
        )
        mems = self._q(
            "select id, text from memories_fts where memories_fts match ? and profile_id = ? "
            "order by bm25(memories_fts) limit ?",
            (q, profile, limit),
        )
        return {"messages": [dict(r) for r in msgs], "memories": [dict(r) for r in mems]}

    def recall(self, profile: str, text: str, *, exclude: str, k: int = 3) -> list[dict[str, Any]]:
        """The best-matching lines from your other conversations."""
        hits = self.search(profile, text, limit=k * 4)["messages"]
        return [h for h in hits if h["conversation_id"] != exclude][:k]

    def relevant_memories(self, profile: str, project: str, text: str, k: int = 12) -> list[str]:
        kept = self.memories(profile, status="kept", scopes=["profile", f"project:{project}"])
        if len(kept) <= k:
            return [m.text for m in kept]
        q = fts_query(text)
        ranked = (
            self._q(
                "select text from memories_fts where memories_fts match ? and profile_id = ? "
                "order by bm25(memories_fts) limit ?",
                (q, profile, k),
            )
            if q
            else []
        )
        out = [r["text"] for r in ranked]
        for m in kept:  # newest first fills the rest
            if len(out) >= k:
                break
            if m.text not in out:
                out.append(m.text)
        return out


@dataclass
class Turn:
    conversation: Conversation
    user: ChatMessage
    reply: ChatMessage
    run: dict[str, Any] | None = None
    proposed: list[Memory] = field(default_factory=list)
    kept: list[Memory] = field(default_factory=list)
    shared_memory: bool = False  # whether notes and recall went into the prompt
    plan: dict[str, Any] | None = None  # the plan, when this turn drafted or started one
    decided: dict[str, Any] | None = None  # the run applied or discarded by this turn

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class Chat:
    """One conversation turn: talk to a lane, maybe start a run, maybe propose a note."""

    HISTORY_MIN = 1500
    CHAT_MAX_TOKENS = 1500
    MESSAGE_MAX = 3000
    CONTEXT = Budget(total=5000, files=3200, tree=700, max_files=4)

    def __init__(
        self,
        orch: Orchestrator,
        store: Store | None = None,
        *,
        background: bool = False,
        workspace: Workspace | None = None,
        plans: Plans | None = None,
    ) -> None:
        """`background`: runs a turn starts are worked on a thread (the API); else inline.
        `workspace`: conversations in other projects talk to that project's orchestrator."""
        self.orch = orch
        self.workspace = workspace
        self.plans = plans
        self.background = background
        self.store = store or Store(orch.settings.home / "pablo.db")
        self.store.ensure_profile(OWNER, "Owner", "owner")
        self.project = orch.repo.name

    def start(
        self, profile: str = OWNER, title: str = "", lane: str = AUTO, project: str = ""
    ) -> Conversation:
        project = project or self.project
        orch = self._orch_named(project)  # unknown projects fail here
        if self.unfiltered(project):
            lane = AUTO  # one model drives the whole project
        self._lane(lane, orch)  # unknown lanes fail here, not on the first message
        return self.store.create(profile, title, lane=lane, project=project)

    def _orch_named(self, project: str) -> Orchestrator:
        if self.workspace is None or project in ("", self.project):
            return self.orch
        return self.workspace.orch(project)

    def is_advisor(self, conv: Conversation) -> bool:
        """An Advisor conversation that can build: it needs the workspace to get its own team."""
        return conv.lane == ADVISOR and self.workspace is not None

    def advisor_orch(self, conv: Conversation) -> Orchestrator:
        assert self.workspace is not None
        try:
            return self.workspace.advisor_orch(conv.project or self.project)
        except KeyError:  # the project's folder is gone; work in the default one
            return self.workspace.advisor_orch(self.project)

    def orch_for(self, conv: Conversation) -> Orchestrator:
        try:
            if self.is_advisor(conv):
                return self.advisor_orch(conv)
            return self._orch_named(conv.project)
        except KeyError:  # the project's folder is gone; talk about the default one
            return self.orch

    def unfiltered(self, project: str) -> bool:
        if self.workspace is None:
            return False
        try:
            return self.workspace.project(project or self.project).mode == "unfiltered"
        except KeyError:
            return False

    def _lane(self, name: str, orch: Orchestrator | None = None) -> Lane:
        return (orch or self.orch).settings.lane("coder" if name == AUTO else name)

    def say(
        self,
        cid: str,
        text: str,
        *,
        profile: str = OWNER,
        lane: str | None = None,
        do: bool | None = None,
        confirm: bool = False,
        on_delta: Delta | None = None,
        spoken: bool = False,
        think: bool = False,
    ) -> Turn:
        """`do`: True starts a run with your words as the task, False only talks, None lets
        the model decide (a RUN line). `confirm` is required for a remote lane, every time.
        `think`: extended thinking, for Base and Hybrid in chat: the reply, the runs and the
        plan it starts use the coder's thinking variant. Never spoken (it's slow)."""
        conv = self.store.conversation(cid, profile)
        # The Advisor: its own tab (conversation lane), or "No guardrails" picked in Chat.
        advisor = self.workspace is not None and (lane or conv.lane) == ADVISOR
        orch = self.advisor_orch(conv) if advisor else self.orch_for(conv)
        # The Advisor builds the way an unfiltered project does: one model, by name, alone.
        unfiltered = self.unfiltered(conv.project) or advisor
        name = AUTO if unfiltered else (lane or conv.lane)
        label = ADVISOR if advisor else name  # what the turn is filed under
        cfg = self._lane(name, orch)
        think = think and not spoken and not unfiltered and cfg.thinking is not None
        if think and cfg.thinking is not None:
            t = cfg.thinking
            cfg = cfg.model_copy(update={"model": t.model, "max_tokens": t.max_tokens,
                                         "timeout_s": t.timeout_s})  # fmt: skip
        remote = cfg.kind == "remote" or cfg.requires_approval
        if remote and not confirm:
            raise PermissionError(f"'{name}' leaves Pablo: confirm this send first")
        # Runs start from local lanes only. Claude is reached by escalating a run, never here.
        can_run = (name == AUTO or cfg.writes_code) and not remote
        if do and not can_run:
            raise ValueError(f"'{name}' talks only; it can't start a run")

        history = self.store.messages(cid)
        user = self.store.add_message(cid, "user", text, lane=label)
        if not history and conv.title == "New conversation":
            title = " ".join(text.split())[:60]
            self.store.rename(cid, profile, title)
            conv.title = title

        said = _SAID_REMEMBER.match(text)
        if said:
            mem = self.store.remember(profile, said.group(1), source=cid)
            reply = self.store.add_message(cid, "assistant", f"Kept: {mem.text}", lane=label)
            return Turn(conv, user, reply, kept=[mem])

        # Spoken turns rank notes without the message, so the list (and the prompt) stays put.
        notes = [] if remote else self.store.relevant_memories(
            profile, conv.project, "" if spoken else text
        )  # fmt: skip
        if do:
            run = self._run(text, name, notes, profile, orch, unfiltered, think)
            body = self._run_line(run)
            reply = self.store.add_message(cid, "assistant", body, lane=label, run_id=run.id)
            return Turn(conv, user, reply, run=run.to_dict(), shared_memory=bool(notes))

        messages = self._prompt(
            profile, conv, cfg, name, history, text, notes, remote, spoken, orch, advisor
        )
        sampling: dict[str, Any] = {
            "temperature": cfg.temperature,
            "top_p": cfg.top_p,
            # Thinking spends its tokens before the reply starts: give it the variant's room.
            "max_tokens": cfg.max_tokens if think else min(cfg.max_tokens, self.CHAT_MAX_TOKENS),
            "timeout_s": cfg.timeout_s,
        }
        with gpu_job(Priority.VOICE if spoken else Priority.CHAT, profile):
            done = (
                stream_complete(orch.backend, cfg.model, messages, on_delta, **sampling)
                if on_delta
                else orch.backend.complete(cfg.model, messages, **sampling)
            )
        answer = done.text
        self.store.record_usage(profile, label, done.prompt_tokens, done.completion_tokens)
        task = next((m.group(1) for m in _RUN.finditer(answer)), "") if can_run else ""
        facts = [m.group(1) for m in _REMEMBER.finditer(answer)]
        visible = _CONTROL.sub("", _REMEMBER.sub("", _RUN.sub("", answer))).strip()
        proposed = [
            self.store.remember(profile, f, status="proposed", source=cid) for f in facts[:3]
        ]
        extra: list[str] = []
        plan_obj: Plan | None = None
        decided: Run | None = None
        if can_run and do is None and is_question(text) and (task or _STEP.search(answer)):
            # A question gets an answer. The model offered work anyway: keep the offer, start
            # nothing, and let a "yes, do it" start it on the next turn.
            task = ""
            answer = _STEP.sub("", _PLAN.sub("", answer))
            extra.append(
                '(Nothing started: that was a question. Say "do it" if you want the change.)'
            )
        consent_only = self._consent_only(conv, text)
        if can_run and do is not False:
            if consent_only and _STEP.search(answer):
                answer = _STEP.sub("", _PLAN.sub("", answer))  # "go" isn't a new plan: keep it
            plan_obj = self._planning(conv, label, profile, answer, extra, think, request=text)
            if plan_obj is not None:
                task = ""  # a plan takes the place of a single run
            if consent_only and not _BUILD.search(answer):
                answer += "\nBUILD: go"  # the user said go; the model only restated it
            if _APPLY.search(answer) and _SAYS_APPLY.search(text):
                decided = self._decide_waiting(conv, profile, approve=True, out=extra)
            elif _DISCARD.search(answer) and _SAYS_DISCARD.search(text):
                decided = self._decide_waiting(conv, profile, approve=False, out=extra)
            if self.plans and _BUILD.search(answer) and _SAYS_GO.search(text):
                plan_obj = self._build(conv, profile, extra) or plan_obj
        run_obj = None
        if task and do is not False:
            run_obj = self._run(task, name, notes, profile, orch, unfiltered, think)
            extra.append(self._run_line(run_obj))
        visible = "\n\n".join(x for x in [visible, *extra] if x).strip()
        reply = self.store.add_message(
            cid, "assistant", visible or "(no reply)", lane=label,
            run_id=run_obj.id if run_obj else None,
        )  # fmt: skip
        return Turn(conv, user, reply, run=run_obj.to_dict() if run_obj else None,
                    proposed=proposed, shared_memory=bool(notes) and not remote,
                    plan=plan_obj.model_dump(mode="json") if plan_obj else None,
                    decided=decided.to_dict() if decided else None)  # fmt: skip

    # plan mode and spoken approvals ------------------------------------------------------

    def _consent_only(self, conv: Conversation, text: str) -> bool:
        """A short "go" to a draft plan: build it, and don't let a restated plan replace it."""
        if self.plans is None or len(text.split()) > 8 or not _SAYS_GO.search(text):
            return False
        if _CHANGE_WORDS.search(text):
            return False  # "go, but make step 2 smaller" is a change to the plan
        try:
            plan = self.plans.get(conv.project)
        except KeyError:
            return False
        return plan is not None and plan.state in ("draft", "paused")

    def _planning(
        self,
        conv: Conversation,
        lane: str,
        profile: str,
        answer: str,
        out: list[str],
        think: bool = False,
        request: str = "",
    ) -> Plan | None:
        steps = [m.group(1) for m in _STEP.finditer(answer)]
        if not steps or self.plans is None:
            return None
        goal = next((m.group(1) for m in _PLAN.finditer(answer)), "")
        try:
            plan = self.plans.draft(conv.project, goal, steps, lane=lane, by=profile,
                                    conversation=conv.id, think=think, request=request)  # fmt: skip
        except (PlanBusy, ValueError) as exc:
            out.append(f"(The plan wasn't changed: {exc}.)")
            return None
        out.append(plan_text(plan))
        return plan

    def _build(self, conv: Conversation, profile: str, out: list[str]) -> Plan | None:
        assert self.plans is not None
        try:
            plan = self.plans.go(conv.project, by=profile)
        except (KeyError, ValueError, PlanBusy) as exc:
            out.append(f"(Not building yet: {str(exc).strip(chr(39))}.)")
            return None
        step = next((t for t in Plans.steps(plan) if t.status in ("active", "needs_you")), None)
        if step is not None:
            out.append(
                f"Building step {step.id}: {step.title}. It'll wait for you when it's ready."
            )
        return plan

    def _state_note(self, conv: Conversation, cfg: Lane, name: str, remote: bool) -> str:
        """The project's plan and what waits for the user, ahead of their message. It rides on
        the message (not the system prompt) so the cached prefix stays the same."""
        if remote or not (name == AUTO or cfg.writes_code):
            return ""
        parts = []
        if self.plans is not None:
            try:
                summary = self.plans.summary(conv.project)
            except KeyError:
                summary = ""
            if summary:
                parts.append("The project's plan:\n" + summary)
        waiting = self._waiting(conv)
        if waiting:
            parts.append("Waiting for the user's decision: " + "; ".join(
                f"run {r.id} ({r.state.replace('_', ' ')}"
                + (f", certainty {r.certainty:.2f}" if r.certainty is not None else "")
                + f"): {clip(r.task.splitlines()[0], 16)}" for r in waiting[:3]))  # fmt: skip
        if not parts:
            return ""
        return "[Pablo, not the user: " + "\n".join(parts) + "]\n\n"

    def _waiting(self, conv: Conversation) -> list[Run]:
        """Changes waiting for a decision that this conversation (or its plan) started."""
        orch = self.orch_for(conv)
        ids = [m.run_id for m in reversed(self.store.messages(conv.id)) if m.run_id]
        if self.plans is not None:
            try:
                plan = self.plans.get(conv.project)
            except KeyError:
                plan = None
            if plan is not None:
                ids = [t.run for t in Plans.steps(plan) if t.run and t.status == "needs_you"] + ids
        seen: list[Run] = []
        recent = orch.recent(200)
        for rid in ids:
            try:
                run = orch.get(rid)
            except KeyError:
                continue
            family = [run] + [
                r for r in recent if run.group and r.group == run.group and r is not run
            ]
            for r in family:
                if r.state in ("ready", "needs_you") and r not in seen:
                    seen.append(r)
        return sorted(seen, key=lambda r: r.state != "ready")  # a ready one first

    def _decide_waiting(
        self, conv: Conversation, profile: str, *, approve: bool, out: list[str]
    ) -> Run | None:
        if profile != OWNER:
            out.append("(Only the owner can apply or discard changes.)")
            return None
        waiting = self._waiting(conv)
        if not waiting:
            out.append("(Nothing is waiting for you here.)")
            return None
        run = waiting[0]
        orch = self.workspace.for_run(run.id) if self.workspace else self.orch
        try:
            done = orch.approve(run.id) if approve else orch.reject(run.id)
        except (ValueError, RuntimeError) as exc:
            out.append(f"(Couldn't {'apply' if approve else 'discard'} it: {exc}.)")
            return None
        word = "Applied" if approve else "Discarded"
        out.append(f"{word}: {clip(run.task.splitlines()[0], 20)} [run {run.id}].")
        if self.plans is not None:
            self.plans.after(done)
        return done

    def _run(
        self,
        task: str,
        lane: str,
        notes: list[str],
        profile: str,
        orch: Orchestrator | None = None,
        unfiltered: bool = False,
        think: bool = False,
    ) -> Run:
        orch = orch or self.orch
        if notes:
            task += "\n\nNotes from the user (keep to these):\n" + "\n".join(
                f"- {n}" for n in notes[:6]
            )
        # Hybrid (AUTO): the router picks, checks and the judge review, and deep retries a
        # broken change right away. A model you picked by name answers alone: no retry.
        target, retry = (None, "now") if lane == AUTO else (lane, "off")
        if unfiltered:  # the one model, by name: no router, no retry elsewhere
            target, retry = "coder", "off"
        if self.background:
            return orch.start_background(task, lane=target, by=profile, retry=retry, think=think)
        return orch.start(task, lane=target, by=profile, retry=retry, think=think)

    @staticmethod
    def _run_line(run: Run) -> str:
        cert = f" · certainty {run.certainty:.2f}" if run.certainty is not None else ""
        return f"[run {run.id} · {run.lane} · {run.state.replace('_', ' ')}{cert}]"

    GLANCE_TOKENS = 1200

    def repo_glance(self, orch: Orchestrator | None = None) -> str:
        """The repo map, clipped: the same text until the repo changes."""
        orch = orch or self.orch
        try:
            repo_map = load_or_build(orch.repo, orch.settings.home)
        except Exception:  # the map is a nicety; a voice turn must not fail on it
            return "(map unavailable)"
        return clip(repo_map.render(limit=160), self.GLANCE_TOKENS)

    def _prompt(
        self,
        profile: str,
        conv: Conversation,
        cfg: Lane,
        name: str,
        history: list[ChatMessage],
        text: str,
        notes: list[str],
        remote: bool,
        spoken: bool = False,
        orch: Orchestrator | None = None,
        advisor: bool = False,
    ) -> list[Message]:
        orch = orch or self.orch
        builds = name == AUTO or cfg.writes_code
        system = (ADVISOR_BUILDS if advisor else CHAT_PROMPT) if builds else ADVISOR_PROMPT
        parts = [system + (SPOKEN if spoken else "")]
        if not remote:
            who = self.store.profile(profile).name
            if notes:
                parts.append(f"What {who} has told you to keep in mind:\n" + "\n".join(
                    f"- {n}" for n in notes))  # fmt: skip
            if spoken:
                # A voice call keeps the system prompt identical turn to turn, so llama.cpp
                # reuses its cache and the first word comes sooner: no per-message recall or
                # file picking, just a compact map of the repo. A run still gets full context.
                if name == AUTO or cfg.writes_code:
                    parts.append("The repo at a glance:\n" + self.repo_glance(orch))
            else:
                recalled = self.store.recall(profile, text, exclude=conv.id)
                if recalled:
                    parts.append("From earlier conversations:\n" + "\n".join(
                        f"- ({h['title']}) {h['role']}: {clip(h['content'], 80)}" for h in recalled
                    ))  # fmt: skip
            if (name == AUTO or cfg.writes_code) and not spoken:
                context = build_coder_context(
                    orch.repo, text, budget=self.CONTEXT, home=orch.settings.home
                )
                parts.append("The repo, for this message:\n" + context)
        sys_text = "\n\n".join(parts)
        sent = min(tokens(text), self.MESSAGE_MAX)
        room = max(self.HISTORY_MIN, cfg.context_tokens - tokens(sys_text) - sent)
        kept: list[Message] = []
        for msg in reversed(history):
            cost = tokens(msg.content)
            if cost > room:
                break
            kept.insert(0, {"role": msg.role, "content": msg.content})
            room -= cost
        return [
            {"role": "system", "content": sys_text},
            *kept,
            # A pasted log can't overflow the window; the stored message stays whole.
            {
                "role": "user",
                "content": self._state_note(conv, cfg, name, remote) + clip(text, self.MESSAGE_MAX),
            },
        ]
