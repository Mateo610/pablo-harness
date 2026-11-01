"""The real container. Runs on Pablo (`uv run pytest -m container`); skipped elsewhere.

Needs the base image: `pablo sandbox build --base`.
"""

from __future__ import annotations

import subprocess
import uuid
from pathlib import Path

import pytest

from pablo.config import load_settings
from pablo.sandbox import ContainerSandbox
from pablo.verify import verify_diff

ROOT = Path(__file__).resolve().parents[1]
CFG = load_settings(ROOT / "config" / "lanes.yaml").sandbox.model_copy(update={"kind": "container"})
SANDBOX = ContainerSandbox(CFG)
READY, WHY = SANDBOX.ready()

pytestmark = [
    pytest.mark.container,
    pytest.mark.skipif(not READY, reason=f"no container sandbox here: {WHY}"),
]
PYTEST = {"tests": ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider"]}


def new_test(body: str) -> str:
    """A diff that adds test_probe.py with `body` as its only test."""
    lines = ["def test_probe():", *("    " + line for line in body.splitlines())]
    plus = "".join(f"+{line}\n" for line in lines)
    return (
        "diff --git a/test_probe.py b/test_probe.py\nnew file mode 100644\n"
        f"--- /dev/null\n+++ b/test_probe.py\n@@ -0,0 +1,{len(lines)} @@\n{plus}"
    )


@pytest.fixture
def bare(tmp_path: Path) -> Path:
    """No dependency files, so the base image is used."""
    root = tmp_path / "bare"
    root.mkdir()
    (root / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    return root


def check(repo: Path, body: str, timeout_s: float = 120) -> tuple[bool, str]:
    verdict = verify_diff(repo, new_test(body), PYTEST, timeout_s, sandbox=SANDBOX)
    assert verdict.applied, verdict.error
    return verdict.passed, verdict.checks[0].output_tail


def test_a_passing_test_passes(bare: Path) -> None:
    ok, out = check(bare, "from calc import add\nassert add(2, 3) == 5")
    assert ok, out


def test_no_network(bare: Path) -> None:
    ok, out = check(
        bare, "import socket\nsocket.create_connection(('1.1.1.1', 53), timeout=3).close()"
    )
    assert not ok, out


def test_no_dns(bare: Path) -> None:
    ok, out = check(bare, "import socket\nsocket.getaddrinfo('pypi.org', 443)")
    assert not ok, out


def test_writes_outside_the_copy_never_reach_the_host(bare: Path) -> None:
    marker = f"/tmp/pablo-escape-{uuid.uuid4().hex}"
    # /tmp inside is a tmpfs: the write works there and vanishes with the container.
    ok, out = check(bare, f"open({marker!r}, 'w').write('x')")
    assert ok, out
    assert not Path(marker).exists()
    # The image itself is read-only (non-root may hit Permission denied first).
    ok, out = check(bare, "open('/usr/local/lib/pablo-escape', 'w').write('x')")
    assert not ok and ("Read-only" in out or "Permission denied" in out), out


def test_os_system_logging_stays_inside(bare: Path) -> None:
    """The judge fixture `bad-shell-call` logs to /tmp/textutils.log through the shell."""
    log = Path("/tmp/textutils.log")
    before = log.read_text() if log.exists() else None
    ok, out = check(bare, "import os\nos.system('echo pablo-probe >> /tmp/textutils.log')")
    assert ok, out
    after = log.read_text() if log.exists() else None
    assert after == before


def test_memory_limit(bare: Path) -> None:
    ok, out = check(bare, "blob = bytearray(8 * 1024**3)\nassert blob")
    assert not ok, out


def test_pids_limit(bare: Path) -> None:
    """400 sleeps must not all start under --pids-limit 256."""
    ok, out = check(
        bare,
        "import subprocess\n"
        "procs = []\n"
        "for _ in range(400):\n"
        "    procs.append(subprocess.Popen(['sleep', '30']))\n"
        "assert len(procs) == 400",
        timeout_s=30,
    )
    assert not ok, out


def test_cpu_limit(bare: Path) -> None:
    """Affinity or cpu_count inside the guest must be the configured cap."""
    ok, out = check(
        bare,
        "import os\n"
        "n = len(os.sched_getaffinity(0))\n"
        "print('affinity', n, 'count', os.cpu_count())\n"
        "assert n <= 4",
    )
    assert ok, out


def test_uv_run_pytest_is_offline_on_the_base_image(tmp_path: Path) -> None:
    """The command hello actually runs, against a project with no third-party deps."""
    repo = tmp_path / "bare"
    repo.mkdir()
    (repo / "pyproject.toml").write_text(
        '[project]\nname = "bare"\nversion = "0.1.0"\n'
        'requires-python = ">=3.11"\ndependencies = []\n\n[tool.uv]\npackage = false\n'
    )
    (repo / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    sandbox = ContainerSandbox(CFG, repo)
    sandbox.image = CFG.image  # base image; nothing here is installed from the lock
    verdict = verify_diff(
        repo,
        new_test("from calc import add\nassert add(2, 3) == 5"),
        {"tests": ["uv", "run", "pytest", "-q"]},
        120,
        sandbox=sandbox,
    )
    assert verdict.applied, verdict.error
    assert verdict.passed, verdict.checks[0].output_tail


def test_timeout_kills_and_removes_the_container(bare: Path) -> None:
    ok, out = check(bare, "import time\ntime.sleep(60)", timeout_s=5)
    assert not ok and "timed out" in out
    left = subprocess.run(
        [CFG.engine, "ps", "--all", "--filter", "name=pablo-check-", "--format", "{{.Names}}"],
        text=True,
        capture_output=True,
        check=False,
    ).stdout.split()
    assert left == []
