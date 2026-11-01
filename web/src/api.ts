// Typed client for the harness API (src/pablo/api.py).

export type LaneName = "router" | "coder" | "judge" | "deep" | "media" | "claude" | string;

export interface Lane {
  name: LaneName;
  role: string;
  model: string;
  color: string;
  kind: "local" | "remote";
  train_ok: boolean;
  requires_approval: boolean;
}

export interface Check {
  name: string;
  passed: boolean;
  exit_code: number;
  duration_s: number;
  output_tail: string;
}

export interface Verdict {
  applied: boolean;
  passed: boolean;
  checks: Check[];
  added: number;
  removed: number;
  files: string[];
  error: string;
}

export type RunState = "running" | "ready" | "needs_you" | "approved" | "rejected" | "failed";

export interface Run {
  id: string;
  task: string;
  lane: LaneName;
  model: string;
  state: RunState;
  route_reason: string;
  complexity: string;
  explanation: string;
  diff: string;
  verdict: Verdict | null;
  certainty: number | null;
  judge_summary: string;
  question: string;
  task_id: string | null;
  escalated_from: string | null;
  created_at: string;
}

export type TaskStatus = "queued" | "active" | "needs_you" | "done" | "blocked";

export interface PlanTask {
  id: string;
  title: string;
  status: TaskStatus;
  lane: string | null;
}

export interface Phase {
  id: number;
  title: string;
  gate: string;
  tasks: PlanTask[];
}

export interface Plan {
  project: string;
  goal: string;
  phases: Phase[];
}

export interface HarnessEvent {
  kind: string;
  run_id: string;
  data: Record<string, unknown> & { lane?: string; text?: string };
  at: string;
}

export type JobStatus = "queued" | "running" | "done" | "failed" | "cancelled";

export interface Job {
  id: string;
  task: string;
  lane: string | null;
  task_id: string | null;
  status: JobStatus;
  run_id: string | null;
  error: string;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface BriefItem {
  run_id: string;
  task: string;
  lane: string;
  state: RunState;
  certainty: number | null;
  summary: string;
  question: string;
}

export interface Brief {
  day: string;
  headline: string;
  done: number;
  needs_you: number;
  failed: number;
  items: BriefItem[];
  failed_jobs: { task: string; error: string }[];
  still_queued: number;
  plan_line: string;
  created_at: string;
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error((body as { detail?: string }).detail ?? `${res.status} ${res.statusText}`);
  }
  return (await res.json()) as T;
}

export const api = {
  lanes: () => call<Lane[]>("/api/lanes"),
  plan: () => call<Plan>("/api/plan"),
  inbox: () => call<Run[]>("/api/inbox"),
  run: (id: string) => call<Run>(`/api/runs/${id}`),
  start: (task: string, lane?: string, taskId?: string) =>
    call<Run>("/api/runs", {
      method: "POST",
      body: JSON.stringify({ task, lane: lane ?? null, task_id: taskId ?? null }),
    }),
  approve: (id: string) => call<Run>(`/api/runs/${id}/approve`, { method: "POST" }),
  reject: (id: string) => call<Run>(`/api/runs/${id}/reject`, { method: "POST" }),
  escalate: (id: string) => call<Run>(`/api/runs/${id}/escalate`, { method: "POST" }),
  queue: () => call<Job[]>("/api/queue"),
  addJob: (task: string, lane?: string, taskId?: string) =>
    call<Job>("/api/queue", {
      method: "POST",
      body: JSON.stringify({ task, lane: lane ?? null, task_id: taskId ?? null }),
    }),
  cancelJob: (id: string) => call<Job>(`/api/queue/${id}`, { method: "DELETE" }),
  runQueueNow: () => call<Job[]>("/api/queue/run", { method: "POST" }),
  brief: () => call<Brief>("/api/brief").catch(() => null),
  makeBrief: () => call<Brief>("/api/brief", { method: "POST" }),
  voice: async (audio: Blob) => {
    const res = await fetch("/api/voice", { method: "POST", body: audio });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error((body as { detail?: string }).detail ?? `${res.status}`);
    }
    return (await res.json()) as { text: string; run?: Run };
  },
};

export function subscribe(onEvent: (e: HarnessEvent) => void): () => void {
  const source = new EventSource("/api/events");
  source.onmessage = (msg) => onEvent(JSON.parse(msg.data) as HarnessEvent);
  return () => source.close();
}
