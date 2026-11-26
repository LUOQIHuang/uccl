#!/usr/bin/env bash
# Use the same MetaX Python and library environment as normal_completion.
set -euo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
export VERBS_DIAG_HOME=${URMA_DIAG_HOME:-/home/h00918771}
export VERBS_DIAG_PYTHON=${URMA_DIAG_PYTHON:-"$VERBS_DIAG_HOME/metax_dev/venv-maca-py312/bin/python"}
export VERBS_DIAG_LIBRARY_DIR=${URMA_DIAG_LIBRARY_DIR:-"$VERBS_DIAG_HOME/metax_dev/urma_after_kernel_20261029/diag/umdk_private_618957c3/lib"}
export MACA_PATH=${MACA_PATH:-/opt/maca-3.9.0}
export VERBS_DIAG_ENTRYPOINT="$HERE/check_blockers_gpu.py"
export CUDA_VISIBLE_DEVICES=0 PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
unset LD_PRELOAD PYTHONPATH
exec bash "$HERE/../verbs_diag/run.sh" "$@"
