# C14 · Chat modes, Plan mode, skills and /loop

**Context.** Chat has three modes today: Auto, Just talk, and Do it. Plan output currently shows up
as CLI-style script text, which is fine for testing but isn't what a teammate in the browser should see.
This milestone redesigns the chat composer and the planning experience, the way Matt planned it on 2026-11-07.
Build the parts in order (C14a → C14f). Each part works on its own before the next starts.

**Decisions (Matt, 2026-11-07)**
- Two modes: **Auto** and **Plan**. *Do it* is merged into Auto. *Just talk* is removed.
- **Auto** is the default. It handles conversation, side questions, and normal work. Like any
  LLM, it can still write a multi-step plan and run it when asked.
- **Plan** is only ever turned on by the user. Pablo never switches into Plan, and it never starts the
  GSD-style flow on its own. Plan adds a context and prompt-engineering layer on top of a normal plan.
- Every Plan conversation ends in a plan, or it's abandoned partway. Abandoning is fine.
- Approving a plan switches that chat to **Auto**, which runs it.
- The plan lives in a **panel next to the chat**. The Work tab shows progress for every plan in every chat.
- No form builder. The Major interview works like GSD's discuss phase: one adaptive question at a time.

## Where this lives today (read at 81074ea, 2026-11-07)

| Piece | Today | What C14 changes |
|---|---|---|
| Mode toggle | `web/src/Chat.tsx`: `type Act = "auto" \| "talk" \| "do"`, the `h-seg` group in the composer. `send()` maps it to `do: true/false/null` for `api.say` | The web stops sending `do: true`. Auto is `do: null`. Plan sends a new `plan: "minor" \| "major"` field. **Keep `do` in the API**, because voice and the TUI still use it |
| Turn handling | `src/pablo/convos.py` `Conversations.say()`: the model's control lines (`RUN:`, `PLAN:`, `STEP:`, `BUILD:`, `APPLY:`) drive runs and plans | In Plan mode, `say()` uses the discuss and plan prompts, treats `RUN:` and `BUILD:` as not allowed until the user approves, and makes sure the conversation ends in a `PLAN:` |
| Plans | `src/pablo/plans.py` and `plan.py`: **one plan per project**, at `.pablo/plan.yaml`. `Plan.conversation` records where it was planned. The runner already waits for an approval between steps and pauses on a reject | **Plans move to one file per plan**, `.pablo/plans/<plan-id>.yaml`, each tied to its conversation. This is what lets two chats run plans in one project (C14d). Migrate `.pablo/plan.yaml` into the folder on first load |
| Plan card | `web/src/hud.tsx` `PlanCard`, rendered under the messages in Chat and Voice | It becomes the plan panel to the right of the chat (C14b). Voice keeps the card |
| Remember | The right rail `h-rail-r` in `Chat.tsx` ("+ remember something"), the `remember: …` keyword in `convos.py` (`_SAID_REMEMBER`), and `api.remember` | The drop zone goes in this rail (C14a). There's **no upload endpoint yet**: add `POST /api/memory/files` (stored under `~/.pablo/`, per profile) |
| Needs-you | Run cards and `RunView.tsx`. The v1 Workbench (`App.tsx`) has the inbox | The five buttons go on the run card (C14c) |
| Work tab | `App.tsx`, the v1 Workbench, kept "until each tool has a home" (`Shell.tsx`) | Rebuilt around plans and loops (C14d) |
| Skills | **They already exist**: `skills/*.md` with `name` / `when` / `keywords` frontmatter, chosen automatically by `context.pick_skill()` and put into run prompts | C14e adds commands you call by name *on top of* this, without replacing it (see C14e) |
| Night work | `src/pablo/overnight.py` (the queue and morning brief) and `gpu.py` `Priority` | The `/loop` scheduling uses these (C14f) |

**Folder decision:** to stay consistent with `.pablo/plan.yaml`, the GSD-style files go under
`.pablo/plans/<plan-id>/`, not GSD's `.planning/`.

---

## C14a · Composer

```
[ Auto | Plan ]  →(Plan only)→  [ Minor | Major ]          [ 🧠 Extended thinking ○ ]
                                                                  [ Hybrid ▾ ]
┌─────────────────────────────────────────────────────────────────────────────┐
│ message…                                                       📎      ➤    │
└─────────────────────────────────────────────────────────────────────────────┘
 remember area · drop files here
```
1. Replace the three mode buttons with an `Auto | Plan` toggle. Remember the last choice per user,
   so a new chat opens in whatever mode they used last.
2. **Minor | Major** appears only while Plan is selected.
3. Extended thinking becomes a small toggle chip, narrower than the model box, stacked directly
   above it. Its rules don't change (Base and Hybrid only, never voice, see C13).
4. Drag-and-drop file upload goes in the **remember area**, plus a 📎 button. Dropped files
   become remembered context. They are not part of the plan panel, but plans read them
   automatically (see C14b, step 6).
5. Keep the `remember` keyword trigger working exactly as it does today.

**Done when:** the composer shows the two modes, the Plan sub-choice, the small thinking chip
above the model box, and a working drop zone. The mode choice survives a page reload.

## C14b · Plan mode

1. **Entering Plan.** Selecting Plan shows a prompt card that asks *"What are you planning?"*, with a
   text box and Minor | Major. Nothing goes to the model until the user sends it.
2. **Minor.** The model asks 0–2 clarifying questions, then produces a **plan card**: numbered
   steps, files likely touched, and risks. Buttons: **Approve**, **Edit**, **Discard**.
3. **Major (GSD-style).** This runs a discuss phase in the chat:
   - One question at a time. Each question depends on the previous answer, and there's no fixed list.
   - When the model has enough, it drafts a **spec card**: goal, users, requirements, out of scope,
     and open questions. Buttons: **Edit**, **Keep refining**, **Use this**.
   - "Use this" turns the spec into a phased plan. Each phase has steps and a "done when" line.
   - The model writes the spec between `SPEC: <title>` and `END SPEC`, and the phases as
     `PHASE: <title> | <done when>` lines, each followed by its STEP lines (up to 6 phases and 24 steps).
   - Files go next to the plan, under `.pablo/plans/<conversation>/`: `SPEC.md`, `ROADMAP.md`, and
     `STATE.md`, rewritten on every save. *(Built 2026-11-07: three files, not GSD's five. The spec
     already covers project and requirements.)*
   - A Minor plan is just `.pablo/plans/<conversation>.yaml`, with no extra files.
4. **Plan panel.** The plan builds up in a panel to the right of the chat, while the
   conversation stays on the left. Under 1500 px wide, the conversation list hides while the
   panel is open. Under 860 px, the plan sits inline in the chat.
5. **Approve.** The chat switches to Auto and runs the plan, using the current plan runner:
   steps run in order, a step never starts before the one before it is approved, and a failed
   step pauses. Progress shows in the panel.
6. **Context.** Every Plan turn includes the remembered items and files from C14a.
7. **Drafts.** An unfinished plan belongs to its chat. It survives switching browser tabs or chats
   and reloading the page, and so does an unsent message. It's deleted only when the chat is
   deleted or the user presses **Start over** (not while a step is building).
   **Voice:** a voice call has no chat of its own. With no plan of its own, it talks about (and
   builds) your newest live plan in the project, so "what's left in the plan?" still works.
8. **Prompts.** Write the discuss and plan prompts as files in `src/pablo/prompts/`, so they
   can be tuned without code changes.

**Done when:** Minor makes a plan card in two turns or fewer, Major runs an interview that ends
in a spec and then a phased plan with its files written under `.pablo/plans/`, Approve runs it in Auto, and a
half-finished draft survives a reload.

## C14c · "Needs you" cards that fix things

Today the card's buttons explain the problem but give no way to act on it. Each needs-you card gets:
1. **Copy details**: copies a plain-text block to the clipboard. The block has the plan and
   step, what was being done, the error, the file and lines involved, and the last ~20 log lines.
2. **Send to chat**: puts that same block into the composer with the run attached, so the user only
   types what they want done.
3. **Fix it**: tries again, with the details block as feedback. *(Built 2026-11-07: on a plan
   step it retries **that step** with the feedback, so the plan carries on when the fix lands.
   Anywhere else it starts the run again next to the old one. Both discard the old change.)*
4. **Retry** (again, without the feedback) and **Skip step** (the step becomes `skipped`, its
   change is discarded, and the next step starts), so a plan is never stuck on one step.
   Who can use these: the owner, or whoever started the run.

**Done when:** every needs-you card has the five buttons, and Send to chat → "fix it" resolves a
failed step without copying anything by hand.

## C14d · Work tab: plans across chats

This fits into the Work tab redesign (clearer, less crowded, distinct from Home).
1. One row per active plan or loop, from every chat. Each row shows the project, the chat it came from, its
   size (Minor or Major), step progress (`5/8 ✓✓✓✓▶`), and its status: running, needs you, paused, or done.
2. **Overlap warning.** If two active plans have touched, or plan to touch, the same files, both rows
   show a warning naming the files. This supports Matt's usual setup of two chats working on
   different parts of the same project.
3. Clicking a row opens its chat with the plan panel showing.
4. Teammates see only their own plans. The owner sees everyone's.

**Done when:** two chats running plans in one project both show live progress in Work, and an
overlapping file shows the warning.

## C14e · Slash commands and skills

1. Typing `/` at the start of the composer opens a filterable menu. Built-in commands:
   `/plan` (Plan, Minor), `/major` (Plan, Major), `/auto`, `/remember <text>`, `/loop`,
   `/new-skill`, and `/skills` (lists them).
2. **Skills.** Keep today's format (`skills/*.md`, frontmatter `name` / `when` / `keywords`,
   picked automatically by `context.pick_skill()`). Add two optional frontmatter keys:
   `command: true` (it shows in the `/` menu and runs when called by name) and `args:` (one line
   describing what to type after it). Look in three places, with the most specific winning:
   `<repo>/.pablo/skills/`, then `~/.pablo/skills/`, then the repo's `skills/`. Calling `/<name>`
   puts the body into the turn, whether or not its keywords match.
3. **`/new-skill`.** An interview in the same style as Major: what the skill does, what it needs, and
   when it's finished. It ends in a SKILL.md card with Save and Edit buttons. There are no forms.

**Done when:** the `/` menu works, a skill made with `/new-skill` shows up in the menu and runs,
and `/remember` behaves like the keyword.

## C14f · /loop: work until stopped

`/loop` is a built-in skill. Running it starts a short setup conversation:
1. *What should I work on?* The user points at a doc, or drops one in. The doc has a task list.
2. *What's the goal?* One line. The judge uses it to decide what counts as an improvement.
3. *When the list is done: stop, or keep improving?*
4. *Any limits?* Optional: number of rounds, number of hours, and folders not to touch.

That creates a **Loop task**, which shows in Work next to plans and runs this cycle:
- **Work the list.** The implementer (the Base model) takes the next unchecked task, does it, runs
  the checks, commits, and ticks the task off in the doc.
- **Improve.** When the list is empty and the user chose "keep improving", the judge (the bigger
  model) compares the project to the goal and adds new tasks to the doc, each with a one-line reason.
- **Never wait.** Anything that needs the user is moved to a `## Needs you` section of the doc, and
  the loop goes on to the next task.
- **Control.** Pause and Stop are always visible, in the chat and in Work. The loop runs on its own branch,
  `loop/<slug>`, and never applies anything to main by itself.
- **Stall check.** If the judge proposes only trivial changes for 3 rounds in a row, the loop pauses
  and sends a notification instead of spinning.
- **Log.** The doc gets a `## Loop log` section with one line per task: round, task,
  result, and commit.
- **Live edits.** The user can edit the doc while the loop runs. New tasks are picked up on
  the next pick.
- **GPU.** The loop runs at night urgency through the scheduler, so a person using Pablo always
  goes first.

**Done when:** a loop started on a doc with 3 tasks finishes all three on its branch, writes 2 or more
improvement tasks of its own, parks one needs-you item without stopping, and stops when Stop is pressed.

## C14g · Overnight /loop trial (Cursor, on Pablo)

Run this after C14f is merged, on a night with nothing else on the GPU.
1. `git pull && uv sync && (cd web && npm ci && npm run build)`, then restart `pablo.service`.
2. In a throwaway clone of a small repo under `~/projects/`, write `LOOP.md` with a one-line goal
   and 5 small tasks. In Chat: `/loop LOOP.md`, choose keep improving, with a 6-hour limit.
3. In the morning, record: tasks done, tasks the judge added (and how many you'd keep), items parked as
   needs you, commits on `loop/<slug>`, whether the stall check fired, and whether a person
   using Pablo during the night was ever kept waiting by the loop (Agents → GPU queue).
4. One row in `docs/decisions.md`. Don't merge the loop branch.

**Report back:** the numbers from step 3 and the doc's `## Loop log`.

---

## Don't
- Don't let Pablo choose Plan for the user, or start the Major interview from Auto.
- Don't build forms for the interview or for `/new-skill`.
- Don't let a loop merge to main or apply anything without the owner.
- Don't change voice. Voice keeps its current talk / RUN / PLAN behavior for now.

## Record
One row in `docs/decisions.md` for the mode change (three modes → Auto and Plan), and update
`BUILD_PLAN.md` with C14a–f.
