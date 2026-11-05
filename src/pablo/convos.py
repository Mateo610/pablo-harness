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
- The advisor talks only: it cannot start a run.
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

if TYPE_CHECKING:
    from pablo.gateway import Message
    from pablo.orchestrator import Orchestrator, Run

Role = Literal["owner", "member"]
MemoryStatus = Literal["proposed", "kept", "rejected"]
OWNER = "owner"
AUTO = "auto"  # the router picks the run's lane; the coder does the talking

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
Answer questions about the code and the plan directly and briefly.

When the user wants a change made to the code, do not write the code here. Reply with one
short sentence and then a line:
RUN: <a complete task for the coder: what to change, where, and how to know it's done>
The harness runs it, checks it, and shows the user the diff. Only one RUN line per reply.

When the user tells you something lasting about how they work or what the project needs,
add a line: REMEMBER: <the fact, in a few words>. They decide whether it's kept."""

ADVISOR_PROMPT = """You are the advisor: a blunt second opinion with no stake in the team's
choices. Say what you actually think, including what the user doesn't want to hear. You never
write or change code and you never score anything. Keep it short unless asked to go deep."""

SPOKEN = """

This turn is a voice call: your reply is read aloud. Talk like a person on a call: one to three
short sentences, no code, no lists, no markdown, no file paths unless asked. The RUN and
REMEMBER lines still work and are never read aloud."""

_RUN = re.compile(r"^\s*RUN:\s*(.+?)\s*$", re.M)
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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class Chat:
    """One conversation turn: talk to a lane, maybe start a run, maybe propose a note."""

    HISTORY_MIN = 1500
    CHAT_MAX_TOKENS = 1500
    MESSAGE_MAX = 3000
    CONTEXT = Budget(total=5000, files=3200, tree=700, max_files=4)

    def __init__(
        self, orch: Orchestrator, store: Store | None = None, *, background: bool = False
    ) -> None:
        """`background`: runs a turn starts are worked on a thread (the API); else inline."""
        self.orch = orch
        self.background = background
        self.store = store or Store(orch.settings.home / "pablo.db")
        self.store.ensure_profile(OWNER, "Owner", "owner")
        self.project = orch.repo.name

    def start(self, profile: str = OWNER, title: str = "", lane: str = AUTO) -> Conversation:
        self._lane(lane)  # unknown lanes fail here, not on the first message
        return self.store.create(profile, title, lane=lane, project=self.project)

    def _lane(self, name: str) -> Lane:
        return self.orch.settings.lane("coder" if name == AUTO else name)

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
    ) -> Turn:
        """`do`: True starts a run with your words as the task, False only talks, None lets
        the model decide (a RUN line). `confirm` is required for a remote lane, every time."""
        conv = self.store.conversation(cid, profile)
        name = lane or conv.lane
        cfg = self._lane(name)
        remote = cfg.kind == "remote" or cfg.requires_approval
        if remote and not confirm:
            raise PermissionError(f"'{name}' leaves Pablo: confirm this send first")
        # Runs start from local lanes only. Claude is reached by escalating a run, never here.
        can_run = (name == AUTO or cfg.writes_code) and not remote
        if do and not can_run:
            raise ValueError(f"'{name}' talks only; it can't start a run")

        history = self.store.messages(cid)
        user = self.store.add_message(cid, "user", text, lane=name)
        if not history and conv.title == "New conversation":
            title = " ".join(text.split())[:60]
            self.store.rename(cid, profile, title)
            conv.title = title

        said = _SAID_REMEMBER.match(text)
        if said:
            mem = self.store.remember(profile, said.group(1), source=cid)
            reply = self.store.add_message(cid, "assistant", f"Kept: {mem.text}", lane=name)
            return Turn(conv, user, reply, kept=[mem])

        notes = [] if remote else self.store.relevant_memories(profile, conv.project, text)
        if do:
            run = self._run(text, name, notes, profile)
            body = self._run_line(run)
            reply = self.store.add_message(cid, "assistant", body, lane=name, run_id=run.id)
            return Turn(conv, user, reply, run=run.to_dict(), shared_memory=bool(notes))

        messages = self._prompt(profile, conv, cfg, name, history, text, notes, remote, spoken)
        sampling: dict[str, Any] = {
            "temperature": cfg.temperature,
            "top_p": cfg.top_p,
            "max_tokens": min(cfg.max_tokens, self.CHAT_MAX_TOKENS),
            "timeout_s": cfg.timeout_s,
        }
        done = (
            stream_complete(self.orch.backend, cfg.model, messages, on_delta, **sampling)
            if on_delta
            else self.orch.backend.complete(cfg.model, messages, **sampling)
        )
        answer = done.text
        self.store.record_usage(profile, name, done.prompt_tokens, done.completion_tokens)
        task = next((m.group(1) for m in _RUN.finditer(answer)), "") if can_run else ""
        facts = [m.group(1) for m in _REMEMBER.finditer(answer)]
        visible = _REMEMBER.sub("", _RUN.sub("", answer)).strip()
        proposed = [
            self.store.remember(profile, f, status="proposed", source=cid) for f in facts[:3]
        ]
        run_obj = None
        if task and do is not False:
            run_obj = self._run(task, name, notes, profile)
            visible = f"{visible}\n\n{self._run_line(run_obj)}".strip()
        reply = self.store.add_message(
            cid, "assistant", visible or "(no reply)", lane=name,
            run_id=run_obj.id if run_obj else None,
        )  # fmt: skip
        return Turn(conv, user, reply, run=run_obj.to_dict() if run_obj else None,
                    proposed=proposed, shared_memory=bool(notes) and not remote)  # fmt: skip

    def _run(self, task: str, lane: str, notes: list[str], profile: str) -> Run:
        if notes:
            task += "\n\nNotes from the user (keep to these):\n" + "\n".join(
                f"- {n}" for n in notes[:6]
            )
        target = None if lane == AUTO else lane
        if self.background:
            return self.orch.start_background(task, lane=target, by=profile)
        return self.orch.start(task, lane=target, by=profile)

    @staticmethod
    def _run_line(run: Run) -> str:
        cert = f" · certainty {run.certainty:.2f}" if run.certainty is not None else ""
        return f"[run {run.id} · {run.lane} · {run.state.replace('_', ' ')}{cert}]"

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
    ) -> list[Message]:
        system = ADVISOR_PROMPT if not (name == AUTO or cfg.writes_code) else CHAT_PROMPT
        parts = [system + (SPOKEN if spoken else "")]
        if not remote:
            who = self.store.profile(profile).name
            if notes:
                parts.append(f"What {who} has told you to keep in mind:\n" + "\n".join(
                    f"- {n}" for n in notes))  # fmt: skip
            recalled = self.store.recall(profile, text, exclude=conv.id)
            if recalled:
                parts.append("From earlier conversations:\n" + "\n".join(
                    f"- ({h['title']}) {h['role']}: {clip(h['content'], 80)}" for h in recalled
                ))  # fmt: skip
            if name == AUTO or cfg.writes_code:
                context = build_coder_context(
                    self.orch.repo, text, budget=self.CONTEXT, home=self.orch.settings.home
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
            {"role": "user", "content": clip(text, self.MESSAGE_MAX)},
        ]
