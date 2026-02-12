# Labmarket Qiskit Container

This repo describes a Docker image to load an QER Simulator and host Jupyter notebooks. Likely more features will follow.

Ultimately, this will be a node in an esemble of containers used for open-source drug discovery tools. Examples will evntually be provided.

## Usage

```
make up       # Start the container (uses cached image)
make down     # Stop and remove the container
make rebuild  # Rebuild from scratch (no cache) and start
```

### Example: `make up`

```
$ make up
[+] Running 1/1
 ✔ Container qiskit-aer-container-qiskit-aer-1  Started

Jupyter Lab is running at:

  http://localhost:8888
```
