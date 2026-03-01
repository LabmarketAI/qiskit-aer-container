"""
Tornado handlers for jupyter_pubsub.

  GET  /pubsub/ws/<cell_name>   — WebSocket; streams cell outputs to subscriber
  GET  /pubsub/cells            — REST; lists active cell names and kernel count
"""

import asyncio
import json
import logging

from tornado import websocket, web

from .iopub_listener import listen_iopub
from .registry import add_subscriber, remove_subscriber

log = logging.getLogger(__name__)


def _ensure_listeners(settings: dict) -> None:
    """
    Start an IOPub listener task for every running kernel that doesn't have one yet.
    Called lazily on each WebSocket connection so we never miss a kernel.
    """
    km = settings["pubsub_km"]
    listeners: dict = settings["pubsub_listeners"]
    registry: dict = settings["pubsub_registry"]

    for kernel_id in km.list_kernel_ids():
        if kernel_id in listeners:
            task, stop = listeners[kernel_id]
            if not task.done():
                continue  # already running
        try:
            kernel = km.get_kernel(kernel_id)
            connection_info = kernel.get_connection_info()
        except Exception:
            log.warning("pubsub: could not get connection info for kernel %s", kernel_id)
            continue

        stop = asyncio.Event()
        task = asyncio.ensure_future(
            listen_iopub(kernel_id, connection_info, registry, stop)
        )
        listeners[kernel_id] = (task, stop)
        log.info("pubsub: started listener for kernel %s", kernel_id)


class PubSubWebSocketHandler(websocket.WebSocketHandler):
    """WebSocket endpoint: /pubsub/ws/<cell_name>"""

    def check_origin(self, origin: str) -> bool:
        # Open for local dev; restrict in Phase 4 with API key auth.
        return True

    async def open(self, cell_name: str) -> None:
        self.cell_name = cell_name
        self.queue: asyncio.Queue = add_subscriber(
            self.settings["pubsub_registry"], cell_name
        )
        _ensure_listeners(self.settings)
        self._send_task = asyncio.ensure_future(self._send_loop())
        log.info("pubsub: subscriber connected to cell '%s'", cell_name)

    async def _send_loop(self) -> None:
        try:
            while True:
                envelope = await self.queue.get()
                self.write_message(json.dumps(envelope))
        except websocket.WebSocketClosedError:
            pass
        except asyncio.CancelledError:
            pass

    def on_close(self) -> None:
        remove_subscriber(
            self.settings["pubsub_registry"], self.cell_name, self.queue
        )
        self._send_task.cancel()
        log.info("pubsub: subscriber disconnected from cell '%s'", self.cell_name)

    def on_message(self, message: str) -> None:
        # Clients are receive-only in Phase 1; ignore inbound messages.
        pass


class PubSubCellsHandler(web.RequestHandler):
    """REST endpoint: GET /pubsub/cells"""

    def get(self) -> None:
        registry: dict = self.settings["pubsub_registry"]
        listeners: dict = self.settings["pubsub_listeners"]
        self.set_header("Content-Type", "application/json")
        self.finish(
            json.dumps(
                {
                    "cells": list(registry.keys()),
                    "subscriber_counts": {
                        name: len(qs) for name, qs in registry.items()
                    },
                    "kernel_count": len(
                        [t for t, _ in listeners.values() if not t.done()]
                    ),
                }
            )
        )
