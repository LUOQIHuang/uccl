#!/usr/bin/env bash
set -euo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
JOB= RANK= BUILD= TRACE_BUILD= ROOT= MASTER=141.61.17.202 PORT=32280 GID=3
while [ "$#" -gt 0 ];do
 case "$1" in
 --job) JOB=${2:?};shift 2;; --node-rank) RANK=${2:?};shift 2;;
 --build-dir) BUILD=${2:?};shift 2;; --trace-build-dir) TRACE_BUILD=${2:?};shift 2;;
 --result-root) ROOT=${2:?};shift 2;; --master-addr) MASTER=${2:?};shift 2;;
 --port) PORT=${2:?};shift 2;; --gid-index) GID=${2:?};shift 2;;
 *) echo "unknown verbs stage suite argument: $1" >&2;exit 2;;
 esac
done
[[ "$JOB" =~ ^[a-zA-Z0-9_-]+$ ]] && [[ "$RANK" == 0 || "$RANK" == 1 ]] || { echo 'valid job/rank required' >&2;exit 2; }
[ -n "$BUILD" ] && [ -n "$TRACE_BUILD" ] || { echo 'both base and trace builds required' >&2;exit 2; }
TASK_ROOT=${VERBS_DIAG_HOME:-${HOME:?}}
ROOT=${ROOT:-"$TASK_ROOT/metax_dev/verbs_stage_results/$JOB"}
CONSOLE="$TASK_ROOT/metax_dev/verbs_stage_console/$JOB";mkdir -p "$CONSOLE"
COMMON=(--variant current --mode normal --no-profile --repeats 1 --stage-suite --node-rank "$RANK" --master-addr "$MASTER" --build-dir "$BUILD" --output "$ROOT" --port "$PORT" --gid-index "$GID" --notify-check)
one(){ local tag=$1;shift;bash "$HERE/run.sh" --job "${JOB}_${tag}" "${COMMON[@]}" "$@" 2>&1 | tee "$CONSOLE/${tag}_node${RANK}.log"; }
for repeat in 1 2 3;do
 one "baseline_r0${repeat}" --action run --warmups 50 --samples 50
done
one diagnostic --build-dir "$TRACE_BUILD" --action run --cpu-diagnostics --timeline --gpu-stages --warmups 5 --samples 5
echo "VERBS_STAGE_SUITE_ALL_PASSED rank=$RANK output=$ROOT"
