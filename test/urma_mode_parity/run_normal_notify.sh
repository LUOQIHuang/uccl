#!/usr/bin/env bash
set -euo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
JOB= RANK= BUILD= LAYOUT= ROOT= CPUS=auto MASTER=141.61.17.202 PORT=31980
while [ "$#" -gt 0 ];do
 case "$1" in
 --job) JOB=${2:?};shift 2;; --node-rank) RANK=${2:?};shift 2;; --build-dir) BUILD=${2:?};shift 2;;
 --layout-file) LAYOUT=${2:?};shift 2;; --result-root) ROOT=${2:?};shift 2;;
 --micro-cpus) CPUS=${2:?};shift 2;; --master-addr) MASTER=${2:?};shift 2;; --port) PORT=${2:?};shift 2;;
 *) echo "unknown URMA notification suite argument: $1" >&2;exit 2;;
 esac
done
[[ "$JOB" =~ ^[a-zA-Z0-9_-]+$ ]] && [[ "$RANK" == 0 || "$RANK" == 1 ]] || { echo 'valid job/rank required' >&2;exit 2; }
[ -n "$BUILD" ] && [ -f "$LAYOUT" ] || { echo 'new build and existing layout required' >&2;exit 2; }
TASK_ROOT=${URMA_DIAG_HOME:-${HOME:?}}
ROOT=${ROOT:-"$TASK_ROOT/metax_dev/urma_notify_results/$JOB"}
CONSOLE="$TASK_ROOT/metax_dev/urma_notify_console/$JOB";mkdir -p "$CONSOLE"
COMMON=(--backend urma --mode normal --node-rank "$RANK" --master-addr "$MASTER" --build-dir "$BUILD" --output "$ROOT" --port "$PORT" --normal-completion all --ll-write-order default --notification-check)
one(){ local tag=$1;shift;bash "$HERE/run.sh" --job "${JOB}_${tag}" "${COMMON[@]}" "$@" 2>&1 | tee "$CONSOLE/${tag}_node${RANK}.log"; }
for repeat in 1 2 3;do
 one "micro_r0${repeat}" --action micro --micro-suite notify --micro-repeat "$repeat" --micro-layout "$LAYOUT" --micro-threads 4 --micro-cpus "$CPUS" --warmups 20 --samples 50 --case-timeout 900
done
for repeat in 1 2 3;do
 one "baseline_r0${repeat}" --action run --phase-suite --api-only --warmups 50 --samples 50
done
one timeline --action run --phase-suite --api-only --stage-profile --capture-layout --timeline --warmups 5 --samples 5
echo "URMA_NOTIFICATION_SUITE_ALL_PASSED rank=$RANK output=$ROOT"
