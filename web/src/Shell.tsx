// Cockpit v2: the rail and the pages. Phones keep the phone view (App). Work is the review
// desk: every change waiting for a decision.
import { useEffect, useState } from "react";
import { setLaneNames } from "./components";
import { api, type Lane, type Me, type Resources } from "./api";
import { App } from "./App";
import { Work } from "./Work";
import { Agents } from "./Agents";
import { ChatPage } from "./Chat";
import { Home } from "./Home";
import { initials, usePoll, useRoute } from "./hud";
import { Voice } from "./Voice";

const ICONS: Record<string, JSX.Element> = {
  home: (
    <>
      <rect x="3" y="3" width="7" height="7" />
      <rect x="14" y="3" width="7" height="7" />
      <rect x="3" y="14" width="7" height="7" />
      <rect x="14" y="14" width="7" height="7" />
    </>
  ),
  chat: <path d="M4 5h16v11H9l-5 4z" />,
  advisor: (
    <>
      <circle cx="12" cy="12" r="8" />
      <path d="M12 8v5l3 2" />
    </>
  ),
  voice: <path d="M3 12h2m3-5v10m4-14v18m4-13v8m4-5v2" />,
  agents: (
    <>
      <rect x="5" y="5" width="14" height="14" />
      <path d="M9 1v4m6-4v4M9 19v4m6-4v4M1 9h4m-4 6h4m14-6h4m-4 6h4" />
    </>
  ),
  work: <path d="M4 7h16M4 12h16M4 17h10" />,
};

const PAGES = [
  ["home", "HOME"],
  ["chat", "CHAT"],
  ["advisor", "ADVISOR"],
  ["voice", "VOICE"],
  ["agents", "AGENTS"],
  ["work", "WORK"],
] as const;

export function Shell() {
  const narrow = useNarrow();
  const route = useRoute();
  const [me, setMe] = useState<Me | null>(null);
  const [lanes, setLanes] = useState<Lane[]>([]);
  const [error, setError] = useState("");
  const [res] = usePoll<Resources>(api.resources, 5000);

  useEffect(() => {
    api.me().then(setMe).catch((e: Error) => setError(e.message));
    api.lanes().then((l) => (setLaneNames(l), setLanes(l))).catch(() => undefined);
  }, []);

  if (narrow) return <App />;
  const page = PAGES.some(([p]) => p === route.page) ? route.page : "home";
  const violet = page === "advisor";

  return (
    <div className={`h-app ${violet ? "violet" : ""}`}>
      <nav className="h-rail" aria-label="Pablo">
        <div className="h-logo" aria-hidden="true">
          P
        </div>
        {PAGES.map(([p, label]) => (
          <a key={p} href={`#/${p}`} className={`h-nav ${p === "advisor" ? "violet" : ""}`} aria-current={page === p ? "page" : undefined}>
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true">
              {ICONS[p]}
            </svg>
            {label}
          </a>
        ))}
        <span className={`h-avatar ${me?.role === "member" ? "member" : ""}`} title={me ? `${me.name} · ${me.role}` : ""}>
          {me ? initials(me.name === "Owner" ? "Me" : me.name) : "··"}
        </span>
      </nav>
      <main className="h-main">
        {error && <div className="h-error" style={{ margin: 16 }}>{error}</div>}
        {page === "home" && <Home me={me} res={res} />}
        {page === "chat" && <ChatPage key="chat" mode="chat" id={route.id} me={me} lanes={lanes} />}
        {page === "advisor" && <ChatPage key="advisor" mode="advisor" id={route.id} me={me} lanes={lanes} />}
        {page === "voice" && <Voice me={me} />}
        {page === "agents" && <Agents lanes={lanes} res={res} />}
        {page === "work" && <Work me={me} focus={route.id} />}
      </main>
    </div>
  );
}

function useNarrow(query = "(max-width: 720px)"): boolean {
  const [narrow, setNarrow] = useState(() => window.matchMedia(query).matches);
  useEffect(() => {
    const mq = window.matchMedia(query);
    const on = () => setNarrow(mq.matches);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, [query]);
  return narrow;
}
