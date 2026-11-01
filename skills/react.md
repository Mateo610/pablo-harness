---
name: react
when: [frontend]
keywords: [react, component, tsx, css]
---
# React + TypeScript (Pablo cockpit)
- Function components with typed props; state stays in the nearest parent that needs it.
- Style only with tokens from `web/src/tokens.css` (`var(--surface-200)` etc.); no raw hex.
- Amber (`--accent`) means "your move" and nothing else.
- Real `<button>` and `<label>`; every control reachable by keyboard; contrast ≥ 4.5:1.
- Data comes from `web/src/api.ts`; add the endpoint there with its type, then use it.
