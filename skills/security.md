---
name: security
keywords: [security, secret, password, token, auth, subprocess, shell, injection, upload, path]
---
# Security
- `subprocess.run([...])` with a list, never `shell=True` or f-strings into a shell.
- Paths from input: resolve them and check they stay inside the allowed root.
- No secrets in code, logs, or tests; read them from the environment.
- Parameterized SQL only. Validate input at the boundary and reject, don't "clean".
- Servers bind to 127.0.0.1 or the Tailscale IP, never 0.0.0.0.
