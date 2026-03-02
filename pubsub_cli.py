#!/usr/bin/env python3
"""
pubsub_cli.py — Interactive CLI for the jupyter_pubsub WebSocket extension.

Usage:
    python pubsub_cli.py [--host localhost] [--port 8888]

Commands:
    list              List active cells and per-cell subscriber counts
    kernels           List running kernels reported by the extension
    watch <cell>      Stream output from a tagged cell (press Enter to stop)
    clear             Clear the screen
    help              Show this help
    quit / exit       Exit
"""

import argparse
import asyncio
import json
import os
import sys
import urllib.request
from datetime import datetime

try:
    import websockets
except ImportError:
    print("websockets not installed. Run: uv pip install websockets")
    sys.exit(1)

# ── ANSI helpers ──────────────────────────────────────────────────────────────

RESET  = "\033[0m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
GREEN  = "\033[32m"
CYAN   = "\033[36m"
YELLOW = "\033[33m"
RED    = "\033[31m"

def _c(text, *codes):
    return "".join(codes) + str(text) + RESET


# ── HTTP helpers ──────────────────────────────────────────────────────────────

def _get(base_url: str, path: str):
    req = urllib.request.Request(f"{base_url}{path}")
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read())


# ── Data formatting ───────────────────────────────────────────────────────────

def _fmt_data(data, max_width: int = 100) -> str:
    if data is None:
        return _c("<no data>", DIM)
    if isinstance(data, str):
        preview = data.strip()[:max_width].replace("\n", "\\n")
        suffix = _c(f"… ({len(data):,} chars total)", DIM) if len(data) > max_width else f"  {_c(f'({len(data):,} chars)', DIM)}"
        return f'"{preview}"{suffix}'
    if isinstance(data, dict):
        raw = json.dumps(data)
        keys = list(data.keys())
        return f"dict  keys={keys[:6]}{'…' if len(keys)>6 else ''}  {_c(f'({len(raw):,} chars)', DIM)}"
    return repr(data)[:max_width]


def _fmt_envelope(env: dict) -> str:
    ts      = datetime.now().strftime("%H:%M:%S")
    cell    = _c(env.get("cell_name", "?"), BOLD, CYAN)
    mtype   = _c(env.get("msg_type", "?"), YELLOW)
    chunk   = env.get("chunk_id", 1)
    total   = env.get("total_chunks", 1)
    chunk_s = f"  {_c(f'chunk {chunk}/{total}', DIM)}" if total > 1 else ""
    data_s  = _fmt_data(env.get("data"))
    return f"  {_c(ts, DIM)}  {cell}  {mtype}{chunk_s}\n  {data_s}"


# ── Watch loop ────────────────────────────────────────────────────────────────

async def _watch_loop(ws_base: str, cell_name: str) -> None:
    url = f"{ws_base}/pubsub/ws/{cell_name}"
    try:
        async with websockets.connect(url, open_timeout=5) as ws:
            while True:
                try:
                    raw  = await asyncio.wait_for(ws.recv(), timeout=1.0)
                    env  = json.loads(raw)
                    print(_fmt_envelope(env), flush=True)
                except asyncio.TimeoutError:
                    continue
    except websockets.ConnectionClosed:
        print(_c("  Connection closed by server.", RED))
    except OSError as e:
        print(_c(f"  Connection error: {e}", RED))


# ── CLI ───────────────────────────────────────────────────────────────────────

HELP_TEXT = f"""
{_c('Commands', BOLD)}
  {_c('list', CYAN)}              List active cells and subscriber counts
  {_c('kernels', CYAN)}           List running kernel listeners
  {_c('watch <cell>', CYAN)}      Stream output from a tagged cell  (press Enter to stop)
  {_c('clear', CYAN)}             Clear the screen
  {_c('help', CYAN)}              Show this message
  {_c('quit', CYAN)} / {_c('exit', CYAN)}       Exit
"""


class PubSubCLI:
    def __init__(self, host: str, port: int):
        self.base_url = f"http://{host}:{port}"
        self.ws_base  = f"ws://{host}:{port}"

    # ── Commands ──────────────────────────────────────────────────────────────

    async def do_list(self) -> None:
        try:
            data = _get(self.base_url, "/pubsub/cells")
        except Exception as e:
            print(_c(f"  Error reaching server: {e}", RED))
            return

        cells   = data.get("cells", [])
        counts  = data.get("subscriber_counts", {})
        kcount  = data.get("kernel_count", 0)

        if cells:
            print(f"\n  {_c('Cell', BOLD):<40} {_c('Subscribers', BOLD)}")
            print(f"  {'─'*38}  {'─'*11}")
            for cell in cells:
                print(f"  {_c(cell, CYAN):<40} {counts.get(cell, 0)}")
        else:
            print(_c("  No active cells — run a tagged cell in the notebook first.", DIM))

        print(f"\n  {_c('Active kernel listeners:', DIM)} {kcount}\n")

    async def do_kernels(self) -> None:
        try:
            kernels = _get(self.base_url, "/api/kernels")
        except Exception as e:
            print(_c(f"  Error: {e}", RED))
            return

        if not kernels:
            print(_c("  No running kernels.", DIM))
            return

        print(f"\n  {_c('ID', BOLD):<12} {_c('Name', BOLD):<22} {_c('State', BOLD)}")
        print(f"  {'─'*10}  {'─'*20}  {'─'*10}")
        for k in kernels:
            state_color = GREEN if k.get("execution_state") == "idle" else YELLOW
            print(f"  {k['id'][:8]}…   {k['name']:<22} {_c(k.get('execution_state',''), state_color)}")
        print()

    async def do_watch(self, cell_name: str) -> None:
        print(f"\n  Watching {_c(cell_name, BOLD, CYAN)}  —  press {_c('Enter', BOLD)} to stop\n")

        watch_task = asyncio.ensure_future(_watch_loop(self.ws_base, cell_name))

        # Block until the user hits Enter, then cancel the watcher.
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, sys.stdin.readline)

        watch_task.cancel()
        try:
            await watch_task
        except asyncio.CancelledError:
            pass

        print(_c("  Stopped watching.\n", DIM))

    # ── REPL ──────────────────────────────────────────────────────────────────

    async def run(self) -> None:
        print(f"\n{_c('jupyter_pubsub CLI', BOLD)}  {_c(self.base_url, DIM)}")
        print(_c('Type "help" for commands.\n', DIM))

        loop = asyncio.get_event_loop()

        while True:
            try:
                prompt = _c("pubsub", CYAN) + "> "
                line = await loop.run_in_executor(
                    None, lambda: input(prompt).strip()
                )
            except (EOFError, KeyboardInterrupt):
                print("\nBye.")
                break

            if not line:
                continue

            parts = line.split()
            cmd   = parts[0].lower()

            if cmd in ("quit", "exit"):
                print("Bye.")
                break
            elif cmd == "help":
                print(HELP_TEXT)
            elif cmd == "list":
                await self.do_list()
            elif cmd == "kernels":
                await self.do_kernels()
            elif cmd == "watch":
                if len(parts) < 2:
                    print(_c("  Usage: watch <cell_name>", YELLOW))
                else:
                    await self.do_watch(parts[1])
            elif cmd == "clear":
                os.system("clear")
            else:
                print(_c(f'  Unknown command: "{cmd}".  Type "help" for commands.', DIM))


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Interactive CLI for the jupyter_pubsub extension."
    )
    parser.add_argument("--host", default="localhost",
                        help="Jupyter server host (default: localhost)")
    parser.add_argument("--port", default=8888, type=int,
                        help="Jupyter server port (default: 8888)")
    args = parser.parse_args()

    try:
        asyncio.run(PubSubCLI(args.host, args.port).run())
    except KeyboardInterrupt:
        print("\nBye.")


if __name__ == "__main__":
    main()
