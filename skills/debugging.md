---
name: debugging
when: [bugfix]
---
# Fixing a bug
- Reproduce first: add a failing test with the exact input from the report.
- Find the cause, not the symptom. Fix it in the one place it originates.
- Smallest diff that fixes it; no drive-by refactors in the same change.
- Never catch-and-ignore an exception to make the error go away.
- Keep every existing test as-is; if one was wrong, say so in the explanation.
