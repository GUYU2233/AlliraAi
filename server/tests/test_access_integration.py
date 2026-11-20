"""In-process Access origin/JWT/CSRF regressions; no real Cloudflare login or network."""

import hashlib
import sqlite3
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from joserfc import jwk, jwt
from pydantic import ValidationError

from allira.access_identity import AccessVerifier
from allira.admin import COOKIE_NAME, admin_asset_paths
from allira.admin_cli import main as admin_main
from allira.admin_db import AdminRepository, token_hash
from allira.app import create_app
from allira.backup_cli import REVOKE_TABLES, create_backup, restore_backup
from allira.config import Settings
from allira.db import APIError, Database
from allira.schemas import ConversationInput

ORIGIN = "https://dsah.guyufl.cc.cd"
ISSUER = "https://synthetic-team.cloudflareaccess.com"
AUDIENCE = "b" * 64
EMAIL = "operator@example.invalid"
SUBJECT = "synthetic-access-subject"
JS = "/console-assets/admin-synthetic123.js"
CSS = "/console-assets/admin-synthetic123.css"
ASSERTION = "Cf-Access-Jwt-Assertion"


def settings_for(tmp_path, **changes):
    return Settings(_env_file=None, **{
        "database_path": str(tmp_path / "synthetic.sqlite3"), "provider": "mock",
        "auth_backend": "oidc", "allow_dev_auth": False, "admin_enabled": True,
        "admin_auth_backend": "access", "console_enabled": True,
        "console_dist_path": str(tmp_path / "dist"), "admin_public_origin": ORIGIN,
        "access_team_domain": ISSUER, "access_audience": AUDIENCE,
        "access_allowed_emails": [EMAIL], **changes,
    })


def build_static(root):
    root = Path(root)
    (root / "admin").mkdir(parents=True)
    (root / "test").mkdir()
    (root / "assets").mkdir()
    (root / "admin" / "index.html").write_text(
        f'<html><link href="{CSS}" rel="stylesheet"><script src="{JS}"></script></html>')
    (root / "test" / "index.html").write_text("private test console")
    (root / "test" / "sw.js").write_text("// public shell worker, not an admin asset")
    for name in (JS, CSS, "/console-assets/public-chat-synthetic.js", "/console-assets/admin-old.js"):
        (root / "assets" / name.rsplit("/", 1)[-1]).write_text("synthetic static asset")


class SyntheticAccess:
    def __init__(self, key):
        self.key, self.calls = key, 0

    def assertion(self, **changes):
        stamp = time.time()
        body = {"iss": ISSUER, "aud": [AUDIENCE], "sub": SUBJECT, "email": EMAIL,
                "iat": stamp - 10, "exp": stamp + 600, "type": "app", **changes}
        return jwt.encode({"alg": "RS256", "kid": self.key.kid}, body, self.key)

    def transport(self, request):
        self.calls += 1
        assert request.method == "GET" and str(request.url) == ISSUER + "/cdn-cgi/access/certs"
        assert not request.headers.get("authorization") and not request.headers.get("cookie")
        return httpx.Response(200, json={"keys": [self.key.as_dict(private=False)]},
                              headers={"cache-control": "max-age=300"})


@pytest.fixture(scope="module")
def key():
    return jwk.RSAKey.generate_key(2048, parameters={"kid": "integration-key", "alg": "RS256"})


@pytest.fixture
def client(tmp_path, key):
    configured = settings_for(tmp_path)
    build_static(configured.console_dist_path)
    app = create_app(configured)
    issuer = SyntheticAccess(key)
    with TestClient(app, base_url=ORIGIN, client=("127.0.0.1", 12345),
                    raise_server_exceptions=False) as client:
        client.portal.call(app.state.access_verifier.aclose)
        app.state.access_verifier = AccessVerifier(
            configured, transport=httpx.MockTransport(issuer.transport),
        )
        client.synthetic_issuer = issuer
        client.configured = configured
        yield client


def headers(client, **changes):
    return {ASSERTION: client.synthetic_issuer.assertion(**changes)}


def login(client, assertion=None):
    proof = assertion or client.synthetic_issuer.assertion()
    response = client.get("/api/v1/admin/session", headers={ASSERTION: proof})
    assert response.status_code == 200, response.text
    return proof, response


def test_defaults_fail_closed_without_old_link_or_ambient_headers(tmp_path, monkeypatch):
    configured = Settings(_env_file=None, admin_auth_backend="access", admin_enabled=True,
                          database_path=str(tmp_path / "unconfigured.sqlite3"))
    with TestClient(create_app(configured), base_url="http://127.0.0.1:8000",
                    client=("127.0.0.1", 1)) as client:
        assert client.get("/api/v1/admin/session").status_code == 401
        assert client.get("/api/v1/admin/session", headers={ASSERTION: "synthetic"}).status_code == 503
        for extra in ({"Cf-Access-Authenticated-User-Email": EMAIL},
                      {"Authorization": "Bearer ordinary-user"}, {"Cookie": "CF_Authorization=synthetic"}):
            assert client.get("/api/v1/admin/overview", headers=extra).status_code == 401
        assert client.post("/api/v1/admin/session", json={"code": "x" * 43},
                           headers={"Origin": "http://127.0.0.1:8000"}).status_code == 403
        with pytest.raises(APIError) as failure:
            AdminRepository(client.app.state.db).issue_login("old-code", 300)
        assert failure.value.code == "admin_link_disabled"
        assert client.app.state.db.conn.execute("SELECT COUNT(*) FROM admin_sessions").fetchone()[0] == 0
    monkeypatch.delenv("ALLIRA_ADMIN_AUTH_BACKEND")
    assert Settings(_env_file=None).admin_auth_backend == "access"


def test_verified_human_creates_secure_bound_csrf_cookie_only(client):
    proof, response = login(client)
    body = response.json()
    assert set(body) == {"authenticated", "csrfToken", "expiresAt", "authentication", "identity", "logoutUrl"}
    assert body["authentication"] == "cloudflare-access" and body["identity"] == {"email": EMAIL}
    assert body["logoutUrl"] == "/cdn-cgi/access/logout"
    cookie = response.headers["set-cookie"]
    assert "Secure" in cookie and "HttpOnly" in cookie and "SameSite=strict" in cookie
    assert "Path=/api/v1/admin" in cookie and "Domain=" not in cookie
    assert SUBJECT not in response.text and proof not in response.text
    db = client.app.state.db
    row = db.conn.execute("SELECT * FROM admin_access_sessions").fetchone()
    assert row["subjectHash"] == token_hash(SUBJECT) and row["emailHash"] == token_hash(EMAIL)
    assert row["assertionDigest"] == hashlib.sha256(proof.encode()).hexdigest()
    assert row["expiresAt"] <= time.time() + 600
    text = "\n".join(db.conn.iterdump())
    for secret in (proof, SUBJECT, EMAIL, client.cookies.get(COOKIE_NAME)):
        assert secret not in text
    assert db.conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
    assert db.conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 0
    assert client.synthetic_issuer.calls == 1


def test_every_request_requires_assertion_and_exact_cookie_binding(client):
    proof, _ = login(client)
    endpoint = "/api/v1/admin/overview"
    assert client.get(endpoint, headers={ASSERTION: proof}).status_code == 200
    assert client.get(endpoint).status_code == 401
    assert client.get(endpoint, headers={ASSERTION: "not-a-jwt"}).status_code == 401
    for replacement in (headers(client, sub="another-subject"),
                        headers(client, iat=time.time() - 20),
                        headers(client, email="not-allowed@example.invalid")):
        assert client.get(endpoint, headers=replacement).status_code == 401
    assert client.get(endpoint, headers={ASSERTION: proof}).status_code == 200
    client.cookies.clear()
    assert client.get(endpoint, headers={ASSERTION: proof}).status_code == 401
    # Even a valid pre-cutover Cookie has no Access binding and cannot authenticate.
    with client.app.state.db.atomic():
        client.app.state.db.conn.execute("INSERT INTO admin_sessions VALUES (?, ?)",
                                         (token_hash("old-cookie"), time.time() + 600))
    client.cookies.set(COOKIE_NAME, "old-cookie", path="/api/v1/admin")
    assert client.get(endpoint, headers={ASSERTION: proof}).status_code == 401


def test_changed_assertion_explicit_get_rotates_local_proof_and_logout_needs_both(client):
    old_proof, first = login(client)
    old_cookie = client.cookies.get(COOKIE_NAME)
    new_proof = client.synthetic_issuer.assertion(iat=time.time() - 30)
    assert client.get("/api/v1/admin/overview", headers={ASSERTION: new_proof}).status_code == 401
    _, second = login(client, new_proof)
    assert second.json()["csrfToken"] != first.json()["csrfToken"]
    assert client.cookies.get(COOKIE_NAME) != old_cookie
    for bad in ({ASSERTION: new_proof},
                {ASSERTION: new_proof, "Origin": "https://attacker.invalid",
                 "X-CSRF-Token": second.json()["csrfToken"]},
                {ASSERTION: new_proof, "Origin": ORIGIN, "X-CSRF-Token": first.json()["csrfToken"]}):
        assert client.post("/api/v1/admin/logout", headers=bad).status_code == 403
    good = {ASSERTION: new_proof, "Origin": ORIGIN, "X-CSRF-Token": second.json()["csrfToken"]}
    response = client.post("/api/v1/admin/logout", headers=good)
    assert response.json() == {"ok": True} and "Secure" in response.headers["set-cookie"]
    assert client.get("/api/v1/admin/overview", headers={ASSERTION: new_proof}).status_code == 401
    assert client.app.state.db.conn.execute("SELECT COUNT(*) FROM admin_access_sessions").fetchone()[0] == 1
    # The remaining row belongs to an older independent local proof, not to the logged-out token.
    assert client.app.state.db.conn.execute("SELECT assertionDigest FROM admin_access_sessions").fetchone()[0] == hashlib.sha256(old_proof.encode()).hexdigest()


@pytest.mark.parametrize("path", ["/admin/", JS, CSS])
def test_public_admin_shell_and_exact_assets_require_verified_assertion(client, path):
    assert client.get(path).status_code == 401
    response = client.get(path, headers=headers(client))
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert client.app.state.db.conn.execute("SELECT COUNT(*) FROM admin_sessions").fetchone()[0] == 0


def test_admin_redirect_keeps_relative_https_origin_and_old_exchange_closed(client):
    response = client.get("/admin", headers=headers(client), follow_redirects=False)
    assert response.status_code == 307 and response.headers["location"] == "/admin/"
    response = client.post("/api/v1/admin/session", headers={**headers(client), "Origin": ORIGIN},
                           json={"code": "old-code" * 8})
    assert response.status_code == 403 and response.json()["error"]["code"] == "admin_link_disabled"


@pytest.mark.parametrize("path,status", [("/admin", 307), ("/admin/", 200)])
def test_cross_site_top_level_document_return_still_requires_valid_jwt(client, path, status):
    navigation = {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate",
                  "Sec-Fetch-Dest": "document"}
    assert client.get(path, headers=navigation, follow_redirects=False).status_code == 401
    assert client.get(path, headers={**navigation, ASSERTION: "invalid"},
                      follow_redirects=False).status_code == 401
    assert client.get(path, headers={**navigation, **headers(client)},
                      follow_redirects=False).status_code == status
    for origin in ("null", "https://attacker.invalid"):
        assert client.get(path, headers={**navigation, **headers(client), "Origin": origin},
                          follow_redirects=False).status_code == 403
    assert client.app.state.db.conn.execute("SELECT COUNT(*) FROM admin_sessions").fetchone()[0] == 0


@pytest.mark.parametrize("method,path,mode,destination", [
    ("GET", "/api/v1/admin/session", "navigate", "document"),
    ("GET", "/api/v1/admin/overview", "navigate", "document"),
    ("GET", JS, "navigate", "document"), ("GET", CSS, "navigate", "document"),
    ("GET", "/admin/", "navigate", "iframe"), ("GET", "/admin/", "cors", "empty"),
    ("GET", "/admin/", "", "document"), ("GET", "/admin/", "navigate", ""),
    ("POST", "/admin/", "navigate", "document"),
    ("POST", "/api/v1/admin/logout", "navigate", "document"),
])
def test_cross_site_navigation_exception_never_extends_to_api_assets_frames_or_writes(
    client, method, path, mode, destination,
):
    response = client.request(method, path, headers={**headers(client),
        "Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": mode, "Sec-Fetch-Dest": destination},
        follow_redirects=False)
    assert response.status_code == 403
    assert client.app.state.db.conn.execute("SELECT COUNT(*) FROM admin_sessions").fetchone()[0] == 0


@pytest.mark.parametrize("path", [
    "/", "/test", "/test/", "/test/sw.js", "/test/manifest.webmanifest", "/healthz", "/readyz",
    "/docs", "/redoc", "/openapi.json", "/api/v1/info", "/api/v1/models", "/api/v1/session",
    "/api/v1/conversations", "/api/v1/auth/options", "/api/v1/auth/oidc/session",
    "/api/v1/auth/oidc/callback", "/api/v1/dev/session", "/api/v1/energy/wallet",
    "/console-assets/public-chat-synthetic.js", "/console-assets/admin-old.js", "/console-assets/",
    "/admin/private", "/api/v1/adminfoo", "/api/v1/admin/%2e%2e/session", "/api/v1/admin/%5Coverview",
    "/api//v1/admin/overview", "/api/v1/admin/./../session",
])
def test_public_origin_never_publishes_business_or_other_assets_even_with_valid_identity(client, path):
    for extra in ({}, headers(client)):
        response = client.get(path, headers=extra, follow_redirects=False)
        assert response.status_code == 404
    assert client.app.state.db.conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0
    assert client.app.state.db.conn.execute("SELECT COUNT(*) FROM energy_wallets").fetchone()[0] == 0


@pytest.mark.parametrize("host", ["attacker.invalid", "dsah.guyufl.cc.cd:443", "dsah.guyufl.cc.cd.",
                                   "DSAH.GUYUFL.CC.CD", "dsah.guyufl.cc.cd@127.0.0.1"])
def test_unknown_hosts_and_forwarding_headers_cannot_open_business_or_admin(client, host):
    extra = {**headers(client), "Host": host, "X-Forwarded-Host": "dsah.guyufl.cc.cd",
             "X-Forwarded-For": "127.0.0.1", "X-Forwarded-Proto": "https"}
    for path in ("/api/v1/info", "/admin/", "/api/v1/admin/session"):
        assert client.get(path, headers=extra).status_code == 403


def test_duplicate_hosts_and_duplicate_assertions_fail_closed(client):
    proof = client.synthetic_issuer.assertion()
    assert client.get("/api/v1/admin/session", headers=[("Host", "dsah.guyufl.cc.cd"),
        ("Host", "127.0.0.1"), (ASSERTION, proof)]).status_code == 403
    assert client.get("/api/v1/admin/session", headers=[(ASSERTION, proof), (ASSERTION, proof)]).status_code == 401


def test_nonloopback_peer_and_cross_site_never_gain_admin(tmp_path, key):
    configured = settings_for(tmp_path)
    build_static(configured.console_dist_path)
    with TestClient(create_app(configured), base_url=ORIGIN, client=("198.51.100.1", 4),
                    raise_server_exceptions=False) as client:
        assert client.get("/admin/", headers={ASSERTION: SyntheticAccess(key).assertion()}).status_code == 403
    with TestClient(create_app(configured), base_url=ORIGIN, client=("127.0.0.1", 4),
                    raise_server_exceptions=False) as client:
        assert client.get("/api/v1/admin/session", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
        assert client.get("/admin/", headers={"Origin": "https://attacker.invalid"}).status_code == 403


def test_loopback_ordinary_api_stays_private_but_admin_still_requires_jwt(client):
    for endpoint in ("/healthz", "/readyz", "/api/v1/info", "/api/v1/auth/options"):
        assert client.get(endpoint, headers={"Host": "127.0.0.1:8000"}).status_code == 200
    assert client.get("/api/v1/admin/overview", headers={"Host": "127.0.0.1:8000"}).status_code == 401
    assert client.get("/admin/", headers={"Host": "127.0.0.1:8000"}).status_code == 200


def test_restart_retires_all_old_admin_proofs_without_changing_business(tmp_path):
    configured = settings_for(tmp_path)
    build_static(configured.console_dist_path)
    db = Database(configured.database_path, configured)
    db.create_session("business-token", "business-user", 3600)
    conversation = db.create_conversation("business-user", ConversationInput(title="keep private data"))
    with db.atomic():
        db.conn.execute("INSERT INTO admin_sessions VALUES (?, ?)", (token_hash("old-admin"), time.time() + 600))
        db.conn.execute("INSERT INTO admin_login_codes VALUES (?, ?)", (token_hash("old-code"), time.time() + 600))
        db.conn.execute("INSERT INTO admin_access_sessions VALUES (?, ?, ?, ?, ?)",
                        (token_hash("old-admin"), "s", "e", "d", time.time() + 600))
    db.close()
    with TestClient(create_app(configured), base_url=ORIGIN, client=("127.0.0.1", 1)) as client:
        db = client.app.state.db
        for table in ("admin_sessions", "admin_access_sessions", "admin_login_codes"):
            assert db.conn.execute("SELECT COUNT(*) FROM " + table).fetchone()[0] == 0
        assert db.conn.execute("SELECT title FROM conversations WHERE id=?", (conversation.id,)).fetchone()[0] == "keep private data"
        assert db.conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1
        assert db.conn.execute("PRAGMA user_version").fetchone()[0] == 8
        assert db.conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_backup_restore_clears_access_binding_and_parent_without_copying_live_state(client, tmp_path):
    login(client)
    db = client.app.state.db
    before = [tuple(row) for row in db.conn.execute("SELECT * FROM admin_access_sessions")]
    output = tmp_path / "snapshots"
    result = create_backup(Path(db.settings.database_path), output)
    target = tmp_path / "offline-restored.sqlite3"
    restore_backup(output / result["backupName"], target)
    with sqlite3.connect(target) as restored:
        for table in REVOKE_TABLES:
            if table != "browser_sessions":
                assert restored.execute("SELECT COUNT(*) FROM " + table).fetchone()[0] == 0
        assert restored.execute("PRAGMA foreign_key_check").fetchall() == []
    assert [tuple(row) for row in db.conn.execute("SELECT * FROM admin_access_sessions")] == before


def test_schema7_to8_is_additive_and_old_admin_is_not_a_fallback(tmp_path):
    path = str(tmp_path / "schema7.sqlite3")
    old = Settings(_env_file=None, admin_auth_backend="link", allow_dev_auth=True, auth_backend="legacy")
    db = Database(path, old)
    db.create_session("legacy-business", "legacy-user", 3600)
    chat = db.create_conversation("legacy-user", ConversationInput(title="original"))
    AdminRepository(db).issue_login("c" * 43, 300)
    with db.atomic():
        db.conn.execute("DROP TABLE admin_access_sessions")
        db.conn.execute("PRAGMA user_version=7")
    db.close()
    current = Settings(_env_file=None, admin_auth_backend="access", allow_dev_auth=True, auth_backend="legacy")
    db = Database(path, current)
    try:
        assert db.conn.execute("PRAGMA user_version").fetchone()[0] == 8
        assert db.authenticate("legacy-business") == "legacy-user"
        assert db.conversation("legacy-user", chat.id).title == "original"
        with pytest.raises(APIError) as failure:
            AdminRepository(db).exchange_login("c" * 43, "cannot-issue", 300)
        assert failure.value.code == "admin_link_disabled"
        assert db.conn.execute("SELECT COUNT(*) FROM admin_sessions").fetchone()[0] == 0
        assert db.conn.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        db.close()


@pytest.mark.parametrize("changes", [
    {"access_team_domain": "http://synthetic-team.cloudflareaccess.com"},
    {"access_team_domain": "https://synthetic-team.cloudflareaccess.com/"},
    {"access_team_domain": "https://attacker.invalid"},
    {"access_audience": "wrong"}, {"access_audience": "B" * 64},
    {"access_allowed_emails": []}, {"access_allowed_emails": [" user@example.invalid"]},
    {"access_allowed_emails": ["user@example.invalid\n"]},
    {"admin_public_origin": "http://dsah.guyufl.cc.cd"},
    {"admin_public_origin": "https://dsah.guyufl.cc.cd:443"},
    {"admin_public_origin": "https://dsah.guyufl.cc.cd/"},
    {"admin_public_origin": "https://127.0.0.1"},
    {"admin_public_origin": "https://dsah.guyufl.cc.cd@attacker.invalid"},
    {"admin_public_origin": "https://dsah.guyufl.cc.cd?x=1"},
    {"admin_auth_backend": "link"},
])
def test_operator_configuration_must_be_complete_fixed_and_unambiguous(tmp_path, changes):
    with pytest.raises(ValidationError):
        settings_for(tmp_path, **changes)


def test_cli_does_not_issue_or_print_old_link_in_access_mode(tmp_path, monkeypatch, capsys):
    path = tmp_path / "cli.sqlite3"
    Database(str(path)).close()
    monkeypatch.setenv("ALLIRA_ADMIN_AUTH_BACKEND", "access")
    monkeypatch.setenv("ALLIRA_ADMIN_ENABLED", "true")
    monkeypatch.setenv("ALLIRA_DATABASE_PATH", str(path))
    assert admin_main(["--env-file", str(tmp_path / "absent.env"), "issue-login", "--json"]) == 1
    captured = capsys.readouterr()
    assert not captured.out and "Cloudflare Access" in captured.err
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM admin_login_codes").fetchone()[0] == 0


def test_static_allowlist_is_derived_from_exact_two_current_assets(tmp_path):
    configured = settings_for(tmp_path)
    build_static(configured.console_dist_path)
    root = Path(configured.console_dist_path)
    assert admin_asset_paths(configured) == frozenset({JS, CSS})
    (root / "admin" / "index.html").write_text('<script src="/console-assets/public-chat-synthetic.js"></script>')
    assert admin_asset_paths(configured) == frozenset()
    (root / "admin" / "index.html").write_text('<script src="/console-assets/admin-synthetic123.js"></script>' * 1000)
    assert admin_asset_paths(configured) == frozenset()
