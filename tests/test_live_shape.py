"""Tests against a real DataForSEO response captured 2026-09-23.

The hand-written fixture in test_faqgen.py covers the documented shape. This
one covers what the API actually returns now: PAA expansions that arrive as
`people_also_ask_ai_overview_expanded_element` with everything null, and an
`ai_overview` block that carries the citations instead.
"""

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from faqgen_lib.credentials import (
    Transport,
    _decode_token,
    env_file_candidates,
    resolve_transport,
    setup_help,
)
from faqgen_lib.serp import citation_provenance, parse_serp

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "serp_live_2026-09-23.json").read_text()
)


@pytest.fixture
def serp():
    return parse_serp(FIXTURE, "walk in tub cost")


# ── The live PAA shape ───────────────────────────────────────────────────────

def test_questions_parsed_from_live_response(serp):
    assert len(serp.paa) == 4
    assert serp.paa[0].question == "Will Medicare pay for a walk-in tub?"
    assert all(q.question.endswith("?") for q in serp.paa)


def test_live_paa_expansions_carry_no_source(serp):
    """The regression this file exists for: nulls must not crash or fabricate."""
    assert all(q.answer_text == "" for q in serp.paa)
    assert all(q.source_domain == "" for q in serp.paa)


def test_ranks_are_sequential(serp):
    assert [q.rank for q in serp.paa] == [1, 2, 3, 4]


# ── AI Overview provenance ───────────────────────────────────────────────────

def test_ai_overview_references_parsed(serp):
    assert len(serp.ai_overview_refs) == 4
    assert serp.ai_overview_refs[0].domain == "www.ncoa.org"
    assert all(r.domain for r in serp.ai_overview_refs)


def test_provenance_prefers_ai_overview(serp):
    refs = citation_provenance(serp, limit=3)
    assert len(refs) == 3
    assert refs[0].domain == "www.ncoa.org"


def test_provenance_falls_back_to_organic_without_ai_overview(serp):
    serp.ai_overview_refs = []
    refs = citation_provenance(serp, limit=2)
    assert [r.domain for r in refs] == [o.domain for o in serp.organic[:2]]


def test_cost_read_from_response(serp):
    assert serp.cost == pytest.approx(0.0038)


# ── Credential resolution ────────────────────────────────────────────────────

def test_proxy_wins_when_reachable(monkeypatch):
    monkeypatch.setattr("faqgen_lib.credentials.proxy_available", lambda *a, **k: True)
    monkeypatch.setenv("SECRETS_PROXY_BASE", "http://proxy.test:8900")
    monkeypatch.setenv("DATAFORSEO_LOGIN", "x")
    monkeypatch.setenv("DATAFORSEO_PASSWORD", "y")
    t = resolve_transport()
    assert t.kind == "proxy"
    assert t.auth is None
    assert t.url_for("/v3/x") == "http://proxy.test:8900/dataforseo/v3/x"


def test_proxy_can_be_disabled(monkeypatch):
    monkeypatch.setattr("faqgen_lib.credentials.proxy_available", lambda *a, **k: True)
    monkeypatch.setenv("SECRETS_PROXY_DISABLE", "1")
    monkeypatch.setenv("DATAFORSEO_LOGIN", "user")
    monkeypatch.setenv("DATAFORSEO_PASSWORD", "pass")
    t = resolve_transport()
    assert t.kind == "direct"
    assert t.auth == ("user", "pass")


@pytest.mark.parametrize("login_var,pw_var", [
    ("DATAFORSEO_LOGIN", "DATAFORSEO_PASSWORD"),
    ("DATAFORSEO_USERNAME", "DATAFORSEO_PASS"),
    ("DFS_LOGIN", "DFS_PASSWORD"),
])
def test_alternative_variable_names(monkeypatch, login_var, pw_var):
    """Clients name these differently; accept the common variants."""
    monkeypatch.setattr("faqgen_lib.credentials.proxy_available", lambda *a, **k: False)
    for name in ("DATAFORSEO_LOGIN", "DATAFORSEO_PASSWORD", "DATAFORSEO_USERNAME",
                 "DATAFORSEO_PASS", "DFS_LOGIN", "DFS_PASSWORD", "DATAFORSEO_AUTH"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(login_var, "user")
    monkeypatch.setenv(pw_var, "secret")
    assert resolve_transport().auth == ("user", "secret")


def test_base64_token_accepted(monkeypatch):
    import base64
    monkeypatch.setattr("faqgen_lib.credentials.proxy_available", lambda *a, **k: False)
    for name in ("DATAFORSEO_LOGIN", "DATAFORSEO_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    token = base64.b64encode(b"me@example.com:s3cret").decode()
    monkeypatch.setenv("DATAFORSEO_AUTH", token)
    assert resolve_transport().auth == ("me@example.com", "s3cret")


def test_garbage_token_rejected():
    assert _decode_token("not-base64!!") is None
    assert _decode_token("aGVsbG8=") is None  # decodes, but has no colon


def test_helpful_error_when_nothing_configured(monkeypatch, tmp_path):
    monkeypatch.setattr("faqgen_lib.credentials.proxy_available", lambda *a, **k: False)
    monkeypatch.setattr("faqgen_lib.credentials.env_file_candidates", lambda: [])
    for name in ("DATAFORSEO_LOGIN", "DATAFORSEO_PASSWORD", "DATAFORSEO_USERNAME",
                 "DATAFORSEO_PASS", "DFS_LOGIN", "DFS_PASSWORD", "DATAFORSEO_AUTH",
                 "DATAFORSEO_BASE64", "DATAFORSEO_TOKEN", "DATAFORSEO_USER",
                 "DATAFORSEO_EMAIL", "DFS_USERNAME", "DATAFORSEO_KEY",
                 "DATAFORSEO_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    from faqgen_lib import FaqgenError
    with pytest.raises(FaqgenError) as exc:
        resolve_transport()
    message = str(exc.value)
    assert "DATAFORSEO_LOGIN" in message
    assert "app.dataforseo.com/api-access" in message
    assert "--check" in message


def test_env_candidates_include_tool_dir_and_home():
    paths = [str(p) for p in env_file_candidates()]
    assert any(p.endswith("faq-generator/.env") for p in paths)
    assert any(".dataforseo.env" in p for p in paths)


def test_transport_url_building():
    direct = Transport(kind="direct", base_url="https://api.dataforseo.com")
    assert direct.url_for("/v3/x") == "https://api.dataforseo.com/v3/x"


# ── Sandbox mode ─────────────────────────────────────────────────────────────

def test_sandbox_uses_sandbox_host(monkeypatch):
    monkeypatch.setattr("faqgen_lib.credentials.proxy_available", lambda *a, **k: True)
    monkeypatch.setenv("DATAFORSEO_LOGIN", "user")
    monkeypatch.setenv("DATAFORSEO_PASSWORD", "pass")
    t = resolve_transport(sandbox=True)
    assert t.is_sandbox
    assert t.url_for("/v3/x") == "https://sandbox.dataforseo.com/v3/x"


def test_sandbox_skips_proxy_even_when_available(monkeypatch):
    """A secrets proxy forwards to live, so it cannot serve the sandbox."""
    monkeypatch.setattr("faqgen_lib.credentials.proxy_available", lambda *a, **k: True)
    monkeypatch.setenv("DATAFORSEO_LOGIN", "user")
    monkeypatch.setenv("DATAFORSEO_PASSWORD", "pass")
    assert resolve_transport(sandbox=True).kind == "direct"


def test_sandbox_without_credentials_explains_why(monkeypatch):
    from faqgen_lib import FaqgenError
    monkeypatch.setattr("faqgen_lib.credentials.proxy_available", lambda *a, **k: True)
    monkeypatch.setattr("faqgen_lib.credentials.env_file_candidates", lambda: [])
    for name in ("DATAFORSEO_LOGIN", "DATAFORSEO_PASSWORD", "DATAFORSEO_AUTH",
                 "DATAFORSEO_USERNAME", "DATAFORSEO_PASS", "DFS_LOGIN",
                 "DFS_PASSWORD", "DATAFORSEO_USER", "DATAFORSEO_EMAIL",
                 "DFS_USERNAME", "DATAFORSEO_KEY", "DATAFORSEO_API_KEY",
                 "DATAFORSEO_BASE64", "DATAFORSEO_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(FaqgenError, match="sandbox needs DataForSEO credentials"):
        resolve_transport(sandbox=True)


def test_live_route_is_not_flagged_as_sandbox(monkeypatch):
    monkeypatch.setattr("faqgen_lib.credentials.proxy_available", lambda *a, **k: True)
    assert not resolve_transport().is_sandbox


def test_sandbox_output_is_labelled_and_schema_renamed(serp, tmp_path):
    """Sandbox artifacts must be impossible to mistake for a deliverable."""
    from faqgen_lib.page import PageContent
    from faqgen_lib.render import render_markdown, write_outputs

    page = PageContent(url="https://x.com/p", title="T", h1="T")
    page.paragraphs = [("Some page text that is long enough to count here.", "H")]

    md = render_markdown(page, [], "informational", "test", 0.0, [], sandbox=True)
    assert "SANDBOX OUTPUT, NOT REAL DATA" in md
    assert "not a deliverable" in md

    written = write_outputs(
        tmp_path, page, [], "informational", "test", 0.0, {}, [], sandbox=True
    )
    names = [p.name for p in written]
    assert not [n for n in names if n.endswith(".jsonld")]   # no markup at all


def _clear_creds(monkeypatch):
    for name in ("DATAFORSEO_LOGIN", "DATAFORSEO_PASSWORD", "DATAFORSEO_AUTH",
                 "DATAFORSEO_USERNAME", "DATAFORSEO_PASS", "DFS_LOGIN",
                 "DFS_PASSWORD", "DATAFORSEO_USER", "DATAFORSEO_EMAIL",
                 "DFS_USERNAME", "DATAFORSEO_KEY", "DATAFORSEO_API_KEY",
                 "DATAFORSEO_BASE64", "DATAFORSEO_TOKEN"):
        monkeypatch.delenv(name, raising=False)


def test_managed_credentials_detected_when_nothing_local(monkeypatch):
    """Claude Code cloud environments attach the key after it leaves the session."""
    monkeypatch.setattr("faqgen_lib.credentials.proxy_available", lambda *a, **k: False)
    monkeypatch.setattr("faqgen_lib.credentials.env_file_candidates", lambda: [])
    monkeypatch.setattr(
        "faqgen_lib.credentials.credentials_attached_in_transit", lambda *a, **k: True
    )
    _clear_creds(monkeypatch)
    t = resolve_transport()
    assert t.kind == "managed"
    assert t.auth is None
    assert t.url_for("/v3/x") == "https://api.dataforseo.com/v3/x"
    assert "never enters this session" in t.describe()


def test_local_credentials_win_over_probing(monkeypatch):
    """No point making a network call when we already hold a credential."""
    calls = []
    monkeypatch.setattr("faqgen_lib.credentials.proxy_available", lambda *a, **k: False)
    monkeypatch.setattr(
        "faqgen_lib.credentials.credentials_attached_in_transit",
        lambda *a, **k: calls.append(1) or True,
    )
    _clear_creds(monkeypatch)
    monkeypatch.setenv("DATAFORSEO_LOGIN", "u")
    monkeypatch.setenv("DATAFORSEO_PASSWORD", "p")
    assert resolve_transport().kind == "direct"
    assert calls == [], "should not probe when credentials are already in hand"


def test_probe_rejects_unauthenticated_failure(monkeypatch):
    """A 401 means nothing is authenticating for us — not a managed route."""
    import requests as rq
    from faqgen_lib.credentials import credentials_attached_in_transit

    class _Resp:
        status_code = 401
        def json(self): return {"status_code": 40101}

    monkeypatch.setattr(rq, "get", lambda *a, **k: _Resp())
    assert credentials_attached_in_transit("https://api.dataforseo.com") is False


def test_probe_survives_no_network(monkeypatch):
    import requests as rq
    from faqgen_lib.credentials import credentials_attached_in_transit

    def _boom(*a, **k):
        raise rq.ConnectionError("no network")

    monkeypatch.setattr(rq, "get", _boom)
    assert credentials_attached_in_transit("https://api.dataforseo.com") is False


def test_credentials_never_appear_in_diagnostics(monkeypatch):
    """A credential must never reach anything a user might paste or share.

    Error text and route descriptions go into terminals, transcripts and
    screenshots. They may name where a credential came from, never what it is.
    """
    secret_user, secret_pass = "client@example.com", "sup3r-s3cret-value"
    monkeypatch.setattr("faqgen_lib.credentials.proxy_available", lambda *a, **k: False)
    monkeypatch.setenv("DATAFORSEO_LOGIN", secret_user)
    monkeypatch.setenv("DATAFORSEO_PASSWORD", secret_pass)

    transport = resolve_transport()
    assert transport.auth == (secret_user, secret_pass)  # it is carried...
    assert secret_pass not in transport.describe()       # ...but never shown
    assert secret_user not in transport.describe()
    assert secret_pass not in repr(transport.source)
    assert secret_pass not in setup_help()


def test_auth_failure_message_carries_no_credentials():
    import requests

    from faqgen_lib import FaqgenError
    from faqgen_lib.serp import DataForSEOClient

    secret = "another-s3cret"
    transport = Transport(kind="direct", base_url="https://api.dataforseo.com",
                          auth=("u@example.com", secret), source="environment variables")
    client = DataForSEOClient(transport)

    response = requests.Response()
    response.status_code = 401
    error = requests.HTTPError("401 Client Error", response=response)
    client.session.get = lambda *a, **k: (_ for _ in ()).throw(error)

    with pytest.raises(FaqgenError) as exc:
        client.check()
    message = str(exc.value)
    assert secret not in message
    assert "u@example.com" not in message
    assert "api-access" in message  # but it does say how to fix it


def test_live_output_has_no_sandbox_banner(tmp_path):
    from faqgen_lib.page import PageContent
    from faqgen_lib.render import render_markdown, write_outputs

    page = PageContent(url="https://x.com/p", title="T", h1="T")
    md = render_markdown(page, [], "informational", "test", 0.004, [])
    assert "SANDBOX" not in md
    written = write_outputs(tmp_path, page, [], "informational", "test", 0.004, {}, [])
    assert [p.name for p in written] == ["faq-draft.md", "finish-in-claude.md", "raw.json"]
