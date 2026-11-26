"""Rule-based leak detection. Pure Python: no database, no web framework.

Keeping this file independent makes it easy to read, explain and unit test.
There is NO machine learning here, and the rules are NOT scientifically
validated leak-detection algorithms. They are a teaching example.

Rules (evaluated top to bottom, first match wins):

  flow_difference = flow_in - flow_out

  CRITICAL        (flow_difference >= critical_flow_diff AND pressure < min_pressure)
                  OR (pressure < severe_pressure AND flow_difference >= leak_flow_diff)
  LEAK_SUSPECTED  flow_difference >= leak_flow_diff OR pressure < min_pressure
  WARNING         flow_difference >= warning_flow_diff
                  OR pressure < min_pressure + pressure_warning_margin
                  OR flow_out exceeds flow_in by >= warning_flow_diff (odd, check sensors)
  NORMAL          none of the above

If both flows are at or below idle_flow the section is idle/isolated (NORMAL).
If the sensor itself reports FAULT/OFFLINE the numbers cannot be trusted, so
the leak rules are skipped and a WARNING is raised to inspect the sensor.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum

from app.config import LIMITS, SENSOR_STATUSES, THRESHOLDS, InputLimits, Thresholds


class Status(str, Enum):
    NORMAL = "NORMAL"
    WARNING = "WARNING"
    LEAK_SUSPECTED = "LEAK_SUSPECTED"
    CRITICAL = "CRITICAL"


STATUS_LABELS = {
    "NORMAL": "Normal",
    "WARNING": "Warning",
    "LEAK_SUSPECTED": "Leak suspected",
    "CRITICAL": "Critical",
    "NO_DATA": "No data",
}


def status_label(value: str) -> str:
    return STATUS_LABELS.get(value, value)


class ReadingValidationError(ValueError):
    """Raised when a sensor reading is missing, non-numeric or out of range."""

    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


@dataclass(frozen=True)
class DetectionResult:
    status: Status
    flow_difference: float
    reasons: list[str] = field(default_factory=list)


def validate_reading(
    pressure,
    flow_in,
    flow_out,
    temperature,
    sensor_status: str = "OK",
    limits: InputLimits = LIMITS,
) -> None:
    """Raise ReadingValidationError if any value is missing, non-numeric,
    not finite, or outside the allowed range (so negatives are rejected)."""
    errors: list[str] = []
    checks = (
        ("pressure", pressure, limits.pressure_min, limits.pressure_max, "bar"),
        ("flow_in", flow_in, limits.flow_min, limits.flow_max, "L/min"),
        ("flow_out", flow_out, limits.flow_min, limits.flow_max, "L/min"),
        ("temperature", temperature, limits.temperature_min, limits.temperature_max, "C"),
    )
    for name, value, low, high, unit in checks:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            errors.append(f"{name} is required and must be a number")
        elif not math.isfinite(value):
            errors.append(f"{name} must be a finite number")
        elif value < low or value > high:
            errors.append(f"{name} must be between {low:g} and {high:g} {unit}")
    if sensor_status not in SENSOR_STATUSES:
        errors.append(f"sensor_status must be one of {', '.join(SENSOR_STATUSES)}")
    if errors:
        raise ReadingValidationError(errors)


def _detect_flow(
    pressure: float,
    flow_in: float,
    flow_out: float,
    sensor_status: str = "OK",
    thresholds: Thresholds | None = None,
) -> DetectionResult:
    """Flow and pressure rules only."""
    t = thresholds or THRESHOLDS
    diff = flow_in - flow_out

    if sensor_status != "OK":
        return DetectionResult(
            Status.WARNING,
            diff,
            [f"Sensor status is {sensor_status}: readings are unreliable, so the leak "
             "rules were not applied. Inspect the sensor."],
        )

    if flow_in <= t.idle_flow and flow_out <= t.idle_flow:
        return DetectionResult(
            Status.NORMAL, diff,
            ["No flow: the section is idle or isolated, so leak rules were not applied."],
        )

    flow_warning = diff >= t.warning_flow_diff
    flow_leak = diff >= t.leak_flow_diff
    flow_critical = diff >= t.critical_flow_diff
    outflow_excess = diff <= -t.warning_flow_diff
    pressure_low = pressure < t.min_pressure
    pressure_near = pressure < t.min_pressure + t.pressure_warning_margin
    pressure_severe = pressure < t.severe_pressure

    reasons: list[str] = []
    if flow_critical:
        reasons.append(f"Flow difference {diff:g} L/min is at or above the critical "
                       f"threshold ({t.critical_flow_diff:g}).")
    elif flow_leak:
        reasons.append(f"Flow difference {diff:g} L/min is at or above the leak "
                       f"threshold ({t.leak_flow_diff:g}).")
    elif flow_warning:
        reasons.append(f"Flow difference {diff:g} L/min is at or above the warning "
                       f"threshold ({t.warning_flow_diff:g}).")
    elif outflow_excess:
        reasons.append(f"Flow out exceeds flow in by {-diff:g} L/min; check the flow sensors.")

    if pressure_severe:
        reasons.append(f"Pressure {pressure:g} bar is a severe drop (below {t.severe_pressure:g}).")
    elif pressure_low:
        reasons.append(f"Pressure {pressure:g} bar is below the minimum ({t.min_pressure:g}).")
    elif pressure_near:
        reasons.append(f"Pressure {pressure:g} bar is approaching the minimum ({t.min_pressure:g}).")

    if (flow_critical and pressure_low) or (pressure_severe and flow_leak):
        status = Status.CRITICAL
    elif flow_leak or pressure_low:
        status = Status.LEAK_SUSPECTED
    elif flow_warning or pressure_near or outflow_excess:
        status = Status.WARNING
    else:
        status = Status.NORMAL
        reasons = [f"Flow difference {diff:g} L/min and pressure {pressure:g} bar are within "
                   "the normal range."]

    return DetectionResult(status, diff, reasons)


_RANK = {Status.NORMAL: 0, Status.WARNING: 1, Status.LEAK_SUSPECTED: 2, Status.CRITICAL: 3}


def classify_temperature(temperature: float, thresholds: Thresholds | None = None) -> str:
    """NORMAL below 80, WARNING from 80 up to (not including) 100, DANGER at 100 or above."""
    t = thresholds or THRESHOLDS
    if temperature >= t.temp_danger:
        return "DANGER"
    if temperature >= t.temp_warning:
        return "WARNING"
    return "NORMAL"


def detect(
    pressure: float,
    flow_in: float,
    flow_out: float,
    sensor_status: str = "OK",
    thresholds: Thresholds | None = None,
    temperature: float | None = None,
) -> DetectionResult:
    """Classify one validated reading: flow/pressure rules, then the temperature rule.
    The worse of the two wins. DANGER temperature maps to CRITICAL."""
    t = thresholds or THRESHOLDS
    result = _detect_flow(pressure, flow_in, flow_out, sensor_status, t)
    if temperature is None or sensor_status != "OK":
        return result
    level = classify_temperature(temperature, t)
    if level == "NORMAL":
        return result
    if level == "DANGER":
        status, text = Status.CRITICAL, f"Temperature {temperature:g} C is at or above the danger limit ({t.temp_danger:g})."
    else:
        status, text = Status.WARNING, f"Temperature {temperature:g} C is at or above the warning limit ({t.temp_warning:g})."
    if _RANK[status] <= _RANK[result.status]:
        return DetectionResult(result.status, result.flow_difference, result.reasons + [text])
    reasons = [text] if result.status is Status.NORMAL else result.reasons + [text]
    return DetectionResult(status, result.flow_difference, reasons)
