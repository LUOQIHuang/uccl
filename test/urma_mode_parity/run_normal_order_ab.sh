#!/usr/bin/env bash
set -euo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
JOB= RANK= BUILD= ROOT= ACTION=run REPEATS=3 MASTER=141.61.17.202 PORT=32780
while [ "$#" -gt 0 ];do
 case "$1" in
 --job) JOB=${2:?};shift 2;; --node-rank) RANK=${2:?};shift 2;;
 --build-dir) BUILD=${2:?};shift 2;; --result-root) ROOT=${2:?};shift 2;;
 --master-addr) MASTER=${2:?};shift 2;; --port) PORT=${2:?};shift 2;;
 --repeats) REPEATS=${2:?};shift 2;; --action) ACTION=${2:?};shift 2;;
 --help|-h) echo 'run_normal_order_ab.sh --job TAG --node-rank 0|1 --build-dir DIR [--action check|run] [--master-addr IP] [--result-root DIR] [--port 32780] [--repeats 3]';exit 0;;
 *) echo "Unsupported Normal order argument: $1" >&2;exit 2;;
 esac
done
[[ "$JOB" =~ ^[a-zA-Z0-9_-]+$ ]] && [[ "$RANK" == 0 || "$RANK" == 1 ]] && [ -n "$BUILD" ] || { echo 'job/rank/build required' >&2;exit 2; }
[[ "$ACTION" == check || "$ACTION" == run ]] || { echo 'action must be check/run; build separately' >&2;exit 2; }
[[ "$REPEATS" =~ ^([1-9]|10)$ ]] || { echo 'repeats must be 1..10' >&2;exit 2; }
TASK_ROOT=${URMA_DIAG_HOME:-${HOME:?}}
ROOT=${ROOT:-"$TASK_ROOT/metax_dev/urma_normal_order_results/$JOB"}
CONSOLE="$ROOT/${JOB}_console/node$RANK";mkdir -p "$CONSOLE"
COMMON=(--backend urma --mode normal --phase-suite --api-only --normal-completion all
 --ll-write-order default --normal-tokens 4096 --hidden 7168 --experts 256 --topk 8
 --warmups 50 --samples 50 --expected-kernel 6.6.0-gdr_f00955680_diag
 --node-rank "$RANK" --master-addr "$MASTER" --port "$PORT" --build-dir "$BUILD" --output "$ROOT")
for order in relax strong;do
 bash "$HERE/run.sh" "${COMMON[@]}" --action check --normal-write-order "$order" --job "${JOB}_check_${order}" \
  2>&1 | tee "$CONSOLE/check_${order}.log"
done
[ "$ACTION" = run ] || { echo 'NORMAL_ORDER_CHECK_PASSED';exit 0; }
# Check every target before starting either policy; existing raw results stay intact.
for ((r=1;r<=REPEATS;++r));do
 printf -v tag 'r%02d' "$r"
 for order in relax strong;do
  [ ! -e "$ROOT/${JOB}_${tag}_${order}/node$RANK" ] || { echo "Result exists: ${JOB}_${tag}_${order}; use a new job" >&2;exit 2; }
 done
done
for ((r=1;r<=REPEATS;++r));do
 printf -v tag 'r%02d' "$r"
 ORDERS=(relax strong);if ((r%2==0));then ORDERS=(strong relax);fi
 for order in "${ORDERS[@]}";do
  bash "$HERE/run.sh" "${COMMON[@]}" --action run --normal-write-order "$order" --job "${JOB}_${tag}_${order}" \
   2>&1 | tee "$CONSOLE/${tag}_${order}.log"
 done
done
echo "NORMAL_ORDER_ALL_PASSED rank=$RANK repeats=$REPEATS output=$ROOT"
