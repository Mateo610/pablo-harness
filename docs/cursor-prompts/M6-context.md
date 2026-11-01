# M6 · Better context: repo map + Serena

**Context.** `repo_context()` in orchestrator.py sends the file list plus files the task names. That
fails when the bug is somewhere the task doesn't mention.

**Goal.** Give the coder the right code without blowing the context window.

**Steps**
1. Repo map: build a symbol map (files → classes/functions with signatures) with tree-sitter
   (`tree-sitter-language-pack` or similar, check license), ranked by relevance to the task (BM25 over
   symbol names + docstrings is fine to start). Budget: ≤ 6k tokens.
2. Serena (MIT) gives LSP-backed find-symbol / find-references over MCP. Run it as a separate process
   and add a two-step coder flow: first call asks "which symbols do you need?", harness fetches them
   via Serena, second call writes the diff. Keep the one-shot path as fallback.
3. Index cache in `~/.pablo/index/<repo-hash>`; refresh on git HEAD change.
4. Eval: 10 bug-fix tasks in the sandbox repo where the task text never names the file. Compare
   one-shot vs map vs map+Serena on pass rate and tokens. Record in `docs/benchmarks.md`.

**Acceptance**
- map+Serena beats the current context on the 10 tasks. Tests (Serena mocked) pass. BUILD_PLAN M6 done.
