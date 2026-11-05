#!/usr/bin/env bash
set -euo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
JOB=
RANK=
REPEATS=3
HAS_BUILD=0
DIAGNOSTICS=1
ORDERED=0
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
    --include-ordered-control) ORDERED=1; shift ;;
    --action|--backend|--mode|--phase-suite|--api-only|--ll-write-order|--normal-completion|--stage-profile|--save-traces|--output|--ll-hook-workload|--warmups|--samples)
      printf 'This Normal-only wrapper controls %s.\n' "$1" >&2; exit 2 ;;
    --help|-h)
      echo 'run_normal_ab.sh --job TAG --node-rank 0|1 --master-addr IP --build-dir DIR [--repeats 3] [--skip-diagnostics] [--include-ordered-control] [--result-root DIR]'
      exit 0 ;;
    *) COMMON+=("$1"); shift ;;
  esac
done
[[ "$JOB" =~ ^[a-zA-Z0-9_-]+$ ]] || { echo 'valid --job required' >&2; exit 2; }
[[ "$RANK" == 0 || "$RANK" == 1 ]] || { echo '--node-rank 0|1 required' >&2; exit 2; }
[[ "$REPEATS" =~ ^([1-9]|10)$ ]] || { echo '--repeats must be 1..10' >&2; exit 2; }
[ "$HAS_BUILD" = 1 ] || { echo '--build-dir required; build the Normal completion module first' >&2; exit 2; }
TASK_ROOT=${URMA_DIAG_HOME:-${HOME:?}}
RESULT_ROOT=${RESULT_ROOT:-"$TASK_ROOT/metax_dev/urma_normal_results/$JOB"}
CONSOLE="$TASK_ROOT/metax_dev/urma_normal_console/$JOB"
mkdir -p "$CONSOLE"
run_one() {
  local tag=$1 policy=$2 diagnostic=$3
  local extra=()
  if [ "$diagnostic" = 1 ]; then extra+=(--stage-profile); fi
  bash "$HERE/run.sh" --action run --backend urma --phase-suite --api-only \
    --mode normal --ll-write-order default --warmups 50 --samples 50 --case-timeout 600 \
    "${COMMON[@]}" --normal-completion "$policy" --job "${JOB}_${tag}_${policy}" \
    --output "$RESULT_ROOT" "${extra[@]}" \
    2>&1 | tee "$CONSOLE/${tag}_${policy}_node${RANK}.log"
}
POLICIES=(all paired)
if [ "$ORDERED" = 1 ]; then POLICIES=(all ordered paired); fi
# Confirm the candidate path is exercised before spending the full window.
if [ "$DIAGNOSTICS" = 1 ]; then
  for policy in "${POLICIES[@]}"; do run_one diagnostic "$policy" 1; done
fi
for ((repeat=1; repeat<=REPEATS; ++repeat)); do
  printf -v tag 'r%02d_baseline' "$repeat"
  for ((index=0; index<${#POLICIES[@]}; ++index)); do
    selected=$(((repeat - 1 + index) % ${#POLICIES[@]}))
    run_one "$tag" "${POLICIES[$selected]}" 0
  done
done
echo "NORMAL_AB_ALL_PASSED rank=$RANK output=$RESULT_ROOT"
