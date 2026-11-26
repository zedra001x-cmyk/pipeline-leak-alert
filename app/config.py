"""Central configuration.

Every threshold and limit lives here so it can be changed in ONE place.
Values can be overridden with environment variables (no secrets are needed).

The defaults are PLACEHOLDERS, not engineering standards. Calibrate every threshold for
your pipeline before relying on alerts.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"Environment variable {name} must be a number") from exc


@dataclass(frozen=True)
class Thresholds:
    """Detection thresholds (simulated).

    flow difference = flow_in - flow_out   (litres per minute)
    pressure                               (bar)
    """

    warning_flow_diff: float = 50.0      # L/min  -> WARNING at or above
    leak_flow_diff: float = 150.0        # L/min  -> LEAK SUSPECTED at or above
    critical_flow_diff: float = 250.0    # L/min  -> serious flow indicator for CRITICAL
    min_pressure: float = 60.0           # bar    -> below this = low pressure
    pressure_warning_margin: float = 10.0  # bar  -> "approaching minimum" band
    severe_pressure: float = 40.0        # bar    -> below this = severe pressure drop
    temp_danger: float = 100.0           # deg C  -> DANGER (CRITICAL) at or above
    idle_flow: float = 5.0               # L/min  -> both flows at/below this = section idle or isolated
    temp_warning: float = 80.0           # deg C  -> WARNING at or above

    def __post_init__(self) -> None:
        if not (0 <= self.warning_flow_diff < self.leak_flow_diff < self.critical_flow_diff):
            raise ValueError(
                "Thresholds must satisfy 0 <= warning < leak < critical flow difference"
            )
        if not (0 <= self.severe_pressure < self.min_pressure):
            raise ValueError("severe_pressure must be >= 0 and below min_pressure")
        if not (0 < self.temp_warning < self.temp_danger):
            raise ValueError("temp_warning must be above 0 and below temp_danger")
        if self.pressure_warning_margin < 0:
            raise ValueError("pressure_warning_margin must not be negative")


@dataclass(frozen=True)
class InputLimits:
    """Sanity limits for incoming sensor values (simulated)."""

    pressure_min: float = 0.0
    pressure_max: float = 200.0       # bar
    flow_min: float = 0.0
    flow_max: float = 10_000.0        # L/min
    temperature_min: float = -50.0    # deg C
    temperature_max: float = 150.0    # deg C


SENSOR_STATUSES = ("OK", "FAULT", "OFFLINE")


def load_thresholds() -> Thresholds:
    d = Thresholds()
    return Thresholds(
        warning_flow_diff=_float_env("WARNING_FLOW_DIFF", d.warning_flow_diff),
        leak_flow_diff=_float_env("LEAK_FLOW_DIFF", d.leak_flow_diff),
        critical_flow_diff=_float_env("CRITICAL_FLOW_DIFF", d.critical_flow_diff),
        min_pressure=_float_env("MIN_PRESSURE", d.min_pressure),
        pressure_warning_margin=_float_env("PRESSURE_WARNING_MARGIN", d.pressure_warning_margin),
        severe_pressure=_float_env("SEVERE_PRESSURE", d.severe_pressure),
        idle_flow=_float_env("IDLE_FLOW", d.idle_flow),
        temp_warning=_float_env("TEMP_WARNING", d.temp_warning),
        temp_danger=_float_env("TEMP_DANGER", d.temp_danger),
    )


THRESHOLDS = load_thresholds()
LIMITS = InputLimits()
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./pipeline_leak_alert.db")

# Operations
SIMULATION = os.environ.get("SIMULATION", "1") == "1"   # 1 = built-in virtual devices, 0 = real devices only
SIM_INTERVAL = _float_env("SIM_INTERVAL", 2.0)          # seconds between simulated readings
SESSION_HOURS = _float_env("SESSION_HOURS", 12.0)
DEVICE_ONLINE_SECONDS = _float_env("DEVICE_ONLINE_SECONDS", 60.0)
ALERT_WEBHOOK_URL = os.environ.get("ALERT_WEBHOOK_URL", "")        # optional: POST new alerts here
ALERT_WEBHOOK_SECRET = os.environ.get("ALERT_WEBHOOK_SECRET", "")  # optional: HMAC-SHA256 signing key
PBKDF2_ITERATIONS = int(_float_env("PBKDF2_ITERATIONS", 200_000))
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "0") == "1"

# Detection hardening: a suspected leak must persist across multiple readings before
# it becomes a confirmed LEAK_SUSPECTED alert. CRITICAL conditions remain immediate.
LEAK_CONFIRMATIONS = int(_float_env("LEAK_CONFIRMATIONS", 3))
LEAK_CONFIRMATION_WINDOW_SECONDS = _float_env("LEAK_CONFIRMATION_WINDOW_SECONDS", 30.0)

# Simple in-process protection for field-device endpoints. Keep this comfortably above
# the normal simulator rate; use a shared gateway/rate limiter for multi-worker production.
DEVICE_REQUESTS_PER_MINUTE = int(_float_env("DEVICE_REQUESTS_PER_MINUTE", 60))

# Demo scenarios shown as buttons on the dashboard (simulated values only).
DEMO_SCENARIOS = [
    {"key": "normal", "label": "1 Normal", "pressure": 80, "flow_in": 1000,
     "flow_out": 980, "temperature": 35, "expected": "NORMAL"},
    {"key": "warning", "label": "2 Warning", "pressure": 72, "flow_in": 1000,
     "flow_out": 920, "temperature": 35, "expected": "WARNING"},
    {"key": "leak", "label": "3 Suspected leak", "pressure": 68, "flow_in": 1000,
     "flow_out": 800, "temperature": 35, "expected": "LEAK_SUSPECTED"},
    {"key": "critical", "label": "4 Critical", "pressure": 55, "flow_in": 1000,
     "flow_out": 700, "temperature": 35, "expected": "CRITICAL"},
]
