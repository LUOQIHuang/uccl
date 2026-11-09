#!/usr/bin/env bash
set -euo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
JOB= RANK= BUILD= LAYOUT= ROOT= GID=3 CPUS=auto MASTER=141.61.17.202 PORT=31780
while [ "$#" -gt 0 ]; do
 case "$1" in
  --job) JOB=${2:?};shift 2;; --node-rank) RANK=${2:?};shift 2;; --build-dir) BUILD=${2:?};shift 2;;
  --layout-file) LAYOUT=${2:?};shift 2;; --result-root) ROOT=${2:?};shift 2;; --gid-index) GID=${2:?};shift 2;;
  --micro-cpus) CPUS=${2:?};shift 2;; --master-addr) MASTER=${2:?};shift 2;; --port) PORT=${2:?};shift 2;;
  --help|-h) echo 'run_normal_campaign.sh --job TAG --node-rank 0|1 --build-dir DIR --layout-file FILE [--gid-index 3] [--micro-cpus auto] [--result-root DIR]';exit 0;;
  *) echo "unknown campaign argument: $1" >&2;exit 2;;
 esac
done
[[ "$JOB" =~ ^[a-zA-Z0-9_-]+$ ]] && [[ "$RANK" == 0 || "$RANK" == 1 ]] || { echo 'valid job and rank required' >&2;exit 2; }
[ -n "$BUILD" ] && [ -f "$LAYOUT" ] || { echo 'new build directory and existing layout required' >&2;exit 2; }
TASK_ROOT=${VERBS_DIAG_HOME:-${HOME:?}}
ROOT=${ROOT:-"$TASK_ROOT/metax_dev/verbs_campaign_results/$JOB"}
CONSOLE="$TASK_ROOT/metax_dev/verbs_campaign_console/$JOB"
mkdir -p "$CONSOLE"
COMMON=(--variant current --mode normal --node-rank "$RANK" --master-addr "$MASTER" --build-dir "$BUILD" --output "$ROOT" --port "$PORT" --gid-index "$GID")
one() { local tag=$1;shift;bash "$HERE/run.sh" --job "${JOB}_${tag}" "${COMMON[@]}" "$@" 2>&1 | tee "$CONSOLE/${tag}_node${RANK}.log"; }
one baseline --action run --repeats 3 --warmups 50 --samples 50
one cpu --action run --cpu-diagnostics --no-profile --repeats 1 --warmups 5 --samples 5
for repeat in 1 2 3; do
 ORDER=(1 4);if [ "$repeat" = 2 ];then ORDER=(4 1);fi
 for threads in "${ORDER[@]}";do
  one "micro_r0${repeat}_t${threads}" --action micro --micro-layout "$LAYOUT" --micro-threads "$threads" --micro-cpus "$CPUS" --warmups 20 --samples 50 --case-timeout 900
 done
done
echo "VERBS_NORMAL_CAMPAIGN_ALL_PASSED rank=$RANK output=$ROOT"
