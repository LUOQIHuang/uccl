#!/usr/bin/env bash
# Sequential control and API-boundary diagnostics on both nodes.
set -euo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
JOB=
CHECK=0
ARGS=()
while (($#)); do
  case "$1" in
    --job) [[ $# -ge 2 ]] || { echo 'Missing --job value' >&2; exit 2; }; JOB=$2; shift 2 ;;
    --check-only) CHECK=1; shift ;;
    *) ARGS+=("$1"); shift ;;
  esac
done
[[ "$JOB" =~ ^[a-zA-Z0-9_-]+$ ]] || { echo 'Provide a unique --job name' >&2; exit 2; }
COMMON=("${ARGS[@]}" --suite boundaries --affinity-mode unpinned --repeats 1)
if ((CHECK)); then
  exec bash "$HERE/run.sh" "${COMMON[@]}" --job "${JOB}_check" --trace-transport --check-only
fi
bash "$HERE/run.sh" "${COMMON[@]}" --job "${JOB}_control"
bash "$HERE/run.sh" "${COMMON[@]}" --job "${JOB}_trace" --trace-transport
