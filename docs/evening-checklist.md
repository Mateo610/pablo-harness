# First real evening on Pablo

A walk through everything built on 2026-11-05/06, on the real models. Do it in order. Each step
says what "working" looks like. When something doesn't match, note the step number, what you
saw, and the run id if there is one (it's on the run card), then keep going if you can.

Takes about an hour, most of it waiting on models. Don't run it while an eval is going.

## 0. Before you start

1. On Pablo: `cd ~/pablo-harness && git pull && uv sync && (cd web && npm ci && npm run build)`.
2. `systemctl --user restart pablo.service`, then `systemctl --user is-active pablo.service
   pablo-swap.service pablo-gateway.service pablo-tts.service` says active four times
   (`pablo-tts` may say inactive if deep ran last; that's fine, it wakes on the first voice turn).
3. `mkdir -p ~/projects` if it doesn't exist. Any git repo you clone into it shows up as a project.
4. Open the https address on your laptop (`pablo token` if the browser asks you to sign in).

**Working:** Home loads, the GPU and VRAM tiles show numbers, and the mode switch says Balanced.

## 1. The model menu (Chat)

1. Chat → the model button at the bottom left. It lists **Base** (Qwen3.6 35B-A3B), **No
   guardrails** (Mistral Small 3.2 24B heretic), **Best** (gpt-oss-120b), **Hybrid**
   (Qwen3.6 35B-A3B → gpt-oss-120b), and **Claude** under "off the box".
2. Pick **Base**, "just talk": *what does this repo do, in two sentences?*
   **Working:** an answer streams in a few seconds; the message is labelled Qwen3.6 35B-A3B.
3. Pick **No guardrails**, same question. **Working:** a blunter answer, labelled Mistral, and
   no run card even if you ask for a change ("talks here; builds only in a no-guardrails
   project").
4. Pick **Best**: *what's the riskiest part of this code?* **Working:** "gpt-oss-120b is thinking
   (this takes a while)", then an answer within a couple of minutes (longer if it had to load).
   Agents → GPU queue shows deep holding the GPU while it thinks.

## 2. One change, Hybrid

1. Pick **Hybrid**, "do it": *add a `--version` flag to the CLI that prints the version*
   (or any small change in the repo you're in).
2. **Working:** a run card appears: routing, building, checks, judge. It ends **ready** with a
   certainty, or **needs you**. If the base model broke it, the card says Best is trying it now,
   and a second card follows for the same change.
3. Open the diff, then **Apply** (or Reject). **Working:** applying puts the change in the repo
   (`git status` on Pablo shows it), and the other card of the pair (if any) goes rejected.

## 3. A new project, planned and built (Chat)

1. Home → **+ New project**: name *Plant Watcher*, about *Reads a soil moisture value from a file
   and tells me when to water*, **Standard**. **Start it**.
   **Working:** it appears in Projects; `~/projects/plant-watcher` has a README and one commit.
2. You land in Chat with Plant Watcher picked. Hybrid: *let's build this. Plan it first.*
   **Working:** no run starts. The reply ends with **Plan:** and 3–8 numbered steps, and a Plan
   card appears under the conversation in "draft".
   **If instead a run card appears**, the model used RUN instead of PLAN: note it (step 3.2).
3. Change something by talking: *make step 1 just the reading, no alerts yet.*
   **Working:** a new list replaces the old one.
4. *Looks right, go.* **Working:** "Building step 1: …" and the Plan card says running, step 1
   building, then **waiting for you** with its run card inside the Plan card.
5. *Apply it.* **Working:** "Applied: … [run …]", step 1 shows ✓, step 2 starts by itself.
6. When step 2 waits, press **Reject** on its card instead. **Working:** the plan says paused,
   step 2 stopped. Then **Keep going** on the Plan card: step 2 runs again.
7. Let it finish, or stop after a couple of steps. Either way the plan should never start a step
   before you approved the one before it.

## 4. Building by voice

1. Voice → project picker: Plant Watcher. The chip should say "● live", or "voice sleeping while
   deep works: it wakes when you talk" if deep ran last.
2. Hold the orb (or space): *what's left in the plan?*
   **Working:** if the voice was sleeping, "waking the voice…" first (time it: that's the wake
   cost), then a short spoken answer that names the next step. Nothing about code read aloud.
3. *Go ahead with the next step.* **Working:** it says it's building. Keep the page open and
   wait. When the step finishes, Pablo speaks without being asked: "Step N, …, is ready and the
   checks pass. The judge is … percent sure. Say apply it, or tell me what to change."
4. *Apply it.* **Working:** "Applied…" in the transcript and the Plan card moves on.
5. Ask something that isn't a yes: *what does that step do?* **Working:** nothing gets applied
   (Pablo only applies when your own words say so).
6. Switch to **Advisor** in the orb and ask for its take on the plan. **Working:** a different
   voice, Mistral's view, and it never starts or applies anything.

## 5. A no-guardrails project

1. Home → **+ New project**: *Raw Notes*, any description, **No guardrails · one model**.
2. Chat in it. **Working:** the model menu has a single choice, "No guardrails", and the header
   says "no guardrails · one model".
3. Ask it to plan and build a small first step the same way as in 3. **Working:** every card is
   labelled Mistral; Agents → GPU queue only ever shows the advisor model for this project (no
   coder, no deep, no judge loads for it), and nothing in it can be sent to Claude.
4. Note how good the code is compared to Plant Watcher. Nobody has measured this model as a coder
   yet.

## 6. Someone else in the browser

1. Have a teammate open the https address on their machine (they need to be on your tailnet and
   in `~/.pablo/access.yaml`; see `docs/teammates.md`).
2. They chat in Plant Watcher and ask for a change.
   **Working:** they see their own conversations only, can start runs, can't Apply (no button,
   and "apply it" gets "Only the owner can apply or discard changes"), and can't make projects.
3. While their run is going, you ask Best something. **Working:** Agents → GPU queue shows one
   of you waiting for the other, not both models fighting for VRAM.

## 7. The GPU, under pressure

1. Start a Best question in Chat, and while it's thinking, start a voice turn.
   **Working:** the voice turn says "waking the voice…" and waits until Best finishes (the voice
   can't share the GPU with it), then Best is unloaded, the voice wakes and answers. No 500
   errors anywhere.
2. In a terminal on Pablo: `uv run pablo eval --only ledger-by-payee-sums --cascade` while the
   browser is open. **Working:** Agents → GPU queue shows "another process" rows for the eval,
   the mode switches to Deep Work while it runs and back afterwards, and voice says it's paused.

## What to send back

- The step numbers that didn't match, with what you saw and run ids.
- Wake time from 4.2, and how long the first spoken word took on a warm turn.
- Whether the base model wrote a plan when asked (3.2) and a single change when the job was small
  (2.1). This is the biggest unknown: the prompt asks for it, but only the real model can say.
