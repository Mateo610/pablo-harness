# M7 · Traces you can browse, evals you can trust

**Goal.** See every run in a trace UI, and have a fixed eval suite that decides whether any change
(prompt, model, LoRA) is actually better.

**Steps**
1. Self-host Langfuse (MIT core) with Docker Compose bound to 127.0.0.1; disable its telemetry. Add a
   LiteLLM callback so gateway calls land there. Keep `~/.pablo/traces` as the source of truth;
   Langfuse is a view. Escalation traces are tagged `do-not-train`.
2. Eval suite in `evals/`: 30 tasks (10 from M3 judge fixtures, 10 from M6, 10 new: refactors, tests,
   small features) each with a repo snapshot and a pass condition (tests to pass). Runner:
   `uv run pablo eval [--lane coder] [--model alias]` → pass rate, mean certainty, judge calibration
   (certainty vs actual pass), tokens, time. Results appended to `evals/results.jsonl`.
3. Optionally wire Promptfoo (MIT) for prompt A/B on `CODER_PROMPT`/`JUDGE_PROMPT`.
4. Record the baseline score for the current coder.

**Acceptance**
- Langfuse shows runs from the gateway. `pablo eval` produces a baseline. BUILD_PLAN M7 done.
