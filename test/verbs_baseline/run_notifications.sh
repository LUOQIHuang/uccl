#!/usr/bin/env bash
set -euo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
JOB= RANK= BUILD= LAYOUT= ROOT= GID=3 CPUS=auto MASTER=141.61.17.202 PORT=31880
while [ "$#" -gt 0 ];do
 case "$1" in
 --job) JOB=${2:?};shift 2;; --node-rank) RANK=${2:?};shift 2;; --build-dir) BUILD=${2:?};shift 2;;
 --layout-file) LAYOUT=${2:?};shift 2;; --result-root) ROOT=${2:?};shift 2;; --gid-index) GID=${2:?};shift 2;;
 --micro-cpus) CPUS=${2:?};shift 2;; --master-addr) MASTER=${2:?};shift 2;; --port) PORT=${2:?};shift 2;;
 *) echo "unknown notification suite argument: $1" >&2;exit 2;;
 esac
done
[[ "$JOB" =~ ^[a-zA-Z0-9_-]+$ ]] && [[ "$RANK" == 0 || "$RANK" == 1 ]] || { echo 'valid job/rank required' >&2;exit 2; }
[ -n "$BUILD" ] && [ -f "$LAYOUT" ] || { echo 'new build and existing layout required' >&2;exit 2; }
TASK_ROOT=${VERBS_DIAG_HOME:-${HOME:?}}
ROOT=${ROOT:-"$TASK_ROOT/metax_dev/verbs_notify_results/$JOB"}
CONSOLE="$TASK_ROOT/metax_dev/verbs_notify_console/$JOB";mkdir -p "$CONSOLE"
COMMON=(--variant current --mode normal --node-rank "$RANK" --master-addr "$MASTER" --build-dir "$BUILD" --output "$ROOT" --port "$PORT" --gid-index "$GID")
one(){ local tag=$1;shift;bash "$HERE/run.sh" --job "${JOB}_${tag}" "${COMMON[@]}" "$@" 2>&1 | tee "$CONSOLE/${tag}_node${RANK}.log"; }
for repeat in 1 2 3;do
 one "notify_r0${repeat}" --action micro --micro-suite notify --micro-repeat "$repeat" --micro-layout "$LAYOUT" --micro-threads 4 --micro-cpus "$CPUS" --warmups 20 --samples 50 --case-timeout 900
done
one timeline --action run --cpu-diagnostics --timeline --no-profile --repeats 1 --warmups 5 --samples 5
echo "VERBS_NOTIFICATION_SUITE_ALL_PASSED rank=$RANK output=$ROOT"
