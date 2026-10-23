"""Work out how to reach DataForSEO, whatever the local setup happens to be.

Precedence (first one that answers wins):

1. A host-side secrets proxy, when reachable — it holds the keys and injects
   auth itself, so nothing here ever sees a credential. Skipped when
   ``SECRETS_PROXY_DISABLE=1``.
2. Credentials in the environment, under any of the common variable names.
3. A pre-encoded ``DATAFORSEO_AUTH`` / ``DATAFORSEO_BASE64`` token (what
   DataForSEO's own docs hand you).
4. A ``.env`` file, searched in the tool directory, the working directory and
   its parents, and the usual per-user config spots.

The point of the chain is that someone can install this without telling us how
their credentials are stored. ``faqgen.py --check`` reports which route was
found and proves it works against a free endpoint.
"""

import base64
import os
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

import requests

from . import FaqgenError

TOOL_DIR = Path(__file__).resolve().parent.parent
UPSTREAM_BASE = "https://api.dataforseo.com"
# Same endpoints and response structure, placeholder values, never charged.
# The right way to prove a setup works end to end before spending anything.
SANDBOX_BASE = "https://sandbox.dataforseo.com"
DEFAULT_PROXY_BASE = "http://host.docker.internal:8900"

# Variable names seen in the wild. Checked in order, first match wins.
LOGIN_VARS = (
    "DATAFORSEO_LOGIN", "DATAFORSEO_USERNAME", "DATAFORSEO_USER",
    "DATAFORSEO_EMAIL", "DFS_LOGIN", "DFS_USERNAME",
)
PASSWORD_VARS = (
    "DATAFORSEO_PASSWORD", "DATAFORSEO_PASS", "DATAFORSEO_KEY",
    "DATAFORSEO_API_KEY", "DFS_PASSWORD", "DFS_PASS",
)
TOKEN_VARS = ("DATAFORSEO_AUTH", "DATAFORSEO_BASE64", "DATAFORSEO_TOKEN")


@dataclass
class Transport:
    """How to reach the API, and where that route came from."""
    kind: str                                  # "proxy", "managed", or "direct"
    base_url: str
    auth: Optional[Tuple[str, str]] = None     # basic auth, direct route only
    source: str = ""                           # human-readable provenance

    def url_for(self, endpoint: str) -> str:
        """Full URL for an upstream path like /v3/serp/google/organic/live/advanced."""
        if self.kind == "proxy":
            return f"{self.base_url}/dataforseo{endpoint}"
        return f"{self.base_url}{endpoint}"

    @property
    def is_sandbox(self) -> bool:
        return self.base_url.startswith(SANDBOX_BASE)

    def describe(self) -> str:
        if self.kind == "proxy":
            return f"secrets proxy at {self.base_url} (no credentials needed) — {self.source}"
        if self.kind == "managed":
            return f"{self.base_url} — {self.source}"
        where = "DataForSEO sandbox (free, placeholder data)" if self.is_sandbox else self.base_url
        return f"direct to {where}, credentials from {self.source}"


def env_file_candidates() -> List[Path]:
    """Places a .env might plausibly live, most specific first."""
    cwd = Path.cwd()
    paths = [TOOL_DIR / ".env", cwd / ".env"]
    paths += [parent / ".env" for parent in list(cwd.parents)[:3]]
    paths += [
        Path.home() / ".dataforseo.env",
        Path.home() / ".config" / "dataforseo" / ".env",
    ]
    seen, out = set(), []
    for path in paths:
        if path not in seen:
            seen.add(path)
            out.append(path)
    return out


def _load_env_files() -> Tuple[dict, Optional[Path]]:
    """First .env that defines something we recognise. Returns (values, path)."""
    try:
        from dotenv import dotenv_values
    except ImportError:
        return {}, None
    known = set(LOGIN_VARS) | set(PASSWORD_VARS) | set(TOKEN_VARS)
    for path in env_file_candidates():
        if not path.is_file():
            continue
        try:
            values = {k: v for k, v in dotenv_values(path).items() if v}
        except OSError:
            continue
        if known & set(values):
            return values, path
    return {}, None


def _first(mapping, names) -> str:
    for name in names:
        value = (mapping.get(name) or "").strip()
        if value:
            return value
    return ""


def _decode_token(token: str) -> Optional[Tuple[str, str]]:
    """A base64 login:password token, as DataForSEO's own docs present it."""
    try:
        decoded = base64.b64decode(token, validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None
    if ":" not in decoded:
        return None
    login, password = decoded.split(":", 1)
    return (login, password) if login and password else None


def proxy_available(base_url: str, timeout: float = 3.0) -> bool:
    """Any HTTP answer counts — the question is whether something is listening."""
    try:
        requests.get(base_url, timeout=timeout)
    except requests.RequestException:
        return False
    return True


def credentials_attached_in_transit(base_url: str, timeout: float = 10.0) -> bool:
    """Does something outside this process authenticate our requests for us?

    Claude Code's cloud environments can hold an API credential that Anthropic's
    agent proxy attaches after a request leaves the session, so the key never
    reaches the session at all. Other egress proxies do the same. There is no
    flag to read for this, so we ask the API: call the free account endpoint with
    no credentials and see whether it answers as though we had them.

    Costs nothing, and only runs when no local credentials were found.
    """
    try:
        response = requests.get(
            f"{base_url}/v3/appendix/user_data",
            headers={"Content-Type": "application/json"},
            timeout=timeout,
        )
    except requests.RequestException:
        return False
    if response.status_code != 200:
        return False
    try:
        return response.json().get("status_code") == 20000
    except ValueError:
        return False


def resolve_transport(sandbox: bool = False) -> Transport:
    """Find a usable route, or raise FaqgenError explaining how to make one.

    In sandbox mode the proxy is skipped: a secrets proxy forwards to the live
    host, so reaching the sandbox needs credentials of your own.
    """
    base = SANDBOX_BASE if sandbox else UPSTREAM_BASE

    if sandbox:
        transport = _direct_transport(base)
        if transport:
            return transport
        raise FaqgenError(
            "The sandbox needs DataForSEO credentials of its own.\n\n"
            "A secrets proxy forwards to the live API, so it can't reach the "
            "sandbox host. Set your API login and password directly:\n"
            "    export DATAFORSEO_LOGIN='your-api-login'\n"
            "    export DATAFORSEO_PASSWORD='your-api-password'\n\n"
            "They're the same credentials as live — the sandbox just never "
            "charges. Get them from https://app.dataforseo.com/api-access"
        )

    if os.environ.get("SECRETS_PROXY_DISABLE", "") != "1":
        configured = os.environ.get("SECRETS_PROXY_BASE", "")
        proxy_base = (configured or DEFAULT_PROXY_BASE).rstrip("/")
        if proxy_available(proxy_base):
            source = "SECRETS_PROXY_BASE" if configured else "default devcontainer address"
            return Transport(kind="proxy", base_url=proxy_base, source=source)

    transport = _direct_transport(base)
    if transport:
        return transport

    # Nothing local. Before giving up, check whether the platform is
    # authenticating for us — the Claude Code cloud-environment case, where the
    # key is deliberately kept out of the session.
    if credentials_attached_in_transit(base):
        return Transport(
            kind="managed", base_url=base,
            source="credentials attached in transit by the environment "
                   "(the key never enters this session)",
        )

    raise FaqgenError(setup_help())


def _direct_transport(base_url: str) -> Optional[Transport]:
    """Credentials from the environment, a token, or a .env — in that order."""
    login, password = _first(os.environ, LOGIN_VARS), _first(os.environ, PASSWORD_VARS)
    if login and password:
        return Transport(kind="direct", base_url=base_url,
                         auth=(login, password), source="environment variables")

    token = _first(os.environ, TOKEN_VARS)
    if token:
        pair = _decode_token(token)
        if pair:
            return Transport(kind="direct", base_url=base_url, auth=pair,
                             source="encoded token in the environment")

    values, path = _load_env_files()
    if values:
        login, password = _first(values, LOGIN_VARS), _first(values, PASSWORD_VARS)
        if login and password:
            return Transport(kind="direct", base_url=base_url,
                             auth=(login, password), source=str(path))
        token = _first(values, TOKEN_VARS)
        if token:
            pair = _decode_token(token)
            if pair:
                return Transport(kind="direct", base_url=base_url, auth=pair,
                                 source=f"encoded token in {path}")
    return None


def setup_help() -> str:
    looked_in = "\n".join(f"    {p}" for p in env_file_candidates()[:5])
    return (
        "Couldn't find a way to reach DataForSEO.\n\n"
        "Any one of these works — pick whichever suits your setup:\n\n"
        "  1. Export the credentials in your shell:\n"
        "       export DATAFORSEO_LOGIN='your-api-login'\n"
        "       export DATAFORSEO_PASSWORD='your-api-password'\n\n"
        "  2. Put them in a .env file (gitignore it), e.g. next to this tool:\n"
        f"       {TOOL_DIR / '.env'}\n"
        "     Copy .env.example to .env and fill it in.\n\n"
        "  3. If DataForSEO gave you a single base64 token:\n"
        "       export DATAFORSEO_AUTH='<the token>'\n\n"
        "  4. If you run a secrets proxy, point the tool at it:\n"
        "       export SECRETS_PROXY_BASE='http://your-host:8900'\n\n"
        "  5. On Claude Code for the web (Pro/Max), add an API credential to the\n"
        "     cloud environment for host api.dataforseo.com, header\n"
        "     Authorization, prefix Basic, value = base64 of 'login:password'.\n"
        "     The key is attached after requests leave the session, so it never\n"
        "     reaches this tool or the conversation. Nothing to set here.\n"
        "     Not offered on Team or Enterprise plans yet.\n\n"
        "Credentials come from https://app.dataforseo.com/api-access\n\n"
        "We looked for a .env in:\n" + looked_in + "\n\n"
        "Once set up, run 'python faqgen.py --check' to confirm it works."
    )
