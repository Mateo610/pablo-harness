"""Profiles and teammate access: tailscaled says who a peer is; headers never do."""

from __future__ import annotations

import json
import stat
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pablo.access import Access, AccessConfig, CachedWhois, Denied, load_access, owner_token
from pablo.api import create_app
from pablo.config import Settings
from pablo.gateway import FakeBackend
from pablo.orchestrator import Orchestrator

MATT, SAM, EVE = "100.64.0.10", "100.64.0.20", "100.64.0.30"
PEERS = {MATT: ("matt@github", "Matt"), SAM: ("sam@github", "Sam"), EVE: ("eve@github", "Eve")}
TEAM = AccessConfig(owner="matt@github", members=["sam@github"])
CHECKS = {"tests": [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"]}
GOOD = (
    "Added sub.\n\ncalc.py\n<<<<<<< SEARCH\ndef add(a, b):\n    return a + b\n=======\n"
    "def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n>>>>>>> REPLACE\n"
)


def gate(tmp_path: Path, config: AccessConfig = TEAM) -> Access:
    return Access(tmp_path, config, whois=PEERS.get, self_login=lambda: "")


def test_alone_this_machine_is_the_owner(tmp_path: Path) -> None:
    me = gate(tmp_path, AccessConfig()).resolve("127.0.0.1", "127.0.0.1", {})
    assert me.is_owner and me.profile == "owner" and me.via == "local"


def test_with_teammates_local_requests_need_the_owner_token(tmp_path: Path) -> None:
    g = gate(tmp_path)
    with pytest.raises(Denied) as no:
        g.resolve("127.0.0.1", "127.0.0.1", {})
    assert no.value.status == 401 and "pablo token" in str(no.value)
    with pytest.raises(Denied):
        g.resolve("127.0.0.1", "127.0.0.1", {}, token="guess")
    assert g.resolve("::1", "::1", {}, token=g.token()).is_owner
    # Pablo's own Tailscale IP is this machine too: another account here is not a teammate.
    with pytest.raises(Denied):
        g.resolve(MATT, MATT, {})


def test_tailscale_says_who_a_peer_is(tmp_path: Path) -> None:
    g = gate(tmp_path)
    assert g.resolve(MATT, "100.64.0.1", {}).is_owner
    sam = g.resolve(SAM, "100.64.0.1", {})
    assert (sam.profile, sam.role, sam.name, sam.via) == ("sam@github", "member", "Sam", "tailnet")
    with pytest.raises(Denied, match="isn't on Pablo's access list"):
        g.resolve(EVE, "100.64.0.1", {})
    with pytest.raises(Denied, match="couldn't say"):
        g.resolve("100.64.0.99", "100.64.0.1", {})  # tagged or unknown device
    with pytest.raises(Denied, match="only answers"):
        g.resolve("203.0.113.5", "100.64.0.1", {})


@pytest.mark.parametrize("client", ["127.0.0.1", SAM])
def test_identity_headers_are_never_trusted(tmp_path: Path, client: str) -> None:
    g = gate(tmp_path, AccessConfig(owner="matt@github", members=["sam@github"], loopback="owner"))
    for header in ("tailscale-user-login", "tailscale-funnel-request"):
        with pytest.raises(Denied, match="tailscale serve"):
            g.resolve(client, "100.64.0.1", {header: "matt@github"}, token=g.token())


def test_with_no_setup_the_machine_owner_is_the_owner(tmp_path: Path) -> None:
    """No access.yaml: your phone over Tailscale keeps working, and nobody else gets in."""
    g = Access(tmp_path, AccessConfig(), whois=PEERS.get, self_login=lambda: "matt@github")
    assert g.resolve(MATT, "100.64.0.1", {}).is_owner
    with pytest.raises(Denied):
        g.resolve(SAM, "100.64.0.1", {})
    assert g.resolve("127.0.0.1", "127.0.0.1", {}).is_owner  # still no token when alone


def test_the_token_is_private_and_stable(tmp_path: Path) -> None:
    token = owner_token(tmp_path)
    assert token == owner_token(tmp_path) and len(token) > 30
    assert stat.S_IMODE((tmp_path / "owner.token").stat().st_mode) == 0o600


def test_access_list_lives_outside_the_repo(tmp_path: Path) -> None:
    assert load_access(tmp_path) == AccessConfig()
    (tmp_path / "access.yaml").write_text("owner: matt@github\nmembers: [sam@github]\n")
    cfg = load_access(tmp_path)
    assert cfg.local_mode == "token" and cfg.members == ["sam@github"]
    assert AccessConfig().local_mode == "owner"


def test_whois_is_cached() -> None:
    calls: list[str] = []

    def lookup(ip: str) -> tuple[str, str] | None:
        calls.append(ip)
        return ("sam@github", "Sam")

    who = CachedWhois(lookup)
    assert who(SAM) == who(SAM) and calls == [SAM]


def app_for(settings: Settings, repo: Path) -> tuple[Orchestrator, Access]:
    backend = FakeBackend(
        scripts={
            "pablo/coder": lambda m, msgs: GOOD,
            "pablo/judge": lambda m, msgs: json.dumps({"certainty": 0.9, "summary": "ok"}),
        }
    )
    orch = Orchestrator(settings, backend, repo=repo, checks=CHECKS)
    return orch, Access(settings.home, TEAM, whois=PEERS.get, self_login=lambda: "")


def test_a_teammate_can_work_but_only_the_owner_applies(settings: Settings, repo: Path) -> None:
    orch, access = app_for(settings, repo)
    app = create_app(orch, access)
    sam = TestClient(app, client=(SAM, 5000))
    matt = TestClient(app, client=(MATT, 5000))
    eve = TestClient(app, client=(EVE, 5000))

    me = sam.get("/api/me").json()
    assert (me["role"], me["can_approve"], me["name"]) == ("member", False, "Sam")
    assert eve.get("/api/runs").status_code == 403
    assert eve.get("/api/health").status_code == 200  # no data, no identity needed

    run = sam.post("/api/runs", json={"task": "add sub(a, b)", "lane": "coder"}).json()
    assert run["state"] == "ready" and run["by"] == "sam@github"
    assert sam.post(f"/api/runs/{run['id']}/approve").status_code == 403
    assert sam.post(f"/api/runs/{run['id']}/escalate").status_code == 403
    assert "def sub" not in (repo / "calc.py").read_text()
    assert matt.post(f"/api/runs/{run['id']}/approve").status_code == 200
    assert "def sub" in (repo / "calc.py").read_text()

    mine = matt.post("/api/runs", json={"task": "add sub(a, b)", "lane": "coder"}).json()
    assert sam.post(f"/api/runs/{mine['id']}/reject").status_code == 403
    theirs = sam.post("/api/runs", json={"task": "add sub(a, b)", "lane": "coder"}).json()
    assert sam.post(f"/api/runs/{theirs['id']}/reject").status_code == 200

    assert sam.post("/api/runs", json={"task": "x", "lane": "claude"}).status_code == 403
    assert sam.post("/api/conversations", json={"lane": "claude"}).status_code == 403
    assert sam.post("/api/queue/run").status_code == 403


def test_each_person_has_their_own_chats_and_notes(settings: Settings, repo: Path) -> None:
    orch, access = app_for(settings, repo)
    app = create_app(orch, access)
    sam = TestClient(app, client=(SAM, 5000))
    matt = TestClient(app, client=(MATT, 5000))
    conv = sam.post("/api/conversations", json={"lane": "coder"}).json()
    sam.post(f"/api/conversations/{conv['id']}/messages", json={"text": "remember: I use vim"})
    assert matt.get("/api/conversations").json() == []
    assert matt.get(f"/api/conversations/{conv['id']}").status_code == 404
    assert matt.get("/api/memories").json() == []
    assert [m["text"] for m in sam.get("/api/memories").json()] == ["I use vim"]


def test_the_token_link_signs_in_a_browser_on_pablo(settings: Settings, repo: Path) -> None:
    orch, access = app_for(settings, repo)
    local = TestClient(create_app(orch, access), client=("127.0.0.1", 5000))
    assert local.get("/api/runs").status_code == 401
    assert local.get("/api/me", params={"token": "nope"}).status_code == 401
    assert local.get("/api/me", params={"token": access.token()}).json()["role"] == "owner"
    assert local.get("/api/runs").status_code == 200  # the cookie carries it now
    bearer = TestClient(create_app(orch, access), client=("127.0.0.1", 5000))
    headers = {"Authorization": f"Bearer {access.token()}"}
    assert bearer.get("/api/runs", headers=headers).status_code == 200
