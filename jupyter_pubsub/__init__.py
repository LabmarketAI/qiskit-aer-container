"""
jupyter_pubsub — Jupyter Server Extension

Registers WebSocket and REST handlers under /pubsub/* and wires them to the
kernel manager so IOPub listeners can be started on demand.

Phase 1 endpoints
─────────────────
  WS   /pubsub/ws/<cell_name>       — WebSocket; streams cell outputs
  GET  /pubsub/cells                — list active cell names + subscriber counts

Phase 2 endpoints
─────────────────
  GET  /pubsub/cells/<cell_name>    — per-cell detail (subscribers, history size)
  GET  /pubsub/kernels              — running kernel listeners
  GET  /pubsub/history/<cell_name>  — recent message ring buffer (?limit=N)
  GET  /pubsub/mcp                  — MCP server manifest
  POST /pubsub/mcp                  — MCP JSON-RPC dispatcher
"""

from jupyter_server.extension.application import ExtensionApp
from traitlets import Unicode

from .handlers import (
    PubSubCellDetailHandler,
    PubSubCellsHandler,
    PubSubHistoryHandler,
    PubSubKernelsHandler,
    PubSubMCPHandler,
    PubSubWebSocketHandler,
)


def _jupyter_server_extension_points():
    return [{"module": "jupyter_pubsub", "app": PubSubExtension}]


class PubSubExtension(ExtensionApp):
    name = "jupyter_pubsub"
    extension_url = "/pubsub"
    load_other_extensions = True

    api_key = Unicode(
        "",
        config=True,
        help="API key required by external WebSocket clients (Phase 4). "
        "Empty string disables auth (development mode).",
    )

    def initialize_settings(self) -> None:
        self.settings["pubsub_registry"] = {}   # cell_name -> [asyncio.Queue]
        self.settings["pubsub_listeners"] = {}  # kernel_id -> (task, stop_event)
        self.settings["pubsub_km"] = self.serverapp.kernel_manager
        self.settings["pubsub_api_key"] = self.api_key

    def initialize_handlers(self) -> None:
        self.handlers = [
            # Phase 1
            (r"/pubsub/ws/(?P<cell_name>[\w\-]+)", PubSubWebSocketHandler),
            (r"/pubsub/cells", PubSubCellsHandler),
            # Phase 2 — REST
            (r"/pubsub/cells/(?P<cell_name>[\w\-]+)", PubSubCellDetailHandler),
            (r"/pubsub/kernels", PubSubKernelsHandler),
            (r"/pubsub/history/(?P<cell_name>[\w\-]+)", PubSubHistoryHandler),
            # Phase 2 — MCP
            (r"/pubsub/mcp", PubSubMCPHandler),
        ]
