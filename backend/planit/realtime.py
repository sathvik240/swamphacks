"""WebSocket fan-out to dashboards plus an optional outbound webhook."""
import asyncio
import json
import logging

import httpx
from fastapi import WebSocket

from . import config

log = logging.getLogger(__name__)


class Hub:
    def __init__(self):
        self.clients: set[WebSocket] = set()

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.clients.add(ws)

    def disconnect(self, ws: WebSocket):
        self.clients.discard(ws)

    async def broadcast(self, event_type: str, payload: dict):
        msg = json.dumps({"type": event_type, "data": payload}, default=str)
        dead = []
        for ws in list(self.clients):
            try:
                await ws.send_text(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

    async def notify(self, text: str, payload: dict):
        """Push to the configured webhook (Slack-compatible `text` field)."""
        if not config.ALERT_WEBHOOK:
            return
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                await c.post(config.ALERT_WEBHOOK, json={"text": text, "planit": payload})
        except Exception as e:  # never let a notification failure break monitoring
            log.warning("webhook failed: %s", e)

    def notify_bg(self, text: str, payload: dict):
        asyncio.get_running_loop().create_task(self.notify(text, payload))
