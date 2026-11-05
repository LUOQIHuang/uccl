#!/usr/bin/env bash
set -euo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
JOB=
RANK=
REPEATS=3
HAS_BUILD=0
DIAGNOSTICS=1
COMMON=()
RESULT_ROOT=
while [ "$#" -gt 0 ]; do
  case "$1" in
    --job) JOB=${2:?missing job}; shift 2 ;;
    --node-rank) RANK=${2:?missing rank}; COMMON+=("$1" "$2"); shift 2 ;;
    --build-dir) HAS_BUILD=1; COMMON+=("$1" "${2:?missing build dir}"); shift 2 ;;
    --result-root) RESULT_ROOT=${2:?missing result root}; shift 2 ;;
    --repeats) REPEATS=${2:?missing repeats}; shift 2 ;;
    --skip-diagnostics) DIAGNOSTICS=0; shift ;;
    --action|--backend|--phase-suite|--api-only|--ll-write-order|--stage-profile|--save-traces|--output|--ll-hook-workload|--warmups|--samples)
      printf 'This wrapper controls %s.\n' "$1" >&2; exit 2 ;;
    --help|-h)
      echo 'run_order_ab.sh --job TAG --node-rank 0|1 --master-addr IP --build-dir DIR [--repeats 3] [--skip-diagnostics] [--mode low_latency|both] [--result-root DIR]'
      exit 0 ;;
    *) COMMON+=("$1"); shift ;;
  esac
done
[[ "$JOB" =~ ^[a-zA-Z0-9_-]+$ ]] || { echo 'valid --job required' >&2; exit 2; }
[[ "$RANK" == 0 || "$RANK" == 1 ]] || { echo '--node-rank 0|1 required' >&2; exit 2; }
[[ "$REPEATS" =~ ^([1-9]|10)$ ]] || { echo '--repeats must be 1..10' >&2; exit 2; }
[ "$HAS_BUILD" = 1 ] || { echo '--build-dir required; rebuild the order A/B module first' >&2; exit 2; }
TASK_ROOT=${URMA_DIAG_HOME:-${HOME:?}}
RESULT_ROOT=${RESULT_ROOT:-"$TASK_ROOT/metax_dev/urma_order_results/$JOB"}
CONSOLE="$TASK_ROOT/metax_dev/urma_order_console/$JOB"
mkdir -p "$CONSOLE"
run_one() {
  local tag=$1 order=$2 diagnostic=$3
  local extra=()
  if [ "$diagnostic" = 1 ]; then extra+=(--stage-profile); fi
  bash "$HERE/run.sh" --action run --backend urma --phase-suite --api-only \
    --mode low_latency --ll-hook-workload immediate --warmups 50 --samples 50 \
    "${COMMON[@]}" --ll-write-order "$order" --job "${JOB}_${tag}_${order}" \
    --output "$RESULT_ROOT" "${extra[@]}" \
    2>&1 | tee "$CONSOLE/${tag}_${order}_node${RANK}.log"
}
for ((repeat=1; repeat<=REPEATS; ++repeat)); do
  printf -v tag 'r%02d_baseline' "$repeat"
  ORDERS=(strong relax)
  if ((repeat % 2 == 0)); then ORDERS=(relax strong); fi
  for order in "${ORDERS[@]}"; do run_one "$tag" "$order" 0; done
done
if [ "$DIAGNOSTICS" = 1 ]; then
  for order in strong relax; do run_one diagnostic "$order" 1; done
fi
echo "ORDER_AB_ALL_PASSED rank=$RANK output=$RESULT_ROOT"
