"""API tests: readings, alerts, devices (IoT ingestion), equipment commands, audit."""
from datetime import timedelta

from app.models import Asset, Command, Device, SensorReading, utcnow
from app.simulator import sim_step

CRITICAL = {"pressure": 55, "flow_in": 1000, "flow_out": 700, "temperature": 35}
NORMAL = {"pressure": 80, "flow_in": 1000, "flow_out": 980, "temperature": 35}


def make_device(admin, name="node-1", section=1):
    response = admin.post("/api/devices", json={"name": name, "pipeline_section_id": section})
    assert response.status_code == 201
    return response.json()


def ingest(client, key, body):
    return client.post("/api/v1/ingest", json=body, headers={"X-Device-Key": key})


def test_health_and_seed(admin):
    assert admin.get("/api/health").json() == {"status": "ok"}
    names = [s["name"] for s in admin.get("/api/pipeline-sections").json()]
    assert names == ["Section A", "Section B", "Section C"]


def test_device_registration_returns_key_once(admin):
    device = make_device(admin)
    assert device["api_key"].startswith("pk_")
    listed = admin.get("/api/devices").json()
    assert listed[0]["name"] == "node-1" and "api_key" not in listed[0]
    assert admin.post("/api/devices", json={"name": "node-1", "pipeline_section_id": 1}).status_code == 409
    assert admin.post("/api/devices", json={"name": "n2", "pipeline_section_id": 99}).status_code == 404


def test_ingest_requires_valid_key(admin):
    assert admin.post("/api/v1/ingest", json=NORMAL).status_code == 401
    assert ingest(admin, "pk_wrong", NORMAL).status_code == 401


def test_ingest_normal_then_critical_creates_alert(admin):
    key = make_device(admin)["api_key"]
    ok = ingest(admin, key, NORMAL)
    assert ok.status_code == 201 and ok.json()["detected_status"] == "NORMAL"
    assert ok.json()["alert"] is None

    crit = ingest(admin, key, CRITICAL).json()
    assert crit["detected_status"] == "CRITICAL" and crit["alert"]["status"] == "ACTIVE"
    assert admin.get("/api/pipeline-sections").json()[0]["status"] == "CRITICAL"
    devices = admin.get("/api/devices").json()
    assert devices[0]["last_seen"] is not None


def test_repeated_same_severity_does_not_duplicate_alerts_and_clears_on_normal(admin):
    key = make_device(admin)["api_key"]
    ingest(admin, key, CRITICAL)
    ingest(admin, key, CRITICAL)
    assert len(admin.get("/api/alerts?status=ACTIVE").json()) == 1
    ingest(admin, key, NORMAL)
    assert admin.get("/api/alerts?status=ACTIVE").json() == []
    assert len(admin.get("/api/alerts?status=CLEARED").json()) == 1


def test_invalid_ingest_is_rejected(admin):
    key = make_device(admin)["api_key"]
    assert ingest(admin, key, {**NORMAL, "pressure": -1}).status_code == 422
    assert ingest(admin, key, {"pressure": 80}).status_code == 422
    assert ingest(admin, key, {**NORMAL, "pressure": "high"}).status_code == 422
    assert ingest(admin, key, {**NORMAL, "section": 2}).status_code == 422
    assert admin.get("/api/readings").json() == []


def test_disabled_device_is_rejected(admin):
    device = make_device(admin)
    admin.post(f"/api/devices/{device['id']}/enabled", json={"enabled": False})
    assert ingest(admin, device["api_key"], NORMAL).status_code == 401


def test_acknowledge_alert(admin):
    key = make_device(admin)["api_key"]
    alert_id = ingest(admin, key, CRITICAL).json()["alert"]["id"]
    response = admin.post(f"/api/alerts/{alert_id}/acknowledge")
    assert response.status_code == 200 and response.json()["status"] == "ACKNOWLEDGED"
    assert admin.post("/api/alerts/999/acknowledge").status_code == 404


def test_valve_command_flow_with_simulator(admin, db_session):
    valve = db_session.query(Asset).filter_by(name="Valve A-B").one()
    response = admin.post(f"/api/assets/{valve.id}/command", json={"action": "CLOSE"})
    assert response.status_code == 202
    state = admin.get("/api/state").json()
    assert [a for a in state["assets"] if a["id"] == valve.id][0]["pending"] is True

    sim_step(admin.app.state.sim)   # the virtual device applies the command
    state = admin.get("/api/state").json()
    closed = [a for a in state["assets"] if a["id"] == valve.id][0]
    assert closed["state"] == "CLOSED" and closed["pending"] is False
    # Section B is now fed by a closed valve: no flow, so it reads idle (NORMAL), not a leak
    assert state["sections"][1]["flow_in"] == 0
    assert state["sections"][1]["status"] == "NORMAL"


def test_invalid_command_is_rejected(admin, db_session):
    valve = db_session.query(Asset).filter_by(name="Valve A-B").one()
    assert admin.post(f"/api/assets/{valve.id}/command", json={"action": "START"}).status_code == 422
    assert admin.post("/api/assets/999/command", json={"action": "CLOSE"}).status_code == 422


def test_device_polls_and_acks_commands(admin, db_session):
    key = make_device(admin, section=2)["api_key"]          # Section B owns "Valve A-B"
    valve = db_session.query(Asset).filter_by(name="Valve A-B").one()
    admin.post(f"/api/assets/{valve.id}/command", json={"action": "CLOSE"})
    pending = admin.get("/api/v1/device/commands", headers={"X-Device-Key": key}).json()
    assert pending and pending[0]["action"] == "CLOSE"
    ack = admin.post(f"/api/v1/device/commands/{pending[0]['id']}/ack", headers={"X-Device-Key": key})
    assert ack.status_code == 200 and ack.json()["status"] == "SENT"
    db_session.expire_all()
    command = db_session.get(Command, pending[0]["id"])
    assert command.status == "SENT"
    assert db_session.get(Asset, valve.id).state == "OPEN"  # receipt is not execution or verification
    assert admin.post(f"/api/v1/device/commands/{command.id}/verify",
                      json={"observed_state": "CLOSED"}, headers={"X-Device-Key": key}).status_code == 409
    executed = admin.post(f"/api/v1/device/commands/{command.id}/execute", headers={"X-Device-Key": key})
    assert executed.status_code == 200 and executed.json()["status"] == "EXECUTED"
    mismatch = admin.post(f"/api/v1/device/commands/{command.id}/verify",
                          json={"observed_state": "OPEN"}, headers={"X-Device-Key": key})
    assert mismatch.status_code == 409
    assert admin.post(f"/api/v1/device/commands/{command.id}/execute",
                      headers={"X-Device-Key": key}).status_code == 409
    verified = admin.post(f"/api/v1/device/commands/{command.id}/verify",
                          json={"observed_state": "CLOSED"}, headers={"X-Device-Key": key})
    assert verified.status_code == 200 and verified.json()["status"] == "VERIFIED"
    assert admin.post(f"/api/v1/device/commands/{command.id}/verify",
                      json={"observed_state": "CLOSED"}, headers={"X-Device-Key": key}).status_code == 409
    db_session.expire_all()
    assert db_session.get(Asset, valve.id).state == "CLOSED"


def test_simulator_completes_command_lifecycle(admin, db_session):
    valve = db_session.query(Asset).filter_by(name="Valve A-B").one()
    response = admin.post(f"/api/assets/{valve.id}/command", json={"action": "CLOSE"})
    sim_step(admin.app.state.sim)
    command = db_session.get(Command, response.json()["id"])
    assert command.status == "VERIFIED"


def test_ingestion_rejects_unavailable_deduplication_metadata(admin):
    key = make_device(admin)["api_key"]
    response = ingest(admin, key, {**NORMAL, "sequence": 42})
    assert response.status_code == 422


def test_state_exposes_section_and_device_freshness(admin, db_session):
    key = make_device(admin, section=1)["api_key"]
    ingest(admin, key, NORMAL)
    first = db_session.query(SensorReading).one()
    first.created_at = utcnow() - timedelta(seconds=61)
    db_session.add(SensorReading(pipeline_section_id=2, pressure=80, flow_in=1000,
                                 flow_out=980, temperature=35, sensor_status="OK",
                                 detected_status="NORMAL",
                                 created_at=utcnow() - timedelta(seconds=121)))
    device = db_session.query(Device).one()
    device.last_seen = utcnow() - timedelta(seconds=61)
    db_session.commit()

    state = admin.get("/api/state").json()
    assert [s["telemetry_health"] for s in state["sections"]] == ["STALE", "OFFLINE", "MISSING"]
    assert state["devices"]["items"][0]["health"] == "STALE"


def test_auto_isolation_closes_valves_around_critical_section(admin, db_session):
    assert admin.post("/api/settings/auto-isolation", json={"enabled": True}).status_code == 200
    key = make_device(admin, section=2)["api_key"]           # Section B
    ingest(admin, key, CRITICAL)
    sim_step(admin.app.state.sim)
    db_session.expire_all()
    states = {a.name: a.state for a in db_session.query(Asset).filter_by(kind="VALVE")}
    assert states["Valve A-B"] == "CLOSED" and states["Valve B-C"] == "CLOSED"
    assert states["Inlet valve"] == "OPEN"


def test_simulator_controls(admin):
    assert admin.post("/api/sim/leak", json={"section": 1}).status_code == 200
    assert admin.post("/api/sim/leak", json={"section": 7}).status_code == 422
    assert admin.post("/api/sim/pause").json() == {"running": False}
    assert admin.get("/api/state").json()["sim"]["running"] is False
    assert admin.post("/api/sim/resume").json() == {"running": True}
    assert admin.post("/api/sim/repair").status_code == 200


def test_repair_all_stops_the_leak_immediately(admin):
    sim = admin.app.state.sim
    sim.start_leak(1)
    for _ in range(10):
        sim_step(sim)
    assert admin.get("/api/state").json()["sections"][1]["status"] in ("LEAK_SUSPECTED", "CRITICAL")
    admin.post("/api/sim/repair")
    sim_step(sim)
    assert admin.get("/api/state").json()["sections"][1]["status"] == "NORMAL"


def test_audit_log_records_actions(admin):
    admin.post("/api/sim/repair")
    actions = [row["action"] for row in admin.get("/api/audit").json()]
    assert "SIM" in actions and "LOGIN" in actions


def test_unknown_field_and_form_encoding_rejected_on_ingest(admin):
    key = make_device(admin)["api_key"]
    response = admin.post("/api/v1/ingest", data={k: str(v) for k, v in NORMAL.items()},
                          headers={"X-Device-Key": key})
    assert response.status_code == 422


def test_tank_levels_endpoint_and_average(admin):
    assert admin.get("/api/tanks").json() == {"levels": [], "average": None}
    key = make_device(admin)["api_key"]
    levels = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
    response = admin.post("/api/v1/tanks", json={"levels": levels}, headers={"X-Device-Key": key})
    assert response.status_code == 200 and response.json() == {"average": 55.0}
    data = admin.get("/api/tanks").json()
    assert data["average"] == 55.0 and len(data["levels"]) == 10


def test_tank_levels_validation(admin):
    key = make_device(admin)["api_key"]
    h = {"X-Device-Key": key}
    assert admin.post("/api/v1/tanks", json={"levels": [1] * 9}, headers=h).status_code == 422
    assert admin.post("/api/v1/tanks", json={"levels": [101] + [1] * 9}, headers=h).status_code == 422
    assert admin.post("/api/v1/tanks", json={"levels": [1] * 10}).status_code == 401


def test_temperature_warning_then_danger_alerts(admin):
    key = make_device(admin)["api_key"]
    warm = ingest(admin, key, {**NORMAL, "temperature": 85}).json()
    assert warm["detected_status"] == "WARNING"
    hot = ingest(admin, key, {**NORMAL, "temperature": 100}).json()
    assert hot["detected_status"] == "CRITICAL" and "Temperature" in hot["alert"]["message"]


def test_overheat_control_and_validation(admin):
    assert admin.post("/api/sim/overheat", json={"section": 0}).status_code == 200
    assert admin.post("/api/sim/overheat", json={"section": 9}).status_code == 422


def test_csv_exports(admin):
    key = make_device(admin)["api_key"]
    ingest(admin, key, CRITICAL)
    readings = admin.get("/api/export/readings.csv")
    assert readings.status_code == 200 and readings.headers["content-type"].startswith("text/csv")
    assert readings.text.splitlines()[0].startswith("id,section") and "Section A" in readings.text
    alerts = admin.get("/api/export/alerts.csv")
    assert "CRITICAL" in alerts.text


def test_export_requires_login(client):
    assert client.get("/api/export/readings.csv").status_code == 401


def test_device_key_rotation_invalidates_old_key(admin):
    device = make_device(admin)
    old_key = device["api_key"]
    rotated = admin.post(f"/api/devices/{device['id']}/rotate-key")
    assert rotated.status_code == 200
    new_key = rotated.json()["api_key"]
    assert new_key != old_key
    assert ingest(admin, old_key, NORMAL).status_code == 401
    assert ingest(admin, new_key, NORMAL).status_code == 201
