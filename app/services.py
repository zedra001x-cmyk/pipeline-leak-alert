"""Workflow shared by the API and the dashboard:

Sensor Reading -> Validation -> Leak Detection -> Severity Classification
-> Alert Creation -> Dashboard Display
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app import notify
from app.config import (DEVICE_ONLINE_SECONDS, LEAK_CONFIRMATION_WINDOW_SECONDS,
                        LEAK_CONFIRMATIONS)
from app.control import audit, get_setting, isolate_section
from app.tanks import TankError, average_level, validate_levels
from app.detection import (
    DetectionResult,
    Status,
    detect,
    status_label,
    validate_reading,
)
from app.models import (
    ALERT_ACKNOWLEDGED,
    ALERT_ACTIVE,
    ALERT_CLEARED,
    Alert,
    Asset,
    Command,
    Device,
    PipelineSection,
    SensorReading,
    utcnow,
)
from app.schemas import ReadingIn


class SectionNotFoundError(Exception):
    pass


@dataclass
class ProcessedReading:
    reading: SensorReading
    result: DetectionResult
    alert: Alert | None


def process_reading(db: Session, data: ReadingIn) -> ProcessedReading:
    section = db.get(PipelineSection, data.pipeline_section_id)
    if section is None:
        raise SectionNotFoundError(data.pipeline_section_id)

    # 1. Validation (second layer, independent of the web schema)
    validate_reading(
        data.pressure, data.flow_in, data.flow_out, data.temperature, data.sensor_status
    )

    # 2. Leak detection + severity classification (flow difference calculated inside)
    raw_result = detect(data.pressure, data.flow_in, data.flow_out, data.sensor_status,
                        temperature=data.temperature)
    result = raw_result

    # A single transient reading should not immediately become a confirmed leak.
    # CRITICAL remains immediate so serious pressure/flow/temperature conditions are not delayed.
    if (raw_result.status is Status.LEAK_SUSPECTED and LEAK_CONFIRMATIONS > 1
            and data.sensor_status == "OK"):
        previous = db.scalars(
            select(SensorReading)
            .where(
                SensorReading.pipeline_section_id == section.id,
                SensorReading.sensor_status == "OK",
            )
            .order_by(SensorReading.id.desc())
            .limit(LEAK_CONFIRMATIONS - 1)
        ).all()
        confirmed = len(previous) >= LEAK_CONFIRMATIONS - 1
        if confirmed:
            cutoff = utcnow().timestamp() - LEAK_CONFIRMATION_WINDOW_SECONDS
            for old in previous:
                if old.created_at.timestamp() < cutoff:
                    confirmed = False
                    break
                old_result = detect(
                    old.pressure, old.flow_in, old.flow_out, old.sensor_status,
                    temperature=old.temperature,
                )
                if old_result.status not in (Status.LEAK_SUSPECTED, Status.CRITICAL):
                    confirmed = False
                    break
        if not confirmed:
            result = DetectionResult(
                Status.WARNING,
                raw_result.flow_difference,
                raw_result.reasons + [
                    f"Suspected leak requires {LEAK_CONFIRMATIONS} consecutive readings "
                    f"within {LEAK_CONFIRMATION_WINDOW_SECONDS:g}s before confirmation."
                ],
            )

    # 3. Save the reading with its detected status
    reading = SensorReading(
        pipeline_section_id=section.id,
        pressure=data.pressure,
        flow_in=data.flow_in,
        flow_out=data.flow_out,
        temperature=data.temperature,
        sensor_status=data.sensor_status,
        detected_status=result.status.value,
    )
    db.add(reading)
    db.flush()  # assigns reading.id

    section.status = result.status.value

    # 4. Alerts: one active alert per (section, severity); cleared when the section is normal again
    alert = None
    active = db.scalars(
        select(Alert).where(Alert.pipeline_section_id == section.id, Alert.status == ALERT_ACTIVE)
    ).all()
    if result.status is Status.NORMAL:
        for old in active:
            old.status = ALERT_CLEARED
    elif not any(old.severity == result.status.value for old in active):
        alert = Alert(
            pipeline_section_id=section.id,
            sensor_reading_id=reading.id,
            severity=result.status.value,
            message=f"{status_label(result.status.value)} on {section.name}: "
                    + " ".join(result.reasons),
        )
        db.add(alert)

    db.commit()

    if alert is not None:
        notify.dispatch_alert({
            "event": "alert", "id": alert.id, "severity": alert.severity, "section": section.name,
            "message": alert.message, "created_at": alert.created_at.isoformat(),
        })

    # 5. Emergency isolation (OT): close the valves around a CRITICAL section if enabled
    if result.status is Status.CRITICAL and get_setting(db, "auto_isolation", "0") == "1":
        ids = db.scalars(select(PipelineSection.id).order_by(PipelineSection.id)).all()
        closed = isolate_section(db, ids.index(section.id))
        if closed:
            audit(db, "auto-isolation", "ISOLATE", f"{section.name}: closing {closed} valve(s)")
            db.commit()
    return ProcessedReading(reading, result, alert)


def acknowledge_alert(db: Session, alert_id: int) -> Alert | None:
    alert = db.get(Alert, alert_id)
    if alert is None:
        return None
    if alert.status == ALERT_ACTIVE:
        alert.status = ALERT_ACKNOWLEDGED
        alert.acknowledged_at = utcnow()
        db.commit()
    return alert


def get_dashboard_data(db: Session) -> dict:
    sections = db.scalars(select(PipelineSection).order_by(PipelineSection.id)).all()
    rows = []
    for section in sections:
        last = db.scalars(
            select(SensorReading)
            .where(SensorReading.pipeline_section_id == section.id)
            .order_by(SensorReading.id.desc())
            .limit(1)
        ).first()
        rows.append({"section": section, "reading": last})

    counts = Counter(s.status for s in sections)
    active_alerts = db.scalar(
        select(func.count(Alert.id)).where(Alert.status == ALERT_ACTIVE)
    )
    alerts = db.scalars(
        select(Alert)
        .options(joinedload(Alert.section))
        .order_by(Alert.id.desc())
        .limit(20)
    ).all()

    return {
        "rows": rows,
        "alerts": alerts,
        "summary": {
            "total": len(sections),
            "normal": counts.get("NORMAL", 0),
            "warning": counts.get("WARNING", 0),
            "leak_or_critical": counts.get("LEAK_SUSPECTED", 0) + counts.get("CRITICAL", 0),
            "active_alerts": active_alerts or 0,
        },
    }


def get_tanks(db: Session) -> dict:
    raw = get_setting(db, "tank_levels", "")
    try:
        levels = [float(x) for x in raw.split(",")]
        return {"levels": levels, "average": average_level(levels)}
    except (ValueError, TankError):
        return {"levels": [], "average": None}


def build_state(db: Session, user, sim, mode: str) -> dict:
    """One JSON snapshot for the live operations view."""
    data = get_dashboard_data(db)
    sections = []
    for row in data["rows"]:
        s, r = row["section"], row["reading"]
        sections.append({
            "id": s.id, "name": s.name, "status": s.status,
            "pressure": r.pressure if r else None, "flow_in": r.flow_in if r else None,
            "flow_out": r.flow_out if r else None, "temperature": r.temperature if r else None,
            "updated": r.created_at.isoformat() if r else None,
        })
    pending = {c.asset_id for c in db.scalars(select(Command).where(Command.status == "PENDING"))}
    assets = [
        {"id": a.id, "name": a.name, "kind": a.kind, "position": a.position,
         "state": a.state, "pending": a.id in pending}
        for a in db.scalars(select(Asset).order_by(Asset.id))
    ]
    alerts = [
        {"id": a.id, "section": a.section.name, "severity": a.severity, "message": a.message,
         "status": a.status, "created_at": a.created_at.isoformat()}
        for a in data["alerts"][:8]
    ]
    now = utcnow()
    devices = db.scalars(select(Device).where(Device.enabled.is_(True))).all()
    online = sum(1 for d in devices if d.last_seen and (now - d.last_seen).total_seconds() <= DEVICE_ONLINE_SECONDS)
    return {
        "mode": mode,
        "sim": {"running": bool(sim.running)},
        "auto_isolation": get_setting(db, "auto_isolation", "0") == "1",
        "user": {"username": user.username, "role": user.role},
        "sections": sections, "assets": assets, "alerts": alerts,
        "summary": data["summary"],
        "devices": {"total": len(devices), "online": online},
        "tanks": get_tanks(db),
    }
