"""Optional alert notifications: POST new alerts as JSON to ALERT_WEBHOOK_URL.

If ALERT_WEBHOOK_SECRET is set, the body is signed: header X-Signature: sha256=<hmac hex>.
Point it at a chat bot, an SMS/WhatsApp gateway or any HTTP endpoint. Runs in a background
thread so a slow receiver never delays reading ingestion.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import threading
import urllib.request

from app import config

logger = logging.getLogger("pipeline_leak_alert")


def send_webhook(url: str, payload: dict, secret: str = "", timeout: float = 5.0) -> bool:
    if not url.lower().startswith(("http://", "https://")):
        return False
    body = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    if secret:
        headers["X-Signature"] = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    try:
        request = urllib.request.Request(url, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return 200 <= response.status < 300
    except Exception as exc:  # never let a notification break the pipeline
        logger.warning("Alert webhook failed: %s", exc)
        return False


def dispatch_alert(payload: dict) -> None:
    if config.ALERT_WEBHOOK_URL:
        threading.Thread(
            target=send_webhook,
            args=(config.ALERT_WEBHOOK_URL, payload, config.ALERT_WEBHOOK_SECRET),
            daemon=True,
        ).start()
