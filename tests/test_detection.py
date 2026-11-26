"""Unit tests for the pure detection logic (no database, no web)."""
import math

import pytest

from app.config import DEMO_SCENARIOS, Thresholds
from app.detection import ReadingValidationError, Status, detect, validate_reading

T = Thresholds()  # class defaults, independent of any environment overrides


def run(pressure, flow_in, flow_out, sensor_status="OK"):
    return detect(pressure, flow_in, flow_out, sensor_status, T)


def test_normal_reading():
    result = run(80, 1000, 980)
    assert result.status is Status.NORMAL
    assert result.flow_difference == 20


def test_warning_reading():
    assert run(72, 1000, 920).status is Status.WARNING


def test_suspected_leak():
    assert run(68, 1000, 800).status is Status.LEAK_SUSPECTED


def test_critical_reading():
    assert run(55, 1000, 700).status is Status.CRITICAL


@pytest.mark.parametrize("scenario", DEMO_SCENARIOS, ids=lambda s: s["key"])
def test_demo_scenarios_match_expected(scenario):
    result = run(scenario["pressure"], scenario["flow_in"], scenario["flow_out"])
    assert result.status.value == scenario["expected"]


def test_warning_threshold_is_inclusive():
    assert run(80, 1000, 950).status is Status.WARNING  # difference exactly 50
    assert run(80, 1000, 951).status is Status.NORMAL   # difference 49


def test_low_pressure_alone_is_leak_suspected():
    assert run(59, 1000, 1000).status is Status.LEAK_SUSPECTED


def test_pressure_approaching_minimum_is_warning():
    assert run(65, 1000, 1000).status is Status.WARNING


def test_large_flow_difference_with_good_pressure_is_not_critical():
    assert run(80, 1000, 600).status is Status.LEAK_SUSPECTED


def test_severe_pressure_drop_with_leak_level_flow_is_critical():
    assert run(35, 1000, 840).status is Status.CRITICAL


def test_outflow_exceeding_inflow_is_warning():
    assert run(80, 900, 1000).status is Status.WARNING


def test_idle_section_is_normal_even_with_low_pressure():
    assert run(10, 0, 0).status is Status.NORMAL
    assert run(10, 5, 0).status is Status.NORMAL      # at the idle limit
    assert run(10, 6, 0).status is not Status.NORMAL  # just above it


def test_faulty_sensor_skips_leak_rules():
    result = run(10, 1000, 100, sensor_status="FAULT")
    assert result.status is Status.WARNING
    assert "Sensor" in result.reasons[0]


def test_custom_thresholds_change_the_outcome():
    strict = Thresholds(warning_flow_diff=10, leak_flow_diff=20, critical_flow_diff=30)
    assert detect(80, 1000, 975, "OK", strict).status is Status.LEAK_SUSPECTED


def test_invalid_threshold_order_is_rejected():
    with pytest.raises(ValueError):
        Thresholds(warning_flow_diff=200, leak_flow_diff=100, critical_flow_diff=300)


# ---- validation -----------------------------------------------------------

def test_valid_reading_passes_validation():
    validate_reading(80, 1000, 980, 35)  # does not raise


@pytest.mark.parametrize(
    "args",
    [
        (-1, 1000, 980, 35),        # negative pressure
        (80, -5, 980, 35),          # negative flow in
        (80, 1000, -980, 35),       # negative flow out
        (80, 1000, 980, -500),      # temperature below limit
        (9999, 1000, 980, 35),      # pressure above limit
        (80, 99999999, 980, 35),    # flow above limit
    ],
)
def test_negative_or_out_of_range_values_are_rejected(args):
    with pytest.raises(ReadingValidationError):
        validate_reading(*args)


@pytest.mark.parametrize(
    "args",
    [
        (None, 1000, 980, 35),      # missing
        ("abc", 1000, 980, 35),     # not a number
        (80, "1000", 980, 35),      # numeric string is not accepted at this layer
        (True, 1000, 980, 35),      # bool is not a sensor value
        (math.nan, 1000, 980, 35),
        (math.inf, 1000, 980, 35),
    ],
)
def test_missing_or_non_numeric_values_are_rejected(args):
    with pytest.raises(ReadingValidationError):
        validate_reading(*args)


def test_unknown_sensor_status_is_rejected():
    with pytest.raises(ReadingValidationError):
        validate_reading(80, 1000, 980, 35, sensor_status="BROKEN")


def test_leak_confirmation_requires_multiple_readings(db_session):
    from app.models import PipelineSection
    from app.database import seed_sections
    from sqlalchemy import select
    from app.schemas import ReadingIn
    from app.services import process_reading

    seed_sections(db_session)
    section = db_session.scalar(select(PipelineSection).where(PipelineSection.id == 1))
    assert section is not None
    first = process_reading(db_session, ReadingIn(pipeline_section_id=1, pressure=68, flow_in=1000, flow_out=800, temperature=35))
    second = process_reading(db_session, ReadingIn(pipeline_section_id=1, pressure=68, flow_in=1000, flow_out=800, temperature=35))
    third = process_reading(db_session, ReadingIn(pipeline_section_id=1, pressure=68, flow_in=1000, flow_out=800, temperature=35))
    assert first.result.status is Status.WARNING
    assert second.result.status is Status.WARNING
    assert third.result.status is Status.LEAK_SUSPECTED
