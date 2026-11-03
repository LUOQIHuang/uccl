#!/usr/bin/env bash
set -euo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
export VERBS_DIAG_HOME=${URMA_DIAG_HOME:-${VERBS_DIAG_HOME:-${HOME:?HOME is required}}}
export VERBS_DIAG_DEV_ROOT=${URMA_DIAG_DEV_ROOT:-${VERBS_DIAG_DEV_ROOT:-"$VERBS_DIAG_HOME/metax_dev"}}
if [ -n "${URMA_DIAG_PYTHON:-}" ]; then export VERBS_DIAG_PYTHON="$URMA_DIAG_PYTHON"; fi
if [ -n "${URMA_DIAG_LIBRARY_DIR:-}" ]; then export VERBS_DIAG_LIBRARY_DIR="$URMA_DIAG_LIBRARY_DIR"; fi
if [ -n "${URMA_DIAG_EXTRA_LIBRARY_DIRS:-}" ]; then export VERBS_DIAG_EXTRA_LIBRARY_DIRS="$URMA_DIAG_EXTRA_LIBRARY_DIRS"; fi
exec bash "$HERE/../verbs_diag/run.sh" "$@" --backend urma
