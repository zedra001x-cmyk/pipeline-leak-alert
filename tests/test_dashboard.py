"""HTML pages: login-protected dashboard, manual reading form, CSRF, escaping."""
from app.models import Alert, SensorReading

FORM = {"pipeline_section_id": "1", "pressure": "55", "flow_in": "1000", "flow_out": "700",
        "temperature": "35", "sensor_status": "OK"}


def token(client):
    client.get("/")
    return client.cookies.get("csrf_token")


def test_dashboard_loads_for_logged_in_user(admin):
    response = admin.get("/")
    assert response.status_code == 200
    assert "Section A" in response.text and "Active alerts" in response.text
    assert "CYB" not in response.text and "educational" not in response.text.lower()


def test_operations_and_devices_pages(admin):
    ops = admin.get("/operations")
    assert ops.status_code == 200 and 'id="sim-canvas"' in ops.text and "/static/ops3d.js" in ops.text
    assert 'name="csrf-token"' in ops.text
    assert admin.get("/devices").status_code == 200
    assert admin.get("/simulation", follow_redirects=False).headers["location"] == "/operations"


def test_static_scripts_served(client):
    assert client.get("/static/ops3d.js").status_code == 200
    assert client.get("/static/devices.js").status_code == 200


def test_post_without_csrf_rejected(admin, db_session):
    response = admin.post("/readings", data=FORM, follow_redirects=False)
    assert response.status_code == 403
    assert db_session.query(SensorReading).count() == 0


def test_valid_manual_reading_saves_and_alerts(admin, db_session):
    response = admin.post("/readings", data={**FORM, "csrf_token": token(admin)}, follow_redirects=False)
    assert response.status_code == 303
    assert db_session.query(SensorReading).count() == 1
    assert db_session.query(Alert).one().severity == "CRITICAL"
    assert "classified as" in admin.get(response.headers["location"]).text


def test_negative_value_shows_error(admin, db_session):
    response = admin.post("/readings", data={**FORM, "pressure": "-4", "csrf_token": token(admin)})
    assert response.status_code == 400 and "was not saved" in response.text
    assert db_session.query(SensorReading).count() == 0


def test_user_input_is_html_escaped(admin):
    payload = "<script>alert(1)</script>"
    response = admin.post("/readings", data={**FORM, "pressure": payload, "csrf_token": token(admin)})
    assert response.status_code == 400
    assert payload not in response.text and "&lt;script&gt;" in response.text


def test_acknowledge_from_dashboard(admin, db_session):
    t = token(admin)
    admin.post("/readings", data={**FORM, "csrf_token": t}, follow_redirects=False)
    alert = db_session.query(Alert).one()
    assert admin.post(f"/alerts/{alert.id}/acknowledge", data={}, follow_redirects=False).status_code == 403
    ok = admin.post(f"/alerts/{alert.id}/acknowledge", data={"csrf_token": t}, follow_redirects=False)
    assert ok.status_code == 303
    db_session.expire_all()
    assert db_session.get(Alert, alert.id).status == "ACKNOWLEDGED"


def test_security_headers_present(client):
    response = client.get("/login")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "default-src 'self'" in response.headers["content-security-policy"]


def test_dashboard_shows_tank_section_and_exports(admin):
    text = admin.get("/").text
    assert "Tank levels" in text and "/api/export/readings.csv" in text
