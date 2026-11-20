#!/usr/bin/env bash
set -euo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
PYTHON_BIN=${URMA_DIAG_PYTHON:-${VERBS_DIAG_PYTHON:-python3}}
exec "$PYTHON_BIN" "$HERE/details.py" "$@"
