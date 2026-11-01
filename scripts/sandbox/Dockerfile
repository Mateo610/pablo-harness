# pablo-check:py311 — the image checks run in.
# Build:  pablo sandbox build --base     (needs network; checks never do)
# Then per project:  pablo sandbox build -C <repo>   (bakes that repo's deps on top)
#
# Pin the base by digest once it's pulled on Pablo:
#   podman image inspect docker.io/library/python:3.11-slim --format '{{.Digest}}'
# and write it as python:3.11-slim@sha256:... below. Archive the built image with
# `podman save` under ~/pablo-data/images so a registry outage can't take it away.
FROM docker.io/library/python:3.11-slim

# uv installs fast and reads uv.lock; the check tools live in /opt/venv.
# Pin these versions after the first build (record them in docs/decisions.md).
RUN pip install --no-cache-dir uv \
 && uv venv /opt/venv \
 && uv pip install --python /opt/venv/bin/python --no-cache pytest mypy ruff \
 && rm -rf /root/.cache

ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /work
