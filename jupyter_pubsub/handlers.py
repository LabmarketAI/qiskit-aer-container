"""
Tornado handlers for jupyter_pubsub.

Phase 1 endpoints
─────────────────
  WS   /pubsub/ws/<cell_name>       — WebSocket; streams cell outputs
  GET  /pubsub/cells                — list active cell names + subscriber counts

Phase 2 endpoints
─────────────────
  GET  /pubsub/cells/<cell_name>    — per-cell detail (subscribers, history size)
  GET  /pubsub/kernels              — running kernel listeners
  GET  /pubsub/history/<cell_name>  — recent message ring buffer (?limit=N)
  GET  /pubsub/mcp                  — MCP server manifest (JSON)
  POST /pubsub/mcp                  — MCP JSON-RPC (tools/list, tools/call,
                                       resources/list, resources/read)
"""

import asyncio
import json
import logging

from tornado import websocket, web

from .iopub_listener import listen_iopub
from .registry import (
    add_subscriber,
    get_history,
    get_known_cells,
    remove_subscriber,
)

log = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

_MCP_VERSION = "2024-11-05"
_EXT_VERSION = "0.2.0"


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


def _json_response(handler: web.RequestHandler, data: dict, status: int = 200) -> None:
    handler.set_status(status)
    handler.set_header("Content-Type", "application/json")
    handler.set_header("Access-Control-Allow-Origin", "*")
    handler.finish(json.dumps(data))


# ──────────────────────────────────────────────────────────────────────────────
# Phase 1 handlers
# ──────────────────────────────────────────────────────────────────────────────

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
                try:
                    envelope = await asyncio.wait_for(self.queue.get(), timeout=2.0)
                    self.write_message(json.dumps(envelope))
                except asyncio.TimeoutError:
                    # Re-check for kernels that started after this client connected.
                    _ensure_listeners(self.settings)
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
    """REST endpoint: GET /pubsub/cells

    Returns all cells that have received at least one message this session
    (union of active subscribers and history), plus subscriber counts.
    """

    def get(self) -> None:
        registry: dict = self.settings["pubsub_registry"]
        listeners: dict = self.settings["pubsub_listeners"]

        # Include cells with active subscribers AND cells only in history.
        all_cells = list(set(list(registry.keys()) + get_known_cells()))

        _json_response(
            self,
            {
                "cells": all_cells,
                "subscriber_counts": {
                    name: len(registry.get(name, [])) for name in all_cells
                },
                "kernel_count": len(
                    [t for t, _ in listeners.values() if not t.done()]
                ),
            },
        )


# ──────────────────────────────────────────────────────────────────────────────
# Phase 2 REST handlers
# ──────────────────────────────────────────────────────────────────────────────

class PubSubCellDetailHandler(web.RequestHandler):
    """REST endpoint: GET /pubsub/cells/<cell_name>"""

    def get(self, cell_name: str) -> None:
        registry: dict = self.settings["pubsub_registry"]
        history = get_history(cell_name)

        if not history and cell_name not in registry:
            _json_response(self, {"error": f"unknown cell '{cell_name}'"}, 404)
            return

        last = history[-1] if history else None
        _json_response(
            self,
            {
                "cell_name": cell_name,
                "subscriber_count": len(registry.get(cell_name, [])),
                "history_size": len(history),
                "last_msg_type": last["msg_type"] if last else None,
                "last_kernel_id": last["kernel_id"] if last else None,
                "ws_url": f"/pubsub/ws/{cell_name}",
            },
        )


class PubSubKernelsHandler(web.RequestHandler):
    """REST endpoint: GET /pubsub/kernels"""

    def get(self) -> None:
        listeners: dict = self.settings["pubsub_listeners"]
        km = self.settings["pubsub_km"]

        kernels = []
        for kernel_id, (task, _) in listeners.items():
            active = not task.done()
            try:
                kernel = km.get_kernel(kernel_id)
                kernel_name = kernel.kernel_name
            except Exception:
                kernel_name = "unknown"
            kernels.append(
                {
                    "kernel_id": kernel_id,
                    "kernel_name": kernel_name,
                    "listener_active": active,
                }
            )

        _json_response(self, {"kernels": kernels})


class PubSubHistoryHandler(web.RequestHandler):
    """REST endpoint: GET /pubsub/history/<cell_name>?limit=N"""

    def get(self, cell_name: str) -> None:
        try:
            limit = int(self.get_argument("limit", "20"))
            limit = max(1, min(limit, 50))
        except ValueError:
            _json_response(self, {"error": "limit must be an integer"}, 400)
            return

        history = get_history(cell_name, limit)
        _json_response(
            self,
            {
                "cell_name": cell_name,
                "count": len(history),
                "messages": history,
            },
        )


# ──────────────────────────────────────────────────────────────────────────────
# Phase 2 MCP handler
# ──────────────────────────────────────────────────────────────────────────────

class PubSubMCPHandler(web.RequestHandler):
    """
    Model Context Protocol discovery server.

      GET  /pubsub/mcp  — server manifest (capabilities)
      POST /pubsub/mcp  — JSON-RPC 2.0 dispatcher

    Supported JSON-RPC methods
    ──────────────────────────
      initialize          — MCP handshake; returns server info + capabilities
      tools/list          — enumerate available tools
      tools/call          — invoke a tool by name
      resources/list      — list available resources (one per known cell)
      resources/read      — read a resource (returns cell history)
    """

    # ── GET — human/machine-readable manifest ──────────────────────────────

    def get(self) -> None:
        _json_response(
            self,
            {
                "name": "jupyter-pubsub",
                "version": _EXT_VERSION,
                "protocol_version": _MCP_VERSION,
                "description": (
                    "Real-time Jupyter notebook cell-output streaming via "
                    "WebSocket pub/sub. Exposes tagged cell outputs to external "
                    "clients (AI agents, visualisation tools, dashboards)."
                ),
                "capabilities": {
                    "tools": True,
                    "resources": True,
                },
                "endpoints": {
                    "mcp_rpc": "/pubsub/mcp",
                    "cells": "/pubsub/cells",
                    "kernels": "/pubsub/kernels",
                    "history": "/pubsub/history/<cell_name>",
                    "websocket": "/pubsub/ws/<cell_name>",
                },
            },
        )

    # ── POST — JSON-RPC 2.0 dispatcher ────────────────────────────────────

    def post(self) -> None:
        try:
            body = json.loads(self.request.body)
        except json.JSONDecodeError:
            _json_response(
                self,
                {"jsonrpc": "2.0", "error": {"code": -32700, "message": "Parse error"}, "id": None},
                400,
            )
            return

        rpc_id = body.get("id")
        method = body.get("method", "")
        params = body.get("params", {})

        handler_fn = {
            "initialize": self._rpc_initialize,
            "tools/list": self._rpc_tools_list,
            "tools/call": self._rpc_tools_call,
            "resources/list": self._rpc_resources_list,
            "resources/read": self._rpc_resources_read,
        }.get(method)

        if handler_fn is None:
            _json_response(
                self,
                {
                    "jsonrpc": "2.0",
                    "error": {"code": -32601, "message": f"Method not found: {method}"},
                    "id": rpc_id,
                },
                404,
            )
            return

        result = handler_fn(params)
        _json_response(self, {"jsonrpc": "2.0", "result": result, "id": rpc_id})

    # ── RPC method implementations ─────────────────────────────────────────

    def _rpc_initialize(self, params: dict) -> dict:
        return {
            "protocolVersion": _MCP_VERSION,
            "serverInfo": {"name": "jupyter-pubsub", "version": _EXT_VERSION},
            "capabilities": {"tools": {}, "resources": {}},
        }

    def _rpc_tools_list(self, params: dict) -> dict:
        return {
            "tools": [
                {
                    "name": "list_cells",
                    "description": (
                        "List all pub/sub cells that have received output this session, "
                        "along with their active subscriber counts."
                    ),
                    "inputSchema": {"type": "object", "properties": {}, "required": []},
                },
                {
                    "name": "get_cell_info",
                    "description": "Get details for a specific pub/sub cell.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "cell_name": {
                                "type": "string",
                                "description": "The tagged cell name (e.g. 'nx-graph').",
                            }
                        },
                        "required": ["cell_name"],
                    },
                },
                {
                    "name": "get_history",
                    "description": (
                        "Return the last N messages published by a tagged cell "
                        "(max 50)."
                    ),
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "cell_name": {"type": "string"},
                            "limit": {
                                "type": "integer",
                                "default": 10,
                                "minimum": 1,
                                "maximum": 50,
                            },
                        },
                        "required": ["cell_name"],
                    },
                },
                {
                    "name": "get_ws_url",
                    "description": (
                        "Return the WebSocket URL to subscribe to a cell's live output."
                    ),
                    "inputSchema": {
                        "type": "object",
                        "properties": {"cell_name": {"type": "string"}},
                        "required": ["cell_name"],
                    },
                },
            ]
        }

    def _rpc_tools_call(self, params: dict) -> dict:
        name = params.get("name", "")
        args = params.get("arguments", {})

        if name == "list_cells":
            registry: dict = self.settings["pubsub_registry"]
            all_cells = list(set(list(registry.keys()) + get_known_cells()))
            content = {
                "cells": all_cells,
                "subscriber_counts": {
                    c: len(registry.get(c, [])) for c in all_cells
                },
            }

        elif name == "get_cell_info":
            cell_name = args.get("cell_name", "")
            registry: dict = self.settings["pubsub_registry"]
            history = get_history(cell_name)
            last = history[-1] if history else None
            content = {
                "cell_name": cell_name,
                "subscriber_count": len(registry.get(cell_name, [])),
                "history_size": len(history),
                "last_msg_type": last["msg_type"] if last else None,
                "ws_url": f"/pubsub/ws/{cell_name}",
            }

        elif name == "get_history":
            cell_name = args.get("cell_name", "")
            limit = int(args.get("limit", 10))
            history = get_history(cell_name, limit)
            content = {"cell_name": cell_name, "messages": history}

        elif name == "get_ws_url":
            cell_name = args.get("cell_name", "")
            content = {
                "cell_name": cell_name,
                "ws_url": f"/pubsub/ws/{cell_name}",
            }

        else:
            return {
                "isError": True,
                "content": [{"type": "text", "text": f"Unknown tool: {name}"}],
            }

        return {
            "content": [{"type": "text", "text": json.dumps(content)}],
        }

    def _rpc_resources_list(self, params: dict) -> dict:
        cells = get_known_cells()
        return {
            "resources": [
                {
                    "uri": f"pubsub://cell/{cell}",
                    "name": cell,
                    "description": f"Live output stream for cell '{cell}'",
                    "mimeType": "application/json",
                }
                for cell in cells
            ]
        }

    def _rpc_resources_read(self, params: dict) -> dict:
        uri = params.get("uri", "")
        # Expect uri like "pubsub://cell/<cell_name>"
        cell_name = uri.removeprefix("pubsub://cell/")
        history = get_history(cell_name, limit=20)
        return {
            "contents": [
                {
                    "uri": uri,
                    "mimeType": "application/json",
                    "text": json.dumps(
                        {"cell_name": cell_name, "messages": history}
                    ),
                }
            ]
        }
