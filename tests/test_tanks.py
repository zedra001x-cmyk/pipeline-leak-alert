"""Pure tests: tank average, temperature classes, alert webhook."""
import hashlib
import hmac
import json
import random
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app.config import Thresholds
from app.detection import Status, classify_temperature, detect
from app.notify import send_webhook
from app.plant import PlantSim
from app.tanks import TankError, average_level, validate_levels

T = Thresholds()


def test_average_of_ten_tanks():
    assert average_level([10, 20, 30, 40, 50, 60, 70, 80, 90, 100]) == 55.0
    assert average_level([50.0] * 10) == 50.0


def test_average_rejects_bad_input():
    with pytest.raises(TankError):
        average_level([])
    with pytest.raises(TankError):
        average_level([10, 101])
    with pytest.raises(TankError):
        average_level([10, -1])
    with pytest.raises(TankError):
        average_level([10, "x"])


def test_validate_levels_needs_exactly_ten():
    assert len(validate_levels([1] * 10)) == 10
    with pytest.raises(TankError):
        validate_levels([1] * 9)


@pytest.mark.parametrize("temp,expected", [(0, "NORMAL"), (79.9, "NORMAL"), (80, "WARNING"),
                                           (99, "WARNING"), (99.9, "WARNING"), (100, "DANGER"), (250, "DANGER")])
def test_temperature_classes(temp, expected):
    assert classify_temperature(temp, T) == expected


def test_temperature_raises_the_status():
    ok = dict(pressure=80, flow_in=1000, flow_out=990, sensor_status="OK", thresholds=T)
    assert detect(temperature=35, **ok).status is Status.NORMAL
    warm = detect(temperature=85, **ok)
    assert warm.status is Status.WARNING and "Temperature" in warm.reasons[-1]
    assert detect(temperature=100, **ok).status is Status.CRITICAL
    leak = dict(pressure=68, flow_in=1000, flow_out=800, sensor_status="OK", thresholds=T)
    assert detect(temperature=85, **leak).status is Status.LEAK_SUSPECTED   # worse status wins


def test_overheat_event_passes_through_warning_to_danger():
    sim = PlantSim(random.Random(3)); sim.cooldown = 99
    sim.start_overheat(0)
    temps = [sim.tick(True, [True] * 4)[0]["temperature"] for _ in range(10)]
    classes = [classify_temperature(t, T) for t in temps]
    assert "WARNING" in classes and "DANGER" in classes
    sim.repair_all()
    assert classify_temperature(sim.tick(True, [True] * 4)[0]["temperature"], T) == "NORMAL"


def test_webhook_is_signed_and_delivered():
    received = {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            received.update(body=body, sig=self.headers.get("X-Signature"))
            self.send_response(200); self.end_headers()
        def log_message(self, *a):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.handle_request, daemon=True).start()
    payload = {"event": "alert", "severity": "CRITICAL"}
    ok = send_webhook(f"http://127.0.0.1:{server.server_port}/hook", payload, secret="s3cret")
    server.server_close()
    assert ok and json.loads(received["body"]) == payload
    expected = "sha256=" + hmac.new(b"s3cret", received["body"], hashlib.sha256).hexdigest()
    assert received["sig"] == expected


def test_webhook_failures_do_not_raise():
    assert send_webhook("http://127.0.0.1:9/none", {"x": 1}, timeout=0.5) is False
    assert send_webhook("file:///etc/passwd", {"x": 1}) is False
