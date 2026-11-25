#!/usr/bin/env bash
set -euo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
export URMA_DIAG_HOME=${URMA_DIAG_HOME:-/home/h00918771}
export VERBS_DIAG_HOME="$URMA_DIAG_HOME"
export URMA_DIAG_PYTHON=${URMA_DIAG_PYTHON:-"$URMA_DIAG_HOME/metax_dev/venv-maca-py312/bin/python"}
export VERBS_DIAG_PYTHON="$URMA_DIAG_PYTHON"
export MACA_PATH=${MACA_PATH:-/opt/maca-3.9.0}
export URMA_DIAG_LIBRARY_DIR=${URMA_DIAG_LIBRARY_DIR:-"$URMA_DIAG_HOME/metax_dev/urma_after_kernel_20261029/diag/umdk_private_618957c3/lib"}
export VERBS_DIAG_LIBRARY_DIR="$URMA_DIAG_LIBRARY_DIR"
export URMA_DIAG_INCLUDE_DIR=${URMA_DIAG_INCLUDE_DIR:-"$URMA_DIAG_HOME/metax_dev/urma_sdk_private_include"}
exec "$URMA_DIAG_PYTHON" "$HERE/runner.py" "$@"
