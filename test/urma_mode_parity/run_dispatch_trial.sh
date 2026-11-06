#!/usr/bin/env bash
set -euo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
JOB= RANK= ROOT= HAS_BUILD=0
COMMON=()
while [ "$#" -gt 0 ]; do
  case "$1" in
    --job) JOB=${2:?missing job}; shift 2 ;;
    --node-rank) RANK=${2:?missing rank}; COMMON+=("$1" "$2"); shift 2 ;;
    --build-dir) HAS_BUILD=1; COMMON+=("$1" "${2:?missing build dir}"); shift 2 ;;
    --result-root) ROOT=${2:?missing root}; shift 2 ;;
    --action|--backend|--mode|--phase-suite|--api-only|--normal-completion|--ll-write-order|--stage-profile|--capture-layout|--dispatch-trial|--dispatch-delay-us|--warmups|--samples|--save-traces|--output|--collect-suite)
      echo "dispatch trial wrapper controls $1" >&2; exit 2 ;;
    --help|-h)
      echo 'run_dispatch_trial.sh --job TAG --node-rank 0|1 --master-addr IP --build-dir DIR [--result-root DIR]'; exit 0 ;;
    *) COMMON+=("$1"); shift ;;
  esac
done
[[ "$JOB" =~ ^[a-zA-Z0-9_-]+$ ]] || { echo 'valid --job required' >&2; exit 2; }
[[ "$RANK" == 0 || "$RANK" == 1 ]] && [ "$HAS_BUILD" = 1 ] || { echo '--node-rank and new --build-dir required' >&2; exit 2; }
TASK_ROOT=${URMA_DIAG_HOME:-${HOME:?}}
ROOT=${ROOT:-"$TASK_ROOT/metax_dev/urma_dispatch_trial_results/$JOB"}
CONSOLE="$TASK_ROOT/metax_dev/urma_dispatch_trial_console/$JOB"
mkdir -p "$CONSOLE"
run_one() {
  local tag=$1 delay=$2 diagnostic=$3
  local extra=() warmups=20 samples=50
  if [ "$diagnostic" = 1 ]; then extra+=(--stage-profile --capture-layout); warmups=5; samples=5; fi
  bash "$HERE/run.sh" --action run --backend urma --mode normal --phase-suite --api-only \
    --normal-completion all --ll-write-order default --dispatch-trial --dispatch-delay-us "$delay" \
    --warmups "$warmups" --samples "$samples" --case-timeout 900 "${COMMON[@]}" \
    --job "${JOB}_${tag}_d${delay}" --output "$ROOT" "${extra[@]}" \
    2>&1 | tee "$CONSOLE/${tag}_d${delay}_node${RANK}.log"
}
for delay in 0 200; do run_one diagnostic "$delay" 1; done
for repeat in 1 2 3; do
  ORDER=(0 200)
  if [ "$repeat" = 2 ]; then ORDER=(200 0); fi
  for delay in "${ORDER[@]}"; do run_one "r0${repeat}" "$delay" 0; done
done
echo "DISPATCH_TRIAL_ALL_PASSED rank=$RANK output=$ROOT"
