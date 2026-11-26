"""Server-rendered pages (login, dashboard, operations, devices)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import PlainTextResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app import auth
from app.config import (BASE_DIR, COOKIE_SECURE, DEMO_SCENARIOS, LIMITS, SENSOR_STATUSES,
                        SESSION_HOURS, SIMULATION, THRESHOLDS)
from app.control import audit
from app.database import get_db
from app.detection import ReadingValidationError, status_label
from app.models import SensorReading
from app.schemas import ReadingIn
from app.security import get_or_create_token, set_csrf_cookie, verify_csrf
from app.services import (SectionNotFoundError, acknowledge_alert, get_dashboard_data, get_tanks,
                          process_reading)

router = APIRouter()
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))  # autoescape is on
templates.env.filters["status_label"] = status_label
templates.env.filters["status_css"] = lambda v: "status-" + str(v).lower().replace("_", "-")
templates.env.globals["mode"] = "SIMULATION" if SIMULATION else "LIVE"

FIELD_LABELS = {
    "pipeline_section_id": "Pipeline section", "pressure": "Pressure", "flow_in": "Flow in",
    "flow_out": "Flow out", "temperature": "Temperature", "sensor_status": "Sensor status",
}
CSRF_FAIL = "Invalid or missing CSRF token. Reload the page and try again."


def page(request: Request, name: str, user, ctx: dict | None = None, status_code: int = 200):
    token, is_new = get_or_create_token(request)
    response = templates.TemplateResponse(
        request, name, {**(ctx or {}), "user": user, "csrf_token": token}, status_code=status_code)
    response.headers["Cache-Control"] = "no-store"
    if is_new:
        set_csrf_cookie(response, token)
    return response


def to_login() -> RedirectResponse:
    return RedirectResponse(url="/login", status_code=303)


# ---- login / logout
@router.get("/login")
def login_page(request: Request, db: Session = Depends(get_db)):
    if auth.get_user(request, db):
        return RedirectResponse(url="/", status_code=303)
    return page(request, "login.html", None)


@router.post("/login")
def login(request: Request, csrf_token: str = Form(""), username: str = Form(""),
          password: str = Form(""), db: Session = Depends(get_db)):
    if not verify_csrf(request, csrf_token):
        return PlainTextResponse(CSRF_FAIL, status_code=403)
    key = f"{request.client.host if request.client else '?'}|{username.lower()}"
    if auth.throttled(key):
        return page(request, "login.html", None,
                    {"error": "Too many attempts. Try again in a few minutes."}, 429)
    user = auth.authenticate(db, username, password)
    if user is None:
        auth.record_fail(key)
        audit(db, username or "-", "LOGIN_FAILED")
        db.commit()
        return page(request, "login.html", None, {"error": "Invalid username or password."}, 401)
    auth.clear_fails(key)
    token = auth.create_session(db, user)
    audit(db, user.username, "LOGIN")
    db.commit()
    response = RedirectResponse(url="/", status_code=303)
    response.set_cookie(auth.SESSION_COOKIE, token, httponly=True, secure=COOKIE_SECURE,
                        samesite="strict", max_age=int(SESSION_HOURS * 3600), path="/")
    return response


@router.post("/logout")
def logout(request: Request, csrf_token: str = Form(""), db: Session = Depends(get_db)):
    if not verify_csrf(request, csrf_token):
        return PlainTextResponse(CSRF_FAIL, status_code=403)
    auth.destroy_session(db, request.cookies.get(auth.SESSION_COOKIE))
    response = to_login()
    response.delete_cookie(auth.SESSION_COOKIE, path="/")
    return response


# ---- dashboard
def render_dashboard(request, db, user, *, status_code=200, errors=None, form=None, result_id=None):
    ctx = {
        **get_dashboard_data(db), "errors": errors or [], "form": form or {},
        "result_reading": db.get(SensorReading, result_id) if result_id else None,
        "scenarios": DEMO_SCENARIOS, "thresholds": THRESHOLDS, "limits": LIMITS,
        "sensor_statuses": SENSOR_STATUSES, "tanks": get_tanks(db),
    }
    return page(request, "dashboard.html", user, ctx, status_code)


@router.get("/")
def dashboard(request: Request, result: int | None = None, db: Session = Depends(get_db)):
    user = auth.get_user(request, db)
    if user is None:
        return to_login()
    return render_dashboard(request, db, user, result_id=result)


@router.post("/readings")
def submit_reading(
    request: Request, csrf_token: str = Form(""), pipeline_section_id: str = Form(""),
    pressure: str = Form(""), flow_in: str = Form(""), flow_out: str = Form(""),
    temperature: str = Form(""), sensor_status: str = Form("OK"), db: Session = Depends(get_db),
):
    if not verify_csrf(request, csrf_token):
        return PlainTextResponse(CSRF_FAIL, status_code=403)
    user = auth.get_user(request, db)
    if user is None:
        return to_login()
    if auth.ROLE_RANK[user.role] < auth.ROLE_RANK["operator"]:
        return PlainTextResponse("Operator role required.", status_code=403)

    form = {"pipeline_section_id": pipeline_section_id, "pressure": pressure, "flow_in": flow_in,
            "flow_out": flow_out, "temperature": temperature, "sensor_status": sensor_status}
    errors: list[str] = []
    try:
        processed = process_reading(db, ReadingIn.model_validate(form))
    except ValidationError as exc:
        for err in exc.errors():
            field = str(err["loc"][-1]) if err["loc"] else "input"
            errors.append(f"{FIELD_LABELS.get(field, field)}: {err['msg']}")
    except ReadingValidationError as exc:
        errors.extend(exc.errors)
    except SectionNotFoundError:
        errors.append("The selected pipeline section does not exist.")
    if errors:
        return render_dashboard(request, db, user, status_code=400, errors=errors, form=form)
    audit(db, user.username, "MANUAL_READING", f"reading {processed.reading.id}")
    db.commit()
    return RedirectResponse(url=f"/?result={processed.reading.id}", status_code=303)


@router.post("/alerts/{alert_id}/acknowledge")
def acknowledge(request: Request, alert_id: int, csrf_token: str = Form(""),
                db: Session = Depends(get_db)):
    if not verify_csrf(request, csrf_token):
        return PlainTextResponse(CSRF_FAIL, status_code=403)
    user = auth.get_user(request, db)
    if user is None:
        return to_login()
    if auth.ROLE_RANK[user.role] < auth.ROLE_RANK["operator"]:
        return PlainTextResponse("Operator role required.", status_code=403)
    if acknowledge_alert(db, alert_id) is None:
        return PlainTextResponse("Alert not found.", status_code=404)
    audit(db, user.username, "ALERT_ACK", f"alert {alert_id}")
    db.commit()
    return RedirectResponse(url="/", status_code=303)


# ---- live operations view + device admin
@router.get("/operations")
def operations(request: Request, db: Session = Depends(get_db)):
    user = auth.get_user(request, db)
    if user is None:
        return to_login()
    return page(request, "operations.html", user)


@router.get("/simulation")
def simulation_redirect():
    return RedirectResponse(url="/operations", status_code=307)


@router.get("/devices")
def devices_page(request: Request, db: Session = Depends(get_db)):
    user = auth.get_user(request, db)
    if user is None:
        return to_login()
    if user.role != "admin":
        return PlainTextResponse("Admin role required.", status_code=403)
    return page(request, "devices.html", user)
