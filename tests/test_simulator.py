"""Pure plant-model tests (no database, no web)."""
import random

from app.config import Thresholds
from app.detection import Status, detect
from app.plant import PlantSim

OPEN = [True] * 4
T = Thresholds()


def sim(seed=1):
    return PlantSim(random.Random(seed))


def test_normal_operation_has_flow_and_no_big_difference():
    s = sim()
    s.cooldown = 99                      # no random leaks
    for r in s.tick(True, OPEN):
        assert r["flow_in"] > 900 and abs(r["flow_in"] - r["flow_out"]) < 40 and r["pressure"] > 70


def test_pump_off_means_no_flow():
    s = sim(); s.cooldown = 99
    assert all(r["flow_in"] == 0 and r["flow_out"] == 0 for r in s.tick(False, OPEN))


def test_closed_valve_cuts_flow_downstream():
    s = sim(); s.cooldown = 99
    r = s.tick(True, [True, False, True, True])
    assert r[0]["flow_in"] > 900
    assert r[1]["flow_in"] == 0 and r[2]["flow_in"] == 0


def test_leak_grows_and_is_detected():
    s = sim(); s.cooldown = 99
    s.start_leak(1)
    statuses = []
    for _ in range(12):
        r = s.tick(True, OPEN)[1]
        statuses.append(detect(r["pressure"], r["flow_in"], r["flow_out"], "OK", T).status)
    assert Status.WARNING in statuses and Status.CRITICAL in statuses


def test_repair_all_is_immediate_and_blocks_new_random_leaks():
    s = sim(); s.start_leak(0)
    for _ in range(6):
        s.tick(True, OPEN)
    assert max(s.loss) > 100
    s.repair_all()
    assert s.loss == [0.0, 0.0, 0.0]
    for _ in range(15):
        for r in s.tick(True, OPEN):
            assert detect(r["pressure"], r["flow_in"], r["flow_out"], "OK", T).status is Status.NORMAL


def test_isolated_section_reads_idle_not_leak():
    s = sim(); s.cooldown = 99
    s.loss = [0.0, 300.0, 0.0]
    r = s.tick(True, [True, False, False, True])[1]       # both valves around B closed
    assert detect(r["pressure"], r["flow_in"], r["flow_out"], "OK", T).status is Status.NORMAL
