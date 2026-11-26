"""Minimal field-device client (standard library only). Use it to test ingestion, or as a
template for an ESP32/Raspberry Pi sensor node.

  python examples/device_client.py --url http://localhost:8010 --key pk_XXXX
Replace read_sensors() with real sensor reads. Apply any returned commands to your hardware.
"""
import argparse
import json
import random
import time
import urllib.request


def call(url, key, body=None, method="GET"):
    req = urllib.request.Request(url, method=method, headers={"X-Device-Key": key, "Content-Type": "application/json"},
                                 data=json.dumps(body).encode() if body is not None else None)
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def read_sensors():
    flow_in = 1000 + random.uniform(-15, 15)
    return {"pressure": round(80 + random.uniform(-1, 1), 1), "flow_in": round(flow_in, 1),
            "flow_out": round(flow_in - random.uniform(0, 10), 1), "temperature": round(35 + random.uniform(-1, 1), 1),
            "sensor_status": "OK"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--key", required=True)
    ap.add_argument("--interval", type=float, default=5.0)
    args = ap.parse_args()
    while True:
        result = call(args.url + "/api/v1/ingest", args.key, read_sensors(), "POST")
        print("sent ->", result["detected_status"])
        for cmd in call(args.url + "/api/v1/device/commands", args.key):
            print("APPLY COMMAND:", cmd)           # drive your relay/valve here
            call(f"{args.url}/api/v1/device/commands/{cmd['id']}/ack", args.key, {}, "POST")
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
