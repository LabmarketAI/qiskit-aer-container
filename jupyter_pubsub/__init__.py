"""
jupyter_pubsub — Jupyter Server Extension

Registers WebSocket and REST handlers under /pubsub/* and wires them to the
kernel manager so IOPub listeners can be started on demand.
"""

from jupyter_server.extension.application import ExtensionApp
from traitlets import Unicode

from .handlers import PubSubCellsHandler, PubSubWebSocketHandler


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
            (r"/pubsub/ws/(?P<cell_name>[\w\-]+)", PubSubWebSocketHandler),
            (r"/pubsub/cells", PubSubCellsHandler),
        ]
