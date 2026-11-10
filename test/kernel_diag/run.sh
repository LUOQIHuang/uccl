#!/usr/bin/env bash
set -euo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
PY=${KERNEL_DIAG_PYTHON:-/usr/bin/python3}
# Use the system standard library only; do not import/initialize GPU runtimes.
exec env -u LD_PRELOAD -u PYTHONPATH -u LD_LIBRARY_PATH "$PY" -S "$HERE/collect.py" "$@"
