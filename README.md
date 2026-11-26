# Pipeline Leak Alert System

Monitoring and control for a pipeline split into sections. Field devices (IoT sensor nodes) send
pressure, flow and temperature readings over HTTP; the backend validates them, runs leak
detection, raises and clears alerts, and lets operators command valves and a pump. A live
3D operations view shows everything in real time.

```
Sensor node --HTTP/JSON + API key--> /api/v1/ingest --> validate --> detect --> alerts
                                                                          |
Operator (browser, login) <-- live 3D view / dashboard --- commands --> valves, pump
```

## Features
- **IoT ingestion:** per-device API keys (stored hashed), per-device last-seen, enable/disable.
- **Detection:** transparent rules with configurable thresholds (`app/config.py`, env overrides).
- **Alerts:** one active alert per section and severity, auto-cleared when the section is normal, acknowledge by operators.
- **OT control:** isolation valves and a pump via a command queue (devices poll and acknowledge), optional **auto-isolation** of a critical section.
- **Temperature rules:** below 80 C normal, 80-99 C warning, 100 C or above danger (raises the section to CRITICAL). Overheating can be simulated.
- **Tank farm:** 10 tank levels with the average (dashboard, operations view, `GET /api/tanks`, device endpoint `POST /api/v1/tanks`).
- **Reports:** CSV export of readings and alerts.
- **Alert webhook:** set `ALERT_WEBHOOK_URL` (and optionally `ALERT_WEBHOOK_SECRET`) and every new alert is POSTed as JSON, signed with `X-Signature: sha256=<hmac>`. Use it to forward alerts to a chat bot or SMS/WhatsApp gateway.
- **Access control:** login, roles (viewer / operator / admin), CSRF protection, login throttling, audit log.
- **3D operations view** (`/operations`): layer toggles (ground, tanks, pipeline, flow, valves/pump, sensors, leaks, labels), live data, every control wired to the API.
- **Simulation mode** (default): built-in virtual devices so you can try everything without hardware. Set `SIMULATION=0` for real devices only.

## Install (Termux or Linux)
```bash
pip install -r requirements.txt        # add requirements-dev.txt to run the tests
python -m pytest                       # optional
```

## Run
```bash
ADMIN_PASSWORD='choose-a-strong-password' python -m uvicorn app.main:app --host 127.0.0.1 --port 8010
```
On first start an `admin` user is created. If you do not set `ADMIN_PASSWORD`, a random password is printed once in the console/log. Open `http://localhost:8010`.

Add users: `python -m app.cli create-user NAME operator` (roles: viewer, operator, admin). Reset: `python -m app.cli reset-password NAME`.

## Connect real devices
1. Log in as admin, open **Devices**, register a device for a section, copy the API key (shown once).
2. The device sends readings (see `examples/device_client.py`):
```bash
curl -X POST http://HOST:8010/api/v1/ingest -H "X-Device-Key: pk_..." -H "Content-Type: application/json" \
  -d '{"pressure":80,"flow_in":1000,"flow_out":985,"temperature":35,"sensor_status":"OK"}'
```
3. Control: the device polls `GET /api/v1/device/commands`, applies each command to its hardware, then calls `POST /api/v1/device/commands/{id}/ack`.
4. Set `SIMULATION=0` so the virtual devices stop.

## Detection rules (`app/detection.py`)
`flow_difference = flow_in - flow_out` (L/min). First match wins:

| Status | Rule |
|---|---|
| NORMAL (idle) | both flows <= `idle_flow`: section idle or isolated, leak rules skipped |
| CRITICAL | (difference >= critical AND pressure < minimum) OR (pressure < severe AND difference >= leak) |
| LEAK SUSPECTED | difference >= leak OR pressure < minimum |
| WARNING | difference >= warning OR pressure within the margin above minimum OR flow out exceeds flow in by >= warning |
| NORMAL | none of the above |

A `FAULT`/`OFFLINE` sensor skips the rules and raises a WARNING. Defaults (warning 50, leak 150, critical 250 L/min; minimum 60 bar, severe 40 bar, margin 10 bar, idle 5 L/min) are **placeholders**. Override with env vars `WARNING_FLOW_DIFF`, `LEAK_FLOW_DIFF`, `CRITICAL_FLOW_DIFF`, `MIN_PRESSURE`, `SEVERE_PRESSURE`, `PRESSURE_WARNING_MARGIN`, `IDLE_FLOW`.

## Settings (environment variables)
`ALERT_WEBHOOK_URL`, `ALERT_WEBHOOK_SECRET`, `TEMP_WARNING`, `TEMP_DANGER`, `DATABASE_URL`, `SIMULATION` (1/0), `SIM_INTERVAL` (seconds), `SESSION_HOURS`, `DEVICE_ONLINE_SECONDS`, `ADMIN_USERNAME`, `ADMIN_PASSWORD`.

## API summary
Browser session (+ `X-CSRF-Token` header for writes): `GET /api/state`, `/api/pipeline-sections`, `/api/readings`, `/api/alerts`; `POST /api/alerts/{id}/acknowledge`, `/api/assets/{id}/command`, `/api/sim/{pause|resume|leak|repair}`; admin: `/api/devices`, `/api/settings/auto-isolation`, `/api/audit`. Devices (API key): `POST /api/v1/ingest`, `GET /api/v1/device/commands`, `POST /api/v1/device/commands/{id}/ack`. Open: `GET /api/health`. Interactive docs at `/docs`.

## Security notes
Passwords: PBKDF2-SHA256. Sessions: server-side, random tokens stored hashed, HttpOnly + SameSite=Strict cookie. Device keys: stored hashed. SQLAlchemy only (no raw SQL), auto-escaped templates, CSP and other headers. **Serve over HTTPS** (for example behind a Cloudflare tunnel or reverse proxy) before exposing it beyond a trusted network; the cookie is not marked `Secure` so that plain local HTTP works.

## Important limitations
- This software is not certified safety equipment. Do not use it as the only protection for a real pipeline; keep independent hardware safety systems (relief valves, trips, ESD).
- Detection is a simple threshold method on one reading at a time (no trends, no calibration, no sensor redundancy). Calibrate thresholds with a qualified engineer.
- Valve and pump commands are only requests in a queue; your device firmware must apply them safely (interlocks, limits, fail-safe state).
- Single process with SQLite; no clustering, no TLS termination, no email/SMS notifications, no MQTT, no user-management UI (use the CLI).

CYB 207 assignment notes (decomposition, IPO tables, requirements) are in `docs/ASSIGNMENT_NOTES.md`.

## Hardening added

The prototype now includes a small set of defensive improvements that keep the assignment architecture understandable:

- **Leak confirmation:** `LEAK_SUSPECTED` requires 3 consecutive qualifying readings within 30 seconds by default. `CRITICAL` conditions remain immediate.
- **Device credential rotation:** admins can rotate a device API key; the old key is invalidated immediately. The new key is shown only in the rotation response.
- **Device endpoint rate limiting:** field-device requests are limited to 60 requests/minute per device key by default. This is an in-process prototype control; a production multi-worker deployment should enforce the limit at a shared gateway.
- **Secure-cookie option:** set `COOKIE_SECURE=1` when the application is served over HTTPS. Session and CSRF cookies are then marked `Secure`.
- **Additional browser hardening:** `Permissions-Policy`, conditional HSTS, and `no-store` caching for authenticated/API responses.

These controls improve the prototype but do not make it production safety equipment. HTTPS, device-key lifecycle management, replay protection, centralized rate limiting, database hardening, hardware interlocks, and industrial safety validation would still be required for a real deployment.
