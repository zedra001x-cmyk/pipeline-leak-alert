"""JSON API. Browser calls need a login session (+ X-CSRF-Token header for writes);
field devices use an API key (X-Device-Key)."""
from __future__ import annotations

import csv
import io
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.auth import (device_auth, generate_device_key, hash_device_key, require_role)
from app.config import SIMULATION
from app.control import (CommandError, audit, enqueue_command, set_setting,
                         transition_command)
from app.database import get_db
from app.detection import ReadingValidationError
from app.models import (Alert, AuditLog, Asset, Command, Device, PipelineSection,
                        SensorReading, User, utcnow)
from app.schemas import (AlertOut, AuditOut, CommandIn, CommandVerifyIn, DeviceCreate, DeviceCreated, DeviceOut,
                         DeviceReadingIn, EnabledIn, LeakIn, TankLevelsIn, ReadingIn, ReadingOut, ReadingResult,
                         SectionOut)
from app.control import set_setting as _set_setting
from app.services import (SectionNotFoundError, acknowledge_alert, build_state, get_tanks,
                          process_reading)
from app.tanks import TankError, average_level

router = APIRouter(prefix="/api", tags=["api"])
viewer = require_role("viewer")
operator = require_role("operator")
admin = require_role("admin")


def mode() -> str:
    return "SIMULATION" if SIMULATION else "LIVE"


@router.get("/health")
def health() -> dict:
    return {"status": "ok"}


@router.get("/state")
def state(request: Request, user: User = Depends(viewer), db: Session = Depends(get_db)):
    return build_state(db, user, request.app.state.sim, mode())


@router.get("/pipeline-sections", response_model=list[SectionOut])
def list_sections(_: User = Depends(viewer), db: Session = Depends(get_db)):
    return db.scalars(select(PipelineSection).order_by(PipelineSection.id)).all()


@router.get("/readings", response_model=list[ReadingOut])
def list_readings(limit: int = Query(50, ge=1, le=500), _: User = Depends(viewer),
                  db: Session = Depends(get_db)):
    return db.scalars(select(SensorReading).order_by(SensorReading.id.desc()).limit(limit)).all()


@router.get("/alerts", response_model=list[AlertOut])
def list_alerts(status: str | None = Query(None, pattern="^(ACTIVE|ACKNOWLEDGED|CLEARED)$"),
                limit: int = Query(50, ge=1, le=500), _: User = Depends(viewer),
                db: Session = Depends(get_db)):
    query = select(Alert).order_by(Alert.id.desc()).limit(limit)
    if status:
        query = query.where(Alert.status == status)
    return db.scalars(query).all()


@router.post("/alerts/{alert_id}/acknowledge", response_model=AlertOut)
def ack_alert(alert_id: int, user: User = Depends(operator), db: Session = Depends(get_db)):
    alert = acknowledge_alert(db, alert_id)
    if alert is None:
        raise HTTPException(status_code=404, detail="Alert not found")
    audit(db, user.username, "ALERT_ACK", f"alert {alert_id}")
    db.commit()
    return alert


# ---- equipment control
@router.post("/assets/{asset_id}/command", status_code=202)
def command_asset(asset_id: int, body: CommandIn, user: User = Depends(operator),
                  db: Session = Depends(get_db)):
    try:
        command = enqueue_command(db, asset_id, body.action, user.username)
    except CommandError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"id": command.id, "status": command.status}


@router.post("/settings/auto-isolation")
def auto_isolation(body: EnabledIn, user: User = Depends(admin), db: Session = Depends(get_db)):
    set_setting(db, "auto_isolation", "1" if body.enabled else "0")
    audit(db, user.username, "SETTING", f"auto_isolation={body.enabled}")
    db.commit()
    return {"auto_isolation": body.enabled}


# ---- simulator controls (virtual devices)
def _sim(request: Request):
    if not SIMULATION:
        raise HTTPException(status_code=409, detail="Simulation is disabled (SIMULATION=0)")
    return request.app.state.sim


@router.post("/sim/pause")
def sim_pause(request: Request, user: User = Depends(operator), db: Session = Depends(get_db)):
    _sim(request).running = False
    audit(db, user.username, "SIM", "pause")
    db.commit()
    return {"running": False}


@router.post("/sim/resume")
def sim_resume(request: Request, user: User = Depends(operator), db: Session = Depends(get_db)):
    _sim(request).running = True
    audit(db, user.username, "SIM", "resume")
    db.commit()
    return {"running": True}


@router.post("/sim/leak")
def sim_leak(request: Request, body: LeakIn, user: User = Depends(operator),
             db: Session = Depends(get_db)):
    _sim(request).start_leak(body.section)
    audit(db, user.username, "SIM", f"leak in section index {body.section}")
    db.commit()
    return {"leak": body.section}


@router.post("/sim/overheat")
def sim_overheat(request: Request, body: LeakIn, user: User = Depends(operator),
                 db: Session = Depends(get_db)):
    _sim(request).start_overheat(body.section)
    audit(db, user.username, "SIM", f"overheat in section index {body.section}")
    db.commit()
    return {"overheat": body.section}


@router.post("/sim/repair")
def sim_repair(request: Request, user: User = Depends(operator), db: Session = Depends(get_db)):
    _sim(request).repair_all()
    audit(db, user.username, "SIM", "repair all")
    db.commit()
    return {"repaired": True}


# ---- device management (admin)
@router.get("/devices", response_model=list[DeviceOut])
def list_devices(_: User = Depends(admin), db: Session = Depends(get_db)):
    return db.scalars(select(Device).order_by(Device.id)).all()


@router.post("/devices", response_model=DeviceCreated, status_code=201)
def create_device(body: DeviceCreate, user: User = Depends(admin), db: Session = Depends(get_db)):
    if db.get(PipelineSection, body.pipeline_section_id) is None:
        raise HTTPException(status_code=404, detail="Pipeline section not found")
    if db.scalar(select(Device.id).where(Device.name == body.name)):
        raise HTTPException(status_code=409, detail="A device with this name already exists")
    key = generate_device_key()
    device = Device(name=body.name, pipeline_section_id=body.pipeline_section_id,
                    key_hash=hash_device_key(key))
    db.add(device)
    audit(db, user.username, "DEVICE_CREATE", body.name)
    db.commit()
    return DeviceCreated(**DeviceOut.model_validate(device).model_dump(), api_key=key)


@router.post("/devices/{device_id}/enabled", response_model=DeviceOut)
def set_device_enabled(device_id: int, body: EnabledIn, user: User = Depends(admin),
                       db: Session = Depends(get_db)):
    device = db.get(Device, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="Device not found")
    device.enabled = body.enabled
    audit(db, user.username, "DEVICE_ENABLE", f"{device.name}={body.enabled}")
    db.commit()
    return device


@router.post("/devices/{device_id}/rotate-key", response_model=DeviceCreated)
def rotate_device_key(device_id: int, user: User = Depends(admin), db: Session = Depends(get_db)):
    """Rotate a device credential; the old key stops working immediately."""
    device = db.get(Device, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="Device not found")
    key = generate_device_key()
    device.key_hash = hash_device_key(key)
    audit(db, user.username, "DEVICE_KEY_ROTATE", device.name)
    db.commit()
    return DeviceCreated(**DeviceOut.model_validate(device).model_dump(), api_key=key)


@router.get("/audit", response_model=list[AuditOut])
def list_audit(limit: int = Query(100, ge=1, le=500), _: User = Depends(admin),
               db: Session = Depends(get_db)):
    return db.scalars(select(AuditLog).order_by(AuditLog.id.desc()).limit(limit)).all()


# ---- field device endpoints (API key auth, no browser session)
@router.post("/v1/ingest", response_model=ReadingResult, status_code=201)
def ingest(body: DeviceReadingIn, device: Device = Depends(device_auth),
           db: Session = Depends(get_db)):
    device.last_seen = utcnow()
    try:
        processed = process_reading(
            db, ReadingIn(pipeline_section_id=device.pipeline_section_id, **body.model_dump()))
    except ReadingValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors)
    except SectionNotFoundError:
        raise HTTPException(status_code=404, detail="Pipeline section not found")
    return ReadingResult(reading=processed.reading, detected_status=processed.result.status.value,
                         reasons=processed.result.reasons, alert=processed.alert)


@router.get("/v1/device/commands")
def device_commands(device: Device = Depends(device_auth), db: Session = Depends(get_db)):
    rows = db.scalars(
        select(Command).join(Asset, Command.asset_id == Asset.id)
        .where(Command.status == "PENDING", Asset.pipeline_section_id == device.pipeline_section_id)
        .order_by(Command.id)
    ).all()
    return [{"id": c.id, "asset": c.asset.name, "kind": c.asset.kind, "action": c.action} for c in rows]


@router.post("/v1/device/commands/{command_id}/ack")
def device_ack(command_id: int, device: Device = Depends(device_auth), db: Session = Depends(get_db)):
    command = db.get(Command, command_id)
    if command is None or command.asset.pipeline_section_id != device.pipeline_section_id \
            or command.status != "PENDING":
        raise HTTPException(status_code=404, detail="Command not found")
    transition_command(command, "SENT")
    audit(db, device.name, "COMMAND_ACK", f"{command.asset.name}: {command.action}")
    db.commit()
    return {"status": command.status}


@router.post("/v1/device/commands/{command_id}/execute")
def device_command_executed(command_id: int, device: Device = Depends(device_auth),
                             db: Session = Depends(get_db)):
    command = db.get(Command, command_id)
    if command is None or command.asset.pipeline_section_id != device.pipeline_section_id:
        raise HTTPException(status_code=404, detail="Command not found")
    try:
        transition_command(command, "EXECUTED")
    except CommandError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    audit(db, device.name, "COMMAND_EXECUTED", f"{command.asset.name}: {command.action}")
    db.commit()
    return {"status": command.status}


@router.post("/v1/device/commands/{command_id}/verify")
def device_command_verified(command_id: int, body: CommandVerifyIn,
                             device: Device = Depends(device_auth), db: Session = Depends(get_db)):
    command = db.get(Command, command_id)
    if command is None or command.asset.pipeline_section_id != device.pipeline_section_id:
        raise HTTPException(status_code=404, detail="Command not found")
    try:
        transition_command(command, "VERIFIED", body.observed_state)
    except CommandError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    audit(db, device.name, "COMMAND_VERIFIED", f"{command.asset.name}: {body.observed_state}")
    db.commit()
    return {"status": command.status}


# ---- tank levels (10 tanks, percent)
@router.get("/tanks")
def tanks(_: User = Depends(viewer), db: Session = Depends(get_db)):
    return get_tanks(db)


@router.post("/v1/tanks")
def device_tanks(body: TankLevelsIn, device: Device = Depends(device_auth),
                 db: Session = Depends(get_db)):
    try:
        average = average_level(body.levels)
    except TankError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    _set_setting(db, "tank_levels", ",".join(f"{v:.1f}" for v in body.levels))
    device.last_seen = utcnow()
    db.commit()
    return {"average": average}


# ---- CSV export (reports)
def _cell(value):
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@"):
        return "'" + value          # stop spreadsheet formula injection
    return value


def _csv(filename: str, header: list[str], rows) -> Response:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(header)
    for row in rows:
        writer.writerow([_cell(v) for v in row])
    return Response(buffer.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f"attachment; filename={filename}"})


@router.get("/export/readings.csv")
def export_readings(_: User = Depends(viewer), db: Session = Depends(get_db)):
    rows = db.scalars(select(SensorReading).options(joinedload(SensorReading.section))
                      .order_by(SensorReading.id.desc()).limit(5000)).all()
    return _csv("readings.csv",
                ["id", "section", "time_utc", "pressure_bar", "flow_in_lpm", "flow_out_lpm",
                 "difference_lpm", "temperature_c", "sensor_status", "status"],
                [(r.id, r.section.name, r.created_at, r.pressure, r.flow_in, r.flow_out,
                  round(r.flow_difference, 1), r.temperature, r.sensor_status, r.detected_status)
                 for r in rows])


@router.get("/export/alerts.csv")
def export_alerts(_: User = Depends(viewer), db: Session = Depends(get_db)):
    rows = db.scalars(select(Alert).options(joinedload(Alert.section))
                      .order_by(Alert.id.desc()).limit(5000)).all()
    return _csv("alerts.csv", ["id", "section", "severity", "status", "time_utc", "acknowledged_utc", "message"],
                [(a.id, a.section.name, a.severity, a.status, a.created_at, a.acknowledged_at, a.message)
                 for a in rows])
