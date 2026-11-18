#!/usr/bin/env bash
set -euo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
export VERBS_DIAG_HOME=${URMA_DIAG_HOME:-${VERBS_DIAG_HOME:-${HOME:?}}}
SERIAL_PYTHON_BIN=${SERIAL_PAIR_PYTHON:-${URMA_DIAG_PYTHON:-${VERBS_DIAG_PYTHON:-}}}
if [ -n "$SERIAL_PYTHON_BIN" ]; then export VERBS_DIAG_PYTHON="$SERIAL_PYTHON_BIN"; fi
export VERBS_DIAG_ENTRYPOINT="$HERE/runner.py"
exec bash "$HERE/../verbs_diag/run.sh" "$@"
