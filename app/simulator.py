"""Virtual field devices: runs the PlantSim model (app/plant.py) in the background.

run_simulator() feeds its readings through the
same process_reading() path that real devices use. Set SIMULATION=0 to use real devices only.
"""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select

from app.control import apply_pending_commands, set_setting
from app.database import SessionLocal
from app.models import Asset, PipelineSection
from app.plant import PlantSim
from app.schemas import ReadingIn
from app.services import process_reading

logger = logging.getLogger("pipeline_leak_alert")


def sim_step(sim: PlantSim) -> None:
    """One simulator cycle: apply queued commands, then (if running) push readings."""
    with SessionLocal() as db:
        apply_pending_commands(db)
        if not sim.running:
            return
        assets = db.scalars(select(Asset)).all()
        pump_on = any(a.kind == "PUMP" and a.state == "RUNNING" for a in assets)
        valves = [True] * 4
        for a in assets:
            if a.kind == "VALVE":
                valves[a.position] = a.state == "OPEN"
        sections = db.scalars(select(PipelineSection).order_by(PipelineSection.id)).all()
        readings = sim.tick(pump_on, valves)
        for section, reading in zip(sections, readings):
            process_reading(db, ReadingIn(pipeline_section_id=section.id, **reading))
        levels = sim.tick_tanks(pump_on, valves[3] and readings[2]["flow_out"] > 5)
        set_setting(db, "tank_levels", ",".join(f"{v:.1f}" for v in levels))
        db.commit()


async def run_simulator(sim: PlantSim, interval: float) -> None:
    while True:
        await asyncio.sleep(interval)
        try:
            await asyncio.to_thread(sim_step, sim)
        except Exception:  # keep the service alive
            logger.exception("Simulator step failed")
