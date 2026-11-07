"""Profiles and teammate access: tailscaled says who a peer is; headers never do."""

from __future__ import annotations

import json
import stat
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from tests.fakes import FakeBackend

from pablo.access import Access, AccessConfig, CachedWhois, Denied, load_access, owner_token
from pablo.api import create_app
from pablo.config import Settings
from pablo.orchestrator import Orchestrator

MATT, SAM, EVE = "100.64.0.10", "100.64.0.20", "100.64.0.30"
PEERS = {MATT: ("matt@github", "Matt"), SAM: ("sam@github", "Sam"), EVE: ("eve@github", "Eve")}
TEAM = AccessConfig(owner="matt@github", members=["sam@github"], hosts=["testserver"])
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


def test_an_invite_code_works_once_and_expires(tmp_path: Path) -> None:
    from pablo.access import Invites

    invites = Invites(tmp_path)
    code = invites.create(days=1, note="for eve", now=1000.0)
    assert code not in (tmp_path / "invites.json").read_text()  # only the hash is kept
    assert stat.S_IMODE((tmp_path / "invites.json").stat().st_mode) == 0o600
    assert not invites.redeem("wrong", "eve@github", now=1001.0)
    assert invites.redeem(code, "eve@github", now=1001.0)
    assert not invites.redeem(code, "mallory@github", now=1002.0)  # spent
    late = invites.create(days=1, now=1000.0)
    assert not invites.redeem(late, "eve@github", now=1000.0 + 86401)
    assert [e["used_by"] for e in invites.listing()] == ["eve@github", ""]


def test_joining_with_a_code_puts_you_on_the_access_list(tmp_path: Path) -> None:
    from pablo.access import Invites

    (tmp_path / "access.yaml").write_text("owner: matt@github\nloopback: auto\n")
    g = Access(tmp_path, load_access(tmp_path), whois=PEERS.get, self_login=lambda: "")
    with pytest.raises(Denied):
        g.join(EVE, None, {}, "not-a-code")
    code = Invites(tmp_path).create()
    eve = g.join(EVE, None, {}, code)
    assert (eve.role, eve.profile, eve.name) == ("member", "eve@github", "Eve")
    assert g.resolve(EVE, None, {}).role == "member"  # no code needed from now on
    saved = load_access(tmp_path)
    assert saved.owner == "matt@github" and saved.members == ["eve@github"]
    assert g.join(EVE, None, {}, code).role == "member"  # a spent code doesn't lock you out
    assert g.join(MATT, None, {}, "").is_owner  # the owner never needs one
    with pytest.raises(Denied) as local:
        g.join("127.0.0.1", "127.0.0.1", {}, code)  # codes are for tailnet peers only
    assert local.value.status == 401


def test_the_invite_link_page(settings: Settings, repo: Path) -> None:
    from pablo.access import Invites

    orch, _ = app_for(settings, repo)
    config = AccessConfig(owner="matt@github", hosts=["testserver"])
    access = Access(settings.home, config, whois=PEERS.get, self_login=lambda: "")
    app = create_app(orch, access)
    eve = TestClient(app, client=(EVE, 5000))
    assert eve.get("/api/runs").status_code == 403
    bad = eve.get("/join?code=nope")
    assert bad.status_code == 403 and "already used or has expired" in bad.text
    code = Invites(settings.home).create()
    page = eve.get(f"/join?code={code}")
    assert page.status_code == 200 and "You're in, Eve." in page.text
    me = eve.get("/api/me")
    assert me.json().get("role") == "member", me.text
    assert "eve@github" in (settings.home / "access.yaml").read_text()


def test_pablo_invite_prints_a_message_with_both_links(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from typer.testing import CliRunner

    from pablo.cli import app

    home = tmp_path / "home"
    monkeypatch.setenv("PABLO_HOME", str(home))
    (home / "tls").mkdir(parents=True)
    (home / "tls" / "name").write_text("pablo.tail1234.ts.net\n")
    (home / "tls" / "pablo.crt").write_text("cert")
    runner = CliRunner()
    first = runner.invoke(app, ["invite"])
    assert first.exit_code == 1 and "share link" in first.output
    share = "https://login.tailscale.com/admin/invite/abc"
    made = runner.invoke(app, ["invite", "--share-link", share, "--note", "eve"])
    assert made.exit_code == 0, made.output
    assert share in made.output and "https://pablo.tail1234.ts.net:8710/join?code=" in made.output
    assert "join.sh" in made.output and "join.ps1" in made.output
    again = runner.invoke(app, ["invite"])  # the share link is remembered
    assert again.exit_code == 0 and share in again.output
    listed = runner.invoke(app, ["invite", "--list"])
    assert listed.output.count("open") == 2 and "eve" in listed.output


def test_a_rebound_name_is_refused_even_from_the_owners_address(tmp_path: Path) -> None:
    """DNS rebinding: evil.example points at Pablo's IP, the page runs in Matt's browser."""
    g = gate(tmp_path)
    with pytest.raises(Denied, match=r"evil\.example"):
        g.resolve(MATT, None, {"host": "evil.example:8710"})
    for host in ("100.101.102.103:8710", "[fd7a:115c:a1e0::1]:8710", "localhost:8710",
                 "pablo.tail1234.ts.net", "testserver"):  # fmt: skip
        assert g.resolve(MATT, None, {"host": host}).is_owner


def test_extra_names_come_from_the_access_list_and_the_cert(tmp_path: Path) -> None:
    g = Access(tmp_path, AccessConfig(hosts=["Pablo.Home.Arpa"]), whois=PEERS.get,
               self_login=lambda: "matt@github")  # fmt: skip
    assert g.host_ok("pablo.home.arpa:8710") and not g.host_ok("pablo.example")
    (tmp_path / "tls").mkdir()
    (tmp_path / "tls" / "name").write_text("pablo.example\n")
    assert g.host_ok("pablo.example")


def test_changes_only_come_from_pablos_own_pages(tmp_path: Path) -> None:
    g = gate(tmp_path)
    page = {"host": "100.64.0.1:8710"}
    with pytest.raises(Denied, match=r"evil\.example"):
        g.resolve(MATT, None, {**page, "origin": "https://evil.example"}, method="POST")
    with pytest.raises(Denied):
        g.resolve(MATT, None, {**page, "origin": "null"}, method="POST")
    assert g.resolve(MATT, None, {**page, "origin": "https://evil.example"}, method="GET")
    for origin in ("http://100.64.0.1:8710", "http://127.0.0.1:5173"):
        assert g.resolve(MATT, None, {**page, "origin": origin}, method="POST").is_owner
    assert g.resolve(MATT, None, page, method="POST").is_owner  # curl, the TUI: no Origin


def test_the_api_refuses_a_rebound_page(settings: Settings, repo: Path) -> None:
    orch, access = app_for(settings, repo)
    matt = TestClient(create_app(orch, access), client=(MATT, 5000))
    run = matt.post("/api/runs", json={"task": "add sub(a, b)", "lane": "coder"}).json()
    evil = {"host": "evil.example"}
    assert matt.get("/api/runs", headers=evil).status_code == 403
    assert matt.post(f"/api/runs/{run['id']}/approve", headers=evil).status_code == 403
    cross = {"origin": "https://evil.example"}
    assert matt.post(f"/api/runs/{run['id']}/approve", headers=cross).status_code == 403
    assert "def sub" not in (repo / "calc.py").read_text()


def test_a_teammate_cant_compare_and_their_queued_jobs_are_theirs(
    settings: Settings, repo: Path
) -> None:
    orch, access = app_for(settings, repo)
    app = create_app(orch, access)
    sam = TestClient(app, client=(SAM, 5000))
    assert sam.post("/api/compare", json={"task": "add sub"}).status_code == 403
    job = sam.post("/api/queue", json={"task": "add sub(a, b)"}).json()
    assert job["by"] == "sam@github"


def test_the_token_link_leaves_the_address_bar(settings: Settings, repo: Path) -> None:
    orch, access = app_for(settings, repo)
    local = TestClient(create_app(orch, access), client=("127.0.0.1", 5000))
    hop = local.get("/", params={"token": access.token(), "run": "abc"}, follow_redirects=False)
    assert hop.status_code == 303 and hop.headers["location"] == "/?run=abc"
    assert access.token() in hop.headers["set-cookie"]
    assert local.get("/api/runs").status_code == 200  # signed in by the cookie
