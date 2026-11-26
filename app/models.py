"""SQLAlchemy models: pipeline_sections, sensor_readings, alerts."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

ALERT_ACTIVE = "ACTIVE"
ALERT_ACKNOWLEDGED = "ACKNOWLEDGED"
ALERT_CLEARED = "CLEARED"


def utcnow() -> datetime:
    """Naive UTC timestamp (SQLite does not store time zones)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class PipelineSection(Base):
    __tablename__ = "pipeline_sections"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    start_point: Mapped[str] = mapped_column(String(80))
    end_point: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(20), default="NO_DATA")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    readings: Mapped[list["SensorReading"]] = relationship(back_populates="section")
    alerts: Mapped[list["Alert"]] = relationship(back_populates="section")


class SensorReading(Base):
    __tablename__ = "sensor_readings"

    id: Mapped[int] = mapped_column(primary_key=True)
    pipeline_section_id: Mapped[int] = mapped_column(
        ForeignKey("pipeline_sections.id"), index=True
    )
    pressure: Mapped[float] = mapped_column(Float)
    flow_in: Mapped[float] = mapped_column(Float)
    flow_out: Mapped[float] = mapped_column(Float)
    temperature: Mapped[float] = mapped_column(Float)
    sensor_status: Mapped[str] = mapped_column(String(10), default="OK")
    detected_status: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    section: Mapped[PipelineSection] = relationship(back_populates="readings")

    @property
    def flow_difference(self) -> float:
        return self.flow_in - self.flow_out


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(primary_key=True)
    pipeline_section_id: Mapped[int] = mapped_column(
        ForeignKey("pipeline_sections.id"), index=True
    )
    sensor_reading_id: Mapped[int] = mapped_column(ForeignKey("sensor_readings.id"))
    severity: Mapped[str] = mapped_column(String(20))
    message: Mapped[str] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(20), default=ALERT_ACTIVE)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    section: Mapped[PipelineSection] = relationship(back_populates="alerts")
    reading: Mapped[SensorReading] = relationship()


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(60), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default="viewer")  # viewer | operator | admin
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class UserSession(Base):
    __tablename__ = "user_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    expires_at: Mapped[datetime] = mapped_column(DateTime)

    user: Mapped[User] = relationship()


class Device(Base):
    """An IoT sensor node. It authenticates with an API key (stored only as a hash)."""

    __tablename__ = "devices"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(60), unique=True)
    pipeline_section_id: Mapped[int] = mapped_column(ForeignKey("pipeline_sections.id"))
    key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_seen: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Asset(Base):
    """Controllable equipment (OT): isolation valves and the pump."""

    __tablename__ = "assets"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(60), unique=True)
    kind: Mapped[str] = mapped_column(String(10))      # VALVE | PUMP
    position: Mapped[int] = mapped_column(Integer)     # valve boundary 0..3, pump 0
    state: Mapped[str] = mapped_column(String(10))     # OPEN/CLOSED, RUNNING/STOPPED
    pipeline_section_id: Mapped[int] = mapped_column(ForeignKey("pipeline_sections.id"))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Command(Base):
    """A control command waiting for a device (or the simulator) to apply it."""

    __tablename__ = "commands"

    id: Mapped[int] = mapped_column(primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id"))
    action: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(10), default="PENDING")  # PENDING | APPLIED
    requested_by: Mapped[str] = mapped_column(String(60))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    asset: Mapped[Asset] = relationship()


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(60))
    action: Mapped[str] = mapped_column(String(40))
    detail: Mapped[str] = mapped_column(String(300), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(40), primary_key=True)
    value: Mapped[str] = mapped_column(String(100))
