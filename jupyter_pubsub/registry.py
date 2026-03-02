"""
Subscriber registry: maps cell_name -> list of asyncio.Queue instances.
Each connected WebSocket client owns one queue.

History ring buffer: maps cell_name -> deque of recent envelopes, kept
regardless of whether any WebSocket clients are currently connected.
"""

import asyncio
from collections import deque

QUEUE_MAX = 100   # per-subscriber buffer size
HISTORY_MAX = 50  # envelopes retained per cell

# Module-level ring buffer — persists for the lifetime of the server process.
_history: dict[str, deque] = {}


def add_subscriber(registry: dict, cell_name: str) -> asyncio.Queue:
    """Create a bounded queue for a new subscriber and register it."""
    q = asyncio.Queue(maxsize=QUEUE_MAX)
    registry.setdefault(cell_name, []).append(q)
    return q


def remove_subscriber(registry: dict, cell_name: str, queue: asyncio.Queue) -> None:
    """Remove a subscriber queue; clean up the cell entry if no subscribers remain."""
    subscribers = registry.get(cell_name, [])
    try:
        subscribers.remove(queue)
    except ValueError:
        pass
    if not subscribers:
        registry.pop(cell_name, None)


async def publish(registry: dict, cell_name: str, envelope: dict) -> None:
    """
    Push envelope to all subscribers for cell_name and append it to the
    history ring buffer.

    If a subscriber's queue is full, drop its oldest message first to make room
    (buffer strategy — subscriber is lagging but stays connected).
    """
    _history.setdefault(cell_name, deque(maxlen=HISTORY_MAX)).append(envelope)

    for q in list(registry.get(cell_name, [])):
        if q.full():
            try:
                q.get_nowait()  # discard oldest
            except asyncio.QueueEmpty:
                pass
        await q.put(envelope)


def get_history(cell_name: str, limit: int = HISTORY_MAX) -> list[dict]:
    """Return the last *limit* published envelopes for *cell_name*."""
    h = _history.get(cell_name, deque())
    items = list(h)
    return items[-limit:] if limit < len(items) else items


def get_known_cells() -> list[str]:
    """
    Return all cell names that have ever received a message this session,
    including cells that currently have no active subscribers.
    """
    return list(_history.keys())
