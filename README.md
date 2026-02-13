# Labmarket Qiskit Container

This repo describes a Docker image to load an AER Quantum Simulator and host Jupyter notebooks. Likely more features will follow.

Ultimately, this will be a node in an esemble of containers used for open-source drug discovery tools. Examples will evntually be provided.

IBM provides a similar, [containerized version of qiskit](https://github.com/christopherporter1/hpc-course-demos).

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
