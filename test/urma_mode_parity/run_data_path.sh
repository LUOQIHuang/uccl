#!/usr/bin/env bash
set -euo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
JOB= RANK= RESULT_ROOT=
REPEATS=3
REFERENCE=0
HAS_BUILD=0
COMMON=()
while [ "$#" -gt 0 ]; do
  case "$1" in
    --job) JOB=${2:?missing job}; shift 2 ;;
    --node-rank) RANK=${2:?missing rank}; COMMON+=("$1" "$2"); shift 2 ;;
    --build-dir) HAS_BUILD=1; COMMON+=("$1" "${2:?missing build dir}"); shift 2 ;;
    --result-root) RESULT_ROOT=${2:?missing result root}; shift 2 ;;
    --repeats) REPEATS=${2:?missing repeats}; shift 2 ;;
    --with-reference) REFERENCE=1; shift ;;
    --action|--backend|--mode|--phase-suite|--api-only|--ll-write-order|--normal-completion|--stage-profile|--save-traces|--output|--ll-hook-workload|--warmups|--samples|--capture-layout|--micro-layout|--micro-threads|--collect-suite)
      printf 'This Normal data-path wrapper controls %s.\n' "$1" >&2; exit 2 ;;
    --help|-h)
      echo 'run_data_path.sh --job TAG --node-rank 0|1 --master-addr IP --build-dir DIR [--repeats 3] [--with-reference] [--result-root DIR]'
      exit 0 ;;
    *) COMMON+=("$1"); shift ;;
  esac
done
[[ "$JOB" =~ ^[a-zA-Z0-9_-]+$ ]] || { echo 'valid --job required' >&2; exit 2; }
[[ "$RANK" == 0 || "$RANK" == 1 ]] || { echo '--node-rank 0|1 required' >&2; exit 2; }
[[ "$REPEATS" =~ ^([1-9]|10)$ ]] || { echo '--repeats must be 1..10' >&2; exit 2; }
[ "$HAS_BUILD" = 1 ] || { echo '--build-dir required; rebuild the data-path module first' >&2; exit 2; }
TASK_ROOT=${URMA_DIAG_HOME:-${HOME:?}}
RESULT_ROOT=${RESULT_ROOT:-"$TASK_ROOT/metax_dev/urma_data_results/$JOB"}
CONSOLE="$TASK_ROOT/metax_dev/urma_data_console/$JOB"
mkdir -p "$CONSOLE"
run_one() {
  local suffix=$1
  shift
  bash "$HERE/run.sh" --backend urma --mode normal --normal-completion all \
    --ll-write-order default --case-timeout 900 "${COMMON[@]}" \
    --job "${JOB}_${suffix}" --output "$RESULT_ROOT" "$@" \
    2>&1 | tee "$CONSOLE/${suffix}_node${RANK}.log"
}
# Captured timings are deliberately kept separate from performance baselines.
run_one layout --action run --phase-suite --api-only --capture-layout --warmups 5 --samples 5
LAYOUT="$RESULT_ROOT/${JOB}_layout/node${RANK}/normal/write_layout.jsonl"
for ((repeat=1; repeat<=REPEATS; ++repeat)); do
  printf -v tag 'micro_r%02d' "$repeat"
  run_one "$tag" --action micro --micro-layout "$LAYOUT" --micro-threads 4 --warmups 20 --samples 50
done
if [ "$REFERENCE" = 1 ]; then
  run_one reference --action run --phase-suite --api-only --warmups 50 --samples 50
fi
echo "NORMAL_DATA_PATH_ALL_PASSED rank=$RANK output=$RESULT_ROOT"
