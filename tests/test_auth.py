"""Login, roles, CSRF on the JSON API, device keys."""
from tests.conftest import add_user, login

from app.auth import hash_password, verify_password


def test_password_hashing_roundtrip():
    stored = hash_password("correct horse battery", iterations=1000)
    assert verify_password("correct horse battery", stored)
    assert not verify_password("wrong", stored)
    assert not verify_password("x", "garbage")


def test_api_requires_login(client):
    assert client.get("/api/state").status_code == 401
    assert client.get("/api/alerts").status_code == 401
    assert client.get("/api/health").status_code == 200


def test_pages_redirect_to_login(client):
    for path in ("/", "/operations", "/devices"):
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 303 and response.headers["location"] == "/login"


def test_login_success_and_wrong_password(client):
    assert login(client, password="nope").status_code == 401
    assert client.get("/api/state").status_code == 401
    assert login(client).status_code == 303
    assert client.get("/api/state").status_code == 200


def test_login_requires_csrf(client):
    client.get("/login")
    response = client.post("/login", data={"username": "admin", "password": "test-admin-password"})
    assert response.status_code == 403


def test_login_is_throttled_after_repeated_failures(client):
    for _ in range(5):
        login(client, password="bad")
    assert login(client, password="bad").status_code == 429
    assert login(client).status_code == 429  # even the right password is blocked for a while


def test_logout_ends_session(admin):
    admin.post("/logout", data={"csrf_token": admin.cookies.get("csrf_token")}, follow_redirects=False)
    assert admin.get("/api/state").status_code == 401


def test_json_post_needs_csrf_header(admin):
    del admin.headers["X-CSRF-Token"]
    assert admin.post("/api/sim/repair").status_code == 403


def test_viewer_cannot_control_but_can_read(client):
    pw = add_user("vera", "viewer")
    login(client, "vera", pw)
    assert client.get("/api/state").status_code == 200
    assert client.post("/api/sim/repair").status_code == 403
    assert client.post("/api/assets/2/command", json={"action": "CLOSE"}).status_code == 403
    assert client.get("/api/devices").status_code == 403


def test_operator_can_control_but_not_manage_devices(client):
    pw = add_user("omar", "operator")
    login(client, "omar", pw)
    assert client.post("/api/sim/repair").status_code == 200
    assert client.post("/api/devices", json={"name": "x", "pipeline_section_id": 1}).status_code == 403
    assert client.post("/api/settings/auto-isolation", json={"enabled": True}).status_code == 403
