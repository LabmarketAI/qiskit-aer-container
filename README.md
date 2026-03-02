# Labmarket Qiskit Container

This repo describes a Docker image to load an AER Quantum Simulator and host Jupyter notebooks. Likely more features will follow.

Ultimately, this will be a node in an esemble of containers used for open-source drug discovery tools. Examples will evntually be provided.

IBM provides a similar, [containerized version of qiskit](https://github.com/christopherporter1/hpc-course-demos).

## Jupyter Client Message Bus

`jupyter_pubsub` is a Jupyter Server extension included in this repo that streams cell outputs to external WebSocket subscribers in real time. It enables use cases like pushing a NetworkX graph from a notebook directly into a Babylon.js scene, or feeding a live pandas DataFrame into a Godot-Charts widget.

### Local setup (no Docker required)

```bash
uv venv
uv pip install jupyterlab pyzmq jupyter_client networkx igraph matplotlib pandas
uv pip install -e .
source .venv/bin/activate
jupyter lab --no-browser
```

### Tagging a cell for publishing

Add a `# pubsub: <name>` comment anywhere in a cell (first line recommended):

```python
# pubsub: nx-graph
import networkx as nx, json
G = nx.karate_club_graph()
print(json.dumps(nx.node_link_data(G, edges="links")))
```

Any cell without this comment is ignored by the extension.

### Subscribing from a client

Connect a WebSocket to `/pubsub/ws/<cell_name>`. In a browser DevTools console:

```js
const ws = new WebSocket('ws://localhost:8888/pubsub/ws/nx-graph');
ws.onmessage = e => console.log(JSON.parse(e.data));
```

Each time the tagged cell executes, all connected subscribers receive a JSON envelope:

```json
{
  "cell_name": "nx-graph",
  "kernel_id": "abc-123",
  "msg_type": "stream",
  "chunk_id": 1,
  "total_chunks": 1,
  "data": "...",
  "metadata": {}
}
```

Multiple clients can subscribe to the same cell simultaneously. If a subscriber falls behind, its oldest buffered messages are dropped to keep it connected.

### Discovery endpoint

```
GET http://localhost:8888/pubsub/cells
```

Returns the currently active cell names, per-cell subscriber counts, and the number of running kernel listeners.

### Testing with `pubsub-cli`

`pubsub-cli` is an interactive shell for inspecting and watching the message bus from the terminal.

```bash
source .venv/bin/activate
pubsub-cli                        # connects to localhost:8888 by default
pubsub-cli --host 0.0.0.0 --port 8888
```

| Command | Description |
|---|---|
| `list` | Show all active cell names, per-cell subscriber counts, and kernel listener count |
| `kernels` | List running kernels and their execution state |
| `watch <cell>` | Stream live envelopes from a tagged cell — press **Enter** to stop |
| `clear` | Clear the screen |
| `help` | Show command reference |
| `quit` / `exit` | Exit |

Example session:

```
jupyter_pubsub CLI  http://localhost:8888
Type "help" for commands.

pubsub> list

  Cell                           Subscribers
  ──────────────────────────────  ───────────
  nx-graph                        1
  df-summary                      0

  Active kernel listeners: 1

pubsub> watch nx-graph

  Watching nx-graph  —  press Enter to stop

  15:42:01  nx-graph  stream
  "{\"directed\": false, \"multigraph\": false, …  (4,425 chars total)

  Stopped watching.

pubsub> quit
Bye.
```

### Example notebook

See [`workspace/pubsub_example.ipynb`](workspace/pubsub_example.ipynb) for a working demo with a NetworkX graph cell and a pandas DataFrame cell.

---

## Examples

See the notebooks in `./workspace` for usage and proof-of-life tests.

## Features

- **Qiskit Aer GPU** simulator with CUDA support
- **Jupyter Lab** accessible at `http://localhost:8888`
- **SLURM** workload manager (`slurmctld`, `slurmd`, `srun`, `sbatch`, `squeue`, `sinfo`, etc.) for job scheduling
- **HuggingFace** `transformers` and `huggingface_hub` with GPU-accelerated inference and a configurable local models directory

## Pre-built Image

A pre-built image is published to the GitHub Container Registry on every push to `main`:

```
docker pull ghcr.io/labmarketai/qiskit-aer-container:main
```

Browse available tags at [ghcr.io/labmarketai/qiskit-aer-container](https://github.com/LabmarketAI/qiskit-aer-container/pkgs/container/qiskit-aer-container).

## Usage

```
make up       # Start the container (uses cached image)
make down     # Stop and remove the container
make rebuild  # Rebuild from scratch (no cache) and start
```

### Local Models Directory

HuggingFace models are cached in a `/models` volume inside the container. By default this maps to `./models` next to the compose file. To use an existing cache on the host:

```
HF_MODELS_DIR=/path/to/my/models make up
```

Models downloaded inside the container (e.g. via `transformers.AutoModel.from_pretrained()`) will persist across restarts.

### Example: `make up`

```
$ make up
[+] Running 1/1
 ✔ Container qiskit-aer-container-qiskit-aer-1  Started

Jupyter Lab is running at:

  http://localhost:8888
```

