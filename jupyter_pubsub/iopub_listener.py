"""
Per-kernel IOPub listener.

Subscribes to a kernel's ZMQ IOPub socket, parses Jupyter wire-protocol
messages, and routes cell outputs to registered subscribers.

Cell tagging: if the first comment in a cell source matches
    # pubsub: <name>
that cell's subsequent outputs are published under <name>.
"""

import asyncio
import json
import logging
import re

import zmq
import zmq.asyncio

from .registry import publish

log = logging.getLogger(__name__)

_PUBSUB_TAG = re.compile(r"#\s*pubsub:\s*(\S+)")
_IDS_MSG = b"<IDS|MSG>"

# Message types whose content we forward to subscribers
_OUTPUT_TYPES = {"execute_result", "display_data", "stream"}


def _parse_wire(parts: list[bytes]) -> tuple[str, str, dict] | None:
    """
    Parse a Jupyter wire-protocol multipart message.

    Wire format (after any ZMQ identity frames):
        [...identities..., b"<IDS|MSG>", hmac, header, parent_header, metadata, content, *buffers]

    Returns (msg_type, msg_id, content) or None if unparseable.
    """
    try:
        sep = parts.index(_IDS_MSG)
    except ValueError:
        return None
    try:
        header = json.loads(parts[sep + 2])
        content = json.loads(parts[sep + 5])
        return header.get("msg_type", ""), header.get("msg_id", ""), content
    except (IndexError, json.JSONDecodeError, UnicodeDecodeError):
        return None


async def listen_iopub(
    kernel_id: str,
    connection_info: dict,
    registry: dict,
    stop_event: asyncio.Event,
) -> None:
    """
    Async task: subscribe to one kernel's IOPub socket and route outputs.

    Runs until stop_event is set (e.g., on kernel shutdown or server exit).
    """
    transport = connection_info.get("transport", "tcp")
    ip = connection_info.get("ip", "127.0.0.1")
    port = connection_info["iopub_port"]
    url = f"{transport}://{ip}:{port}"

    ctx = zmq.asyncio.Context.instance()
    sock = ctx.socket(zmq.SUB)
    sock.setsockopt(zmq.SUBSCRIBE, b"")
    sock.connect(url)
    log.info("pubsub: IOPub listener started for kernel %s at %s", kernel_id, url)

    current_cell_name: str | None = None

    try:
        while not stop_event.is_set():
            try:
                parts = await asyncio.wait_for(sock.recv_multipart(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            parsed = _parse_wire(parts)
            if parsed is None:
                continue
            msg_type, msg_id, content = parsed

            if msg_type == "execute_input":
                code = content.get("code", "")
                match = _PUBSUB_TAG.search(code)
                current_cell_name = match.group(1) if match else None

            elif msg_type in _OUTPUT_TYPES and current_cell_name:
                if msg_type == "stream":
                    data = content.get("text")
                else:
                    data = content.get("data")

                envelope = {
                    "cell_name": current_cell_name,
                    "kernel_id": kernel_id,
                    "msg_id": msg_id,
                    "msg_type": msg_type,
                    "chunk_id": 1,
                    "total_chunks": 1,
                    "data": data,
                    "metadata": content.get("metadata", {}),
                }
                await publish(registry, current_cell_name, envelope)

    finally:
        sock.close()
        log.info("pubsub: IOPub listener stopped for kernel %s", kernel_id)
