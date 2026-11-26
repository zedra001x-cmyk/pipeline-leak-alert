"""Pydantic schemas: request validation (input) and JSON responses (output)."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.config import LIMITS


class ReadingIn(BaseModel):
    """A simulated sensor reading. Unknown fields are rejected."""

    model_config = ConfigDict(extra="forbid")

    pipeline_section_id: int = Field(gt=0)
    pressure: float = Field(ge=LIMITS.pressure_min, le=LIMITS.pressure_max)
    flow_in: float = Field(ge=LIMITS.flow_min, le=LIMITS.flow_max)
    flow_out: float = Field(ge=LIMITS.flow_min, le=LIMITS.flow_max)
    temperature: float = Field(ge=LIMITS.temperature_min, le=LIMITS.temperature_max)
    sensor_status: Literal["OK", "FAULT", "OFFLINE"] = "OK"


class SectionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    start_point: str
    end_point: str
    status: str
    created_at: datetime


class ReadingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    pipeline_section_id: int
    pressure: float
    flow_in: float
    flow_out: float
    flow_difference: float
    temperature: float
    sensor_status: str
    detected_status: str
    created_at: datetime


class AlertOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    pipeline_section_id: int
    sensor_reading_id: int
    severity: str
    message: str
    status: str
    created_at: datetime
    acknowledged_at: datetime | None = None


class ReadingResult(BaseModel):
    reading: ReadingOut
    detected_status: str
    reasons: list[str]
    alert: AlertOut | None = None


class DeviceReadingIn(BaseModel):
    """What a field device sends: its section is known from its API key."""

    model_config = ConfigDict(extra="forbid")

    pressure: float = Field(ge=LIMITS.pressure_min, le=LIMITS.pressure_max)
    flow_in: float = Field(ge=LIMITS.flow_min, le=LIMITS.flow_max)
    flow_out: float = Field(ge=LIMITS.flow_min, le=LIMITS.flow_max)
    temperature: float = Field(ge=LIMITS.temperature_min, le=LIMITS.temperature_max)
    sensor_status: Literal["OK", "FAULT", "OFFLINE"] = "OK"


class CommandIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: str = Field(min_length=3, max_length=10)


class CommandVerifyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    observed_state: Literal["OPEN", "CLOSED", "RUNNING", "STOPPED"]


class LeakIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    section: int = Field(ge=0, le=2)


class EnabledIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool


class DeviceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=60)
    pipeline_section_id: int = Field(gt=0)


class DeviceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    pipeline_section_id: int
    enabled: bool
    last_seen: datetime | None = None
    created_at: datetime


class DeviceCreated(DeviceOut):
    api_key: str


class AuditOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    action: str
    detail: str
    created_at: datetime


class TankLevelsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    levels: list[float] = Field(min_length=10, max_length=10)
