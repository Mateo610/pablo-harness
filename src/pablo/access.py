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

    owner: you@github            # your Tailscale login
    members: [friend@github]     # teammates: chat, runs, their own memory; no approve
    loopback: auto               # auto · owner · token
"""

from __future__ import annotations

import ipaddress
import json
import os
import secrets
import subprocess
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

from pablo.convos import OWNER, Role

TAILNET_V4 = ipaddress.ip_network("100.64.0.0/10")
TAILNET_V6 = ipaddress.ip_network("fd7a:115c:a1e0::/48")
SERVE_HEADERS = ("tailscale-user-login", "tailscale-user-name", "tailscale-funnel-request")
TOKEN_COOKIE = "pablo_token"

Whois = Callable[[str], "tuple[str, str] | None"]


class AccessConfig(BaseModel):
    owner: str = ""  # your Tailscale login; empty: whoever owns this machine on the tailnet
    members: list[str] = Field(default_factory=list)
    loopback: Literal["auto", "owner", "token"] = "auto"

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

    def resolve(
        self,
        client: str | None,
        server: str | None,
        headers: dict[str, str],
        token: str = "",
    ) -> Caller:
        """Identify one request or raise Denied. `headers` keys are lower-case."""
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
