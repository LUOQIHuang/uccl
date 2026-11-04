#!/usr/bin/env bash
set -euo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
JOB=
REPEATS=3
RANK=
HAS_BUILD=0
COMMON=()
RESULT_ROOT=
while [ "$#" -gt 0 ]; do
  case "$1" in
    --job) JOB=${2:?missing job}; shift 2 ;;
    --repeats) REPEATS=${2:?missing repeats}; shift 2 ;;
    --result-root) RESULT_ROOT=${2:?missing result root}; shift 2 ;;
    --node-rank) RANK=${2:?missing rank}; COMMON+=("$1" "$2"); shift 2 ;;
    --build-dir) HAS_BUILD=1; COMMON+=("$1" "${2:?missing build directory}"); shift 2 ;;
    --action|--phase-suite|--stage-profile|--ll-hook-workload|--save-traces|--backend|--output)
      printf 'This wrapper controls %s; use --result-root for output.\n' "$1" >&2; exit 2 ;;
    --help|-h)
      printf '%s\n' 'run_stages.sh --job TAG --node-rank 0|1 --master-addr IP --build-dir DIR [--repeats 3] [--mode both|normal|low_latency] [--result-root DIR] [other run.sh options]'
      exit 0 ;;
    *) COMMON+=("$1"); shift ;;
  esac
done
[[ "$JOB" =~ ^[a-zA-Z0-9_-]+$ ]] || { echo 'valid --job required' >&2; exit 2; }
[[ "$REPEATS" =~ ^([1-9]|10)$ ]] || { echo '--repeats must be 1..10' >&2; exit 2; }
[[ "$RANK" == 0 || "$RANK" == 1 ]] || { echo '--node-rank 0|1 required' >&2; exit 2; }
[ "$HAS_BUILD" = 1 ] || { echo '--build-dir required (build the diagnostic module first)' >&2; exit 2; }
TASK_ROOT=${URMA_DIAG_HOME:-${HOME:?}}
RESULT_ROOT=${RESULT_ROOT:-"$TASK_ROOT/metax_dev/urma_stage_results/$JOB"}
CONSOLE="$TASK_ROOT/metax_dev/urma_stage_console/$JOB"
mkdir -p "$CONSOLE"
for ((repeat=1; repeat<=REPEATS; ++repeat)); do
  printf -v tag 'r%02d' "$repeat"
  for variant in baseline instrumented; do
    EXTRA=()
    if [ "$variant" = instrumented ]; then
      EXTRA+=(--stage-profile)
      if [ "$repeat" = 1 ]; then EXTRA+=(--save-traces); fi
    fi
    CASE_JOB="${JOB}_${tag}_${variant}"
    printf 'PHASE_RUN job=%s rank=%s output=%s\n' "$CASE_JOB" "$RANK" "$RESULT_ROOT"
    bash "$HERE/run.sh" --action run --phase-suite --ll-hook-workload immediate \
      --warmups 50 --samples 50 "${COMMON[@]}" --job "$CASE_JOB" \
      --output "$RESULT_ROOT" "${EXTRA[@]}" \
      2>&1 | tee "$CONSOLE/${tag}_${variant}_node${RANK}.log"
  done
done
printf 'STAGE_SUITE_ALL_PASSED rank=%s output=%s\n' "$RANK" "$RESULT_ROOT"
