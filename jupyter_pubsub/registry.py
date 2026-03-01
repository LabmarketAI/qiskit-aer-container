"""
Subscriber registry: maps cell_name -> list of asyncio.Queue instances.
Each connected WebSocket client owns one queue.
"""

import asyncio

QUEUE_MAX = 100  # per-subscriber buffer size


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
    Push envelope to all subscribers for cell_name.
    If a subscriber's queue is full, drop its oldest message first to make room
    (buffer strategy — subscriber is lagging but stays connected).
    """
    for q in list(registry.get(cell_name, [])):
        if q.full():
            try:
                q.get_nowait()  # discard oldest
            except asyncio.QueueEmpty:
                pass
        await q.put(envelope)
