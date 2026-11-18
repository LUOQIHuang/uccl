#!/usr/bin/env bash
set -euo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
export VERBS_DIAG_HOME=${URMA_DIAG_HOME:-${VERBS_DIAG_HOME:-${HOME:?}}}
LOAD_PYTHON=${URMA_PAIR_LOAD_PYTHON:-${URMA_DIAG_PYTHON:-${VERBS_DIAG_PYTHON:-}}}
if [ -n "$LOAD_PYTHON" ]; then export VERBS_DIAG_PYTHON="$LOAD_PYTHON"; fi
export VERBS_DIAG_ENTRYPOINT="$HERE/runner.py"
exec bash "$HERE/../verbs_diag/run.sh" "$@"
