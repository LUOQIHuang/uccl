#!/usr/bin/env bash
set -euo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
BACKEND=urma RANK= BASE= TRACE= JOB= MASTER=141.61.17.202 PORT=32880 GID=3 PROBES=full
ROOT="${URMA_DIAG_HOME:-${VERBS_DIAG_HOME:-${HOME:?}}}/metax_dev/dispatch_diag_results"
DEST="$HERE/results"
while [ "$#" -gt 0 ];do
 case "$1" in
 --backend) BACKEND=${2:?};shift 2;; --node-rank) RANK=${2:?};shift 2;;
 --base-build) BASE=${2:?};shift 2;; --trace-build) TRACE=${2:?};shift 2;;
 --job) JOB=${2:?};shift 2;; --master-addr) MASTER=${2:?};shift 2;;
 --output) ROOT=${2:?};shift 2;; --destination) DEST=${2:?};shift 2;;
 --port) PORT=${2:?};shift 2;; --gid-index) GID=${2:?};shift 2;;
 --probe-set) PROBES=${2:?};shift 2;;
 *) echo "unknown compiled-off argument: $1" >&2;exit 2;;
 esac
done
[[ "$BACKEND" == urma || "$BACKEND" == verbs ]] && [[ "$RANK" == 0 || "$RANK" == 1 ]] && [[ "$JOB" =~ ^[a-zA-Z0-9_-]+$ ]] && [ -n "$BASE" ] && [ -n "$TRACE" ] || { echo 'backend/rank/job/base-build/trace-build required' >&2;exit 2; }
PY=${VERBS_DIAG_PYTHON:-}
if [ "$BACKEND" = urma ];then PY=${URMA_DIAG_PYTHON:-$PY};fi
if [ -z "$PY" ];then
 PY="${URMA_DIAG_HOME:-${VERBS_DIAG_HOME:-${HOME:?}}}/metax_dev/venv-maca-py312/bin/python"
 [ -x "$PY" ] || PY=$(command -v python3)
fi
COMMON=(--backend "$BACKEND" --node-rank "$RANK" --master-addr "$MASTER" --tokens 4096 --output "$ROOT" --port "$PORT" --gid-index "$GID" --probe-set "$PROBES")
# Check every target before starting the first GPU case. Reuse the exact two
# modules from the preceding campaign; this script never builds a module.
for SUFFIX in base off post;do
 [ ! -e "$ROOT/${JOB}_${SUFFIX}/node$RANK" ] || { echo 'result exists; use a new job' >&2;exit 2; }
done
bash "$HERE/run.sh" "${COMMON[@]}" --action check --step baseline --build-dir "$BASE" --job "${JOB}_base"
bash "$HERE/run.sh" "${COMMON[@]}" --action check --step compiled-off --build-dir "$TRACE" --job "${JOB}_off"
for SUFFIX in base off post;do
 STEP=baseline BUILD=$BASE
 if [ "$SUFFIX" = off ];then STEP=compiled-off BUILD=$TRACE;fi
 if [ "$SUFFIX" = post ];then STEP=post;fi
 RC=0
 bash "$HERE/run.sh" "${COMMON[@]}" --action run --step "$STEP" --build-dir "$BUILD" --job "${JOB}_${SUFFIX}" || RC=$?
 "$PY" "$HERE/../gpu_stage_recovery/collect.py" "$ROOT/${JOB}_${SUFFIX}" --node-rank "$RANK" --base-build "$BASE" --trace-build "$TRACE" --destination "$DEST/${JOB}_${SUFFIX}"
 [ "$RC" -eq 0 ] || exit "$RC"
done
echo "DISPATCH_COMPILED_OFF_ALL_PASSED node=$RANK results=$DEST job=$JOB"
