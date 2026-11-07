"""Who is asking: the owner, a teammate on the access list, or nobody.

Pablo binds to 127.0.0.1 or its Tailscale IP (never 0.0.0.0), so every request comes either
from this machine or from a tailnet peer.

- A tailnet peer is identified by asking tailscaled who owns the connecting IP
  (`tailscale whois`). WireGuard already proved that IP; nothing in the request can fake it.
- `Tailscale-User-*` headers are never trusted: anything that reaches the port can send them.
  A request that carries them came through `tailscale serve` (or claims to), which would make
  every visitor look local, so it is refused with a message saying how to set Pablo up instead.
- A local request (loopback, or Pablo's own Tailscale IP) is the owner when you work alone.
  Once teammates are on the list it needs the owner token in `~/.pablo/owner.token` (0600,
  readable only by your account), because another account on this box is also "local".

The access list lives in `~/.pablo/access.yaml`, not in the repo: logins are personal.

Teammates usually get on it through an invite (`pablo invite`): a one-time code in a link.
A tailnet peer that opens the link with a valid code is added to `members` under the login
tailscaled reports, so you never need to know their Tailscale login ahead of time.

    owner: you@github            # your Tailscale login
    members: [friend@github]     # teammates: chat, runs, their own memory; no approve
    loopback: auto               # auto · owner · token
    hosts: [pablo.home.arpa]     # extra names you open Pablo by (IPs, localhost, this
                                 # machine's hostname and *.ts.net always work)

Names matter because the owner is known by address, not by a password: a web page you have
open elsewhere could point its own domain at Pablo's IP (DNS rebinding) and then talk to Pablo
from your laptop, as you. Such a request carries that page's name in `Host`, so `/api` answers
only names Pablo knows, and a request that changes something must come from a page Pablo
served (`Origin`).
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import secrets
import socket
import subprocess
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

import yaml
from pydantic import BaseModel, Field

from pablo.convos import OWNER, Role

TAILNET_V4 = ipaddress.ip_network("100.64.0.0/10")
TAILNET_V6 = ipaddress.ip_network("fd7a:115c:a1e0::/48")
SERVE_HEADERS = ("tailscale-user-login", "tailscale-user-name", "tailscale-funnel-request")
TOKEN_COOKIE = "pablo_token"
# The Vite dev server (web/vite.config.ts) proxies /api from these pages.
DEV_ORIGINS = ("http://localhost:5173", "http://127.0.0.1:5173")
SAFE_METHODS = ("GET", "HEAD", "OPTIONS")

Whois = Callable[[str], "tuple[str, str] | None"]


class AccessConfig(BaseModel):
    owner: str = ""  # your Tailscale login; empty: whoever owns this machine on the tailnet
    members: list[str] = Field(default_factory=list)
    loopback: Literal["auto", "owner", "token"] = "auto"
    hosts: list[str] = Field(default_factory=list)  # extra names Pablo answers to

    @property
    def local_mode(self) -> Literal["owner", "token"]:
        if self.loopback == "auto":
            return "token" if self.members else "owner"
        return self.loopback


def load_access(home: Path) -> AccessConfig:
    path = home / "access.yaml"
    if not path.is_file():
        return AccessConfig()
    return AccessConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")) or {})


def owner_token(home: Path) -> str:
    """The local owner's token, created on first use, readable only by your account."""
    path = home / "owner.token"
    if not path.is_file():
        home.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(secrets.token_urlsafe(32))
    return path.read_text(encoding="utf-8").strip()


def tailscale_whois(ip: str) -> tuple[str, str] | None:
    """(login, display name) for a tailnet IP, from tailscaled. None if unknown or tagged."""
    try:
        out = subprocess.run(
            ["tailscale", "whois", "--json", ip],
            capture_output=True,
            text=True,
            timeout=3,
            check=True,
        ).stdout
        user = json.loads(out).get("UserProfile") or {}
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    login = str(user.get("LoginName", ""))
    if not login or login == "tagged-devices":
        return None
    return login, str(user.get("DisplayName", "") or login)


def tailscale_self_login() -> str:
    """The Tailscale login that owns this machine: the default owner, so no setup is needed."""
    try:
        out = subprocess.run(
            ["tailscale", "status", "--json", "--peers=false"],
            capture_output=True,
            text=True,
            timeout=3,
            check=True,
        ).stdout
        data = json.loads(out)
        uid = str((data.get("Self") or {}).get("UserID", ""))
        return str(((data.get("User") or {}).get(uid) or {}).get("LoginName", ""))
    except (OSError, subprocess.SubprocessError, ValueError, AttributeError):
        return ""


def add_member(home: Path, login: str) -> None:
    """Append a login to `members` in access.yaml, keeping its other settings."""
    path = home / "access.yaml"
    data: dict[str, Any] = {}
    if path.is_file():
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    members = [str(m) for m in data.get("members") or []]
    if login in members:
        return
    data["members"] = [*members, login]
    home.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def _digest(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


class Invites:
    """One-time invite codes in `~/.pablo/invites.json` (0600). Only their hashes are kept."""

    def __init__(self, home: Path) -> None:
        self.path = home / "invites.json"

    def _load(self) -> dict[str, dict[str, Any]]:
        if not self.path.is_file():
            return {}
        data = json.loads(self.path.read_text(encoding="utf-8") or "{}")
        return data if isinstance(data, dict) else {}

    def _save(self, data: dict[str, dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=1)

    def create(self, days: float = 7, note: str = "", now: float | None = None) -> str:
        """A new code, good once, for `days` days."""
        at = time.time() if now is None else now
        code = secrets.token_urlsafe(12)
        data = self._load()
        data[_digest(code)] = {
            "note": note,
            "created": at,
            "expires": at + days * 86400,
            "used_by": "",
        }
        self._save(data)
        return code

    def redeem(self, code: str, login: str, now: float | None = None) -> bool:
        """Spend a code on `login`. False if it's unknown, expired, or already used."""
        if not code:
            return False
        at = time.time() if now is None else now
        data = self._load()
        entry = data.get(_digest(code))
        if entry is None or entry.get("used_by") or at > float(entry.get("expires", 0)):
            return False
        entry["used_by"], entry["used_at"] = login, at
        self._save(data)
        return True

    def listing(self) -> list[dict[str, Any]]:
        return sorted(self._load().values(), key=lambda e: float(e.get("created", 0)))


class CachedWhois:
    def __init__(self, lookup: Whois = tailscale_whois, ttl_s: float = 300) -> None:
        self.lookup, self.ttl_s = lookup, ttl_s
        self._cache: dict[str, tuple[float, tuple[str, str] | None]] = {}

    def __call__(self, ip: str) -> tuple[str, str] | None:
        hit = self._cache.get(ip)
        if hit and time.monotonic() - hit[0] < self.ttl_s:
            return hit[1]
        found = self.lookup(ip)
        self._cache[ip] = (time.monotonic(), found)
        return found


@dataclass
class Caller:
    profile: str  # profile id: "owner" for you, the Tailscale login for a teammate
    role: Role
    name: str
    login: str = ""
    via: Literal["local", "tailnet"] = "local"

    @property
    def is_owner(self) -> bool:
        return self.role == "owner"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class Denied(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def host_name(header: str) -> str:
    """The name in a Host header, without its port: "pablo:8710" → "pablo", "[::1]:80" → "::1"."""
    header = header.strip().lower()
    if header.startswith("["):
        return header[1 : header.find("]")] if "]" in header else header
    if header.count(":") == 1:
        return header.split(":", 1)[0]
    return header


def _ip(host: str | None) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(host or "")
    except ValueError:
        return None


class Access:
    def __init__(
        self,
        home: Path,
        config: AccessConfig | None = None,
        whois: Whois | None = None,
        self_login: Callable[[], str] = tailscale_self_login,
    ) -> None:
        self.home = home
        self.config = config or load_access(home)
        self.whois = whois or CachedWhois()
        self._self_login = self_login
        self._owner: str | None = None

    @property
    def owner_login(self) -> str:
        """access.yaml's owner, else whoever owns this machine on the tailnet."""
        if self._owner is None:
            self._owner = self.config.owner or self._self_login()
        return self._owner

    def token(self) -> str:
        return owner_token(self.home)

    def names(self) -> set[str]:
        """Names Pablo answers to, besides any IP address."""
        found = {"localhost", socket.gethostname().lower()}
        cert = self.home / "tls" / "name"  # `pablo cert` writes the name it was issued for
        if cert.is_file():
            found.add(cert.read_text(encoding="utf-8").strip().lower())
        found.update(h.strip().lower() for h in self.config.hosts)
        return {n for n in found if n}

    def host_ok(self, header: str) -> bool:
        """Is this a name a browser could only have used to reach Pablo itself? An IP literal
        can't be rebound; a name must be one of `names()` or a Tailscale MagicDNS name."""
        name = host_name(header)
        if not name:
            return False
        if _ip(name) is not None:
            return True
        return name in self.names() or name.endswith(".ts.net")

    def origin_ok(self, origin: str) -> bool:
        if origin in DEV_ORIGINS:
            return True
        parts = urlsplit(origin)
        return parts.scheme in ("http", "https") and self.host_ok(parts.netloc)

    def resolve(
        self,
        client: str | None,
        server: str | None,
        headers: dict[str, str],
        token: str = "",
        method: str = "GET",
    ) -> Caller:
        """Identify one request or raise Denied. `headers` keys are lower-case.

        A `host` header must name Pablo (see `host_ok`), and a request that changes something
        and says where it came from (`origin`) must come from a page Pablo serves.
        """
        host = headers.get("host")
        if host is not None and not self.host_ok(host):
            raise Denied(
                403,
                f"Pablo doesn't answer to the name {host_name(host)!r}. Open it by its IP or "
                "Tailscale name, or add the name to hosts in ~/.pablo/access.yaml.",
            )
        origin = headers.get("origin")
        if method.upper() not in SAFE_METHODS and origin is not None and not self.origin_ok(origin):
            raise Denied(403, f"Pablo only takes changes from its own pages, not {origin}")
        if any(h in headers for h in SERVE_HEADERS):
            raise Denied(
                403,
                "Pablo doesn't take identity from tailscale serve or funnel headers. "
                "Run `pablo serve --host <tailscale ip>` and open that address instead.",
            )
        ip = _ip(client)
        if ip is None:
            raise Denied(403, "unknown client address")
        if ip.is_loopback or (server is not None and client == server):
            if self.config.local_mode == "owner":
                return Caller(OWNER, "owner", "Owner")
            if token and secrets.compare_digest(token, self.token()):
                return Caller(OWNER, "owner", "Owner")
            raise Denied(401, "Local requests need the owner token: run `pablo token`.")
        if ip in TAILNET_V4 or ip in TAILNET_V6:
            found = self.whois(str(ip))
            if found is None:
                raise Denied(403, "tailscale couldn't say who this device belongs to")
            login, name = found
            if self.owner_login and login == self.owner_login:
                return Caller(OWNER, "owner", name, login, "tailnet")
            if login in self.config.members:
                return Caller(login, "member", name, login, "tailnet")
            raise Denied(403, f"{login} isn't on Pablo's access list (~/.pablo/access.yaml)")
        raise Denied(403, "Pablo only answers this machine and your tailnet")

    def join(
        self,
        client: str | None,
        server: str | None,
        headers: dict[str, str],
        code: str,
    ) -> Caller:
        """Let a tailnet peer in with an invite code; anyone already allowed just passes."""
        try:
            return self.resolve(client, server, headers)
        except Denied as denied:
            ip = _ip(client)
            if ip is None or not (ip in TAILNET_V4 or ip in TAILNET_V6):
                raise
            found = self.whois(str(ip))
            if found is None or denied.status != 403:
                raise
            login, name = found
            if not Invites(self.home).redeem(code, login):
                raise Denied(
                    403, "This invite link was already used or has expired. Ask for a new one."
                ) from denied
            add_member(self.home, login)
            if login not in self.config.members:
                self.config.members.append(login)
            return Caller(login, "member", name, login, "tailnet")
