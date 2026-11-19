#!/bin/sh
# usage: MESH_ENGINE=/path/to/engine sh p.sh run MODEL VARIANT STEPS [LABEL]
HERE="$(cd "$(dirname "$0")/.." && pwd)"
PROBE_OUT="$HERE" exec "$MESH_ENGINE/.venv/bin/python" "$HERE/scripts/probe.py" "$@"
