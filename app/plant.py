"""Pure plant model for the virtual field devices (no database, no web)."""
from __future__ import annotations

import random


class PlantSim:
    def __init__(self, rng: random.Random | None = None):
        self.rng = rng or random.Random()
        self.running = True
        self.loss = [0.0, 0.0, 0.0]     # L/min currently leaking per section
        self.target = [0.0, 0.0, 0.0]
        self.hold = [0, 0, 0]           # ticks a leak lasts before it is "fixed"
        self.cooldown = 0               # ticks with no random leak (after Repair all)
        self.heat = [0.0, 0.0, 0.0]     # extra deg C per section (overheating event)
        self.heat_hold = [0, 0, 0]
        self.tanks = [round(self.rng.uniform(70, 90), 1) for _ in range(5)] + \
                     [round(self.rng.uniform(15, 35), 1) for _ in range(5)]

    def start_leak(self, section: int, size: float = 330.0, ticks: int = 14) -> None:
        self.target[section] = size
        self.hold[section] = ticks

    def start_overheat(self, section: int, ticks: int = 12) -> None:
        self.heat_hold[section] = ticks

    def tick_tanks(self, pump_on: bool, outlet_flowing: bool) -> list[float]:
        """Depot A tanks (1-5) drain while pumping; Depot B tanks (6-10) fill while oil arrives."""
        for i in range(10):
            delta = -0.35 if (i < 5 and pump_on) else (0.35 if (i >= 5 and outlet_flowing) else 0.0)
            self.tanks[i] = round(min(100.0, max(0.0, self.tanks[i] + delta + self.rng.uniform(-0.05, 0.05))), 1)
        return list(self.tanks)

    def repair_all(self) -> None:
        self.heat = [0.0, 0.0, 0.0]
        self.heat_hold = [0, 0, 0]
        self.loss = [0.0, 0.0, 0.0]
        self.target = [0.0, 0.0, 0.0]
        self.hold = [0, 0, 0]
        self.cooldown = 20

    def _update_heat(self) -> None:
        for i in range(3):
            if self.heat_hold[i] > 0:
                self.heat[i] = min(75.0, self.heat[i] + 9.0)
                self.heat_hold[i] -= 1
            else:
                self.heat[i] = max(0.0, self.heat[i] - 9.0)

    def _update_leaks(self) -> None:
        if self.cooldown > 0:
            self.cooldown -= 1
        elif not any(v > 0 for v in self.target) and self.rng.random() < 0.12:
            self.start_leak(self.rng.randrange(3), self.rng.uniform(140, 330), self.rng.randint(9, 16))
        for i in range(3):
            if self.target[i] > 0:
                self.loss[i] = min(self.target[i], self.loss[i] + 32)
                self.hold[i] -= 1
                if self.hold[i] <= 0:
                    self.target[i] = 0.0
            else:
                self.loss[i] = max(0.0, self.loss[i] - 45)

    def tick(self, pump_on: bool, valves_open: list[bool]) -> list[dict]:
        """Return one reading per section. valves_open[k] is the valve at boundary k (0..3)."""
        r = self.rng
        self._update_leaks()
        self._update_heat()
        flow = 1000 + r.uniform(-15, 15) if pump_on else 0.0
        upstream_loss = 0.0
        out = []
        for i in range(3):
            if not valves_open[i]:
                flow = 0.0
            flowing = flow > 0
            loss = max(0.0, self.loss[i] + r.uniform(-6, 6)) if (flowing and self.loss[i] > 0) else 0.0
            if flowing:
                pressure = 82 - 2 * i + r.uniform(-1.2, 1.2) - loss * 0.08 - upstream_loss * 0.02
                flow_out = max(0.0, flow - loss + r.uniform(-8, 8))
            else:
                pressure = 45 + r.uniform(-1, 1)   # static, shut-in pressure
                flow_out = 0.0
            out.append({
                "pressure": round(min(max(pressure, 0.0), 150.0), 1),
                "flow_in": round(flow, 1),
                "flow_out": round(flow_out, 1),
                "temperature": round(34 + i * 0.8 + r.uniform(-0.7, 0.7) + self.heat[i], 1),
                "sensor_status": "OK",
            })
            upstream_loss += loss
            flow = flow_out if valves_open[i + 1] else 0.0
        return out
