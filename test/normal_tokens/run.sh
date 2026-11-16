#!/usr/bin/env bash
set -euo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
PYTHON_BIN=${TOKENS_DIAG_PYTHON:-${VERBS_DIAG_PYTHON:-${URMA_DIAG_PYTHON:-python3}}}
exec "$PYTHON_BIN" "$HERE/runner.py" "$@"
