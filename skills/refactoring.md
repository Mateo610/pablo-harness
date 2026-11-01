---
name: refactoring
when: [refactor]
---
# Refactoring
- No behavior change. Existing tests must pass untouched; don't edit them to fit.
- Small, mechanical steps: extract, rename, inline. One idea per change.
- Keep public names and signatures unless the task says otherwise.
- If no test covers the code you're moving, add one first that pins current behavior.
