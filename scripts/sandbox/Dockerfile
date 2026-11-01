# pablo-check:py311 — the image checks run in.
# Build:  pablo sandbox build --base     (needs network; checks never do)
# Then per project:  pablo sandbox build -C <repo>   (bakes that repo's deps on top)
#
# Pin the base by digest once it's pulled on Pablo:
#   podman image inspect docker.io/library/python:3.11-slim --format '{{.Digest}}'
# and write it as python:3.11-slim@sha256:... below. Archive the built image with
# `podman save` under ~/pablo-data/images so a registry outage can't take it away.
# Pulled on Pablo 2026-11-01 as python:3.11-slim. Digest is the image we ran.
FROM docker.io/library/python:3.11-slim@sha256:673719e071ecf27434fd2635bbd8d00f49f4fdd81f243143438a42ced8bedf60

# uv installs fast and reads uv.lock; the check tools live in /opt/venv.
# Versions are what the first build resolved on Pablo, 2026-11-01.
RUN pip install --no-cache-dir 'uv==0.13.6' \
 && uv venv /opt/venv \
 && uv pip install --python /opt/venv/bin/python --no-cache \
      'pytest==9.2.0' 'mypy==2.4.1' 'ruff==0.17.3' \
 && rm -rf /root/.cache

ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /work
