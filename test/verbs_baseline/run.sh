#!/usr/bin/env bash
set -euo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
export VERBS_DIAG_ENTRYPOINT="$HERE/driver.py"
exec bash "$HERE/../verbs_diag/run.sh" "$@"
