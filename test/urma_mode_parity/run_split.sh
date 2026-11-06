#!/usr/bin/env bash
set -euo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
JOB= RANK= LAYOUT= ROOT=
HAS_BUILD=0
COMMON=()
while [ "$#" -gt 0 ]; do
  case "$1" in
    --job) JOB=${2:?missing job}; shift 2 ;;
    --node-rank) RANK=${2:?missing rank}; COMMON+=("$1" "$2"); shift 2 ;;
    --build-dir) HAS_BUILD=1; COMMON+=("$1" "${2:?missing build dir}"); shift 2 ;;
    --layout-file) LAYOUT=${2:?missing layout}; shift 2 ;;
    --result-root) ROOT=${2:?missing root}; shift 2 ;;
    --action|--backend|--mode|--phase-suite|--api-only|--ll-write-order|--normal-completion|--stage-profile|--save-traces|--output|--warmups|--samples|--capture-layout|--micro-layout|--micro-threads|--micro-suite|--collect-suite)
      echo "split wrapper controls $1" >&2; exit 2 ;;
    --help|-h)
      echo 'run_split.sh --job TAG --node-rank 0|1 --master-addr IP --build-dir DIR --layout-file FILE [--micro-cpus auto|C0,C1,C2,C3] [--result-root DIR]'; exit 0 ;;
    *) COMMON+=("$1"); shift ;;
  esac
done
[[ "$JOB" =~ ^[a-zA-Z0-9_-]+$ ]] || { echo 'valid --job required' >&2; exit 2; }
[[ "$RANK" == 0 || "$RANK" == 1 ]] || { echo '--node-rank 0|1 required' >&2; exit 2; }
[ "$HAS_BUILD" = 1 ] && [ -f "$LAYOUT" ] || { echo 'new --build-dir and existing --layout-file required' >&2; exit 2; }
TASK_ROOT=${URMA_DIAG_HOME:-${HOME:?}}
ROOT=${ROOT:-"$TASK_ROOT/metax_dev/urma_split_results/$JOB"}
CONSOLE="$TASK_ROOT/metax_dev/urma_split_console/$JOB"
mkdir -p "$CONSOLE"
for repeat in 1 2 3; do
  ORDER=(1 4)
  if [ "$repeat" = 2 ]; then ORDER=(4 1); fi
  for threads in "${ORDER[@]}"; do
    tag="r0${repeat}_t${threads}"
    bash "$HERE/run.sh" --action micro --backend urma --mode normal --normal-completion all \
      --ll-write-order default --case-timeout 900 "${COMMON[@]}" \
      --micro-suite split --micro-threads "$threads" --micro-layout "$LAYOUT" \
      --warmups 20 --samples 50 --job "${JOB}_${tag}" --output "$ROOT" \
      2>&1 | tee "$CONSOLE/${tag}_node${RANK}.log"
  done
done
echo "NORMAL_SPLIT_ALL_PASSED rank=$RANK output=$ROOT"
