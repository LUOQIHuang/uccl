#!/usr/bin/env bash
set -euo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
BACKEND=urma RANK= BUILD= JOB= ACTION=run MASTER=141.61.17.202 PORT=32580 GID=3
ROOT="${URMA_DIAG_HOME:-${VERBS_DIAG_HOME:-${HOME:?}}}/metax_dev/fast_normal_results"
while [ "$#" -gt 0 ];do
 case "$1" in
 --backend) BACKEND=${2:?};shift 2;; --node-rank) RANK=${2:?};shift 2;;
 --build-dir) BUILD=${2:?};shift 2;; --job) JOB=${2:?};shift 2;;
 --action) ACTION=${2:?};shift 2;; --master-addr) MASTER=${2:?};shift 2;;
 --port) PORT=${2:?};shift 2;; --gid-index) GID=${2:?};shift 2;;
 --output) ROOT=${2:?};shift 2;; *) echo "unknown argument: $1" >&2;exit 2;;
 esac
done
[[ "$BACKEND" == urma || "$BACKEND" == verbs ]] && [[ "$RANK" == 0 || "$RANK" == 1 ]] && [[ "$JOB" =~ ^[a-zA-Z0-9_-]+$ ]] && [ -n "$BUILD" ] || { echo 'backend/rank/job/build required' >&2;exit 2; }
COMMON=(--node-rank "$RANK" --build-dir "$BUILD" --mode normal --master-addr "$MASTER" --port "$PORT" --output "$ROOT" --warmups 50 --samples 50)
if [ "$BACKEND" = urma ];then
 ENTRY="$HERE/../urma_mode_parity/run.sh"
 COMMON+=(--backend urma --normal-tokens 4096)
else
 ENTRY="$HERE/../verbs_baseline/run.sh"
 COMMON+=(--variant current --gid-index "$GID" --no-profile --repeats 1)
fi
CONSOLE="$ROOT/$JOB/console/node$RANK";mkdir -p "$CONSOLE"
case "$ACTION" in
 build) bash "$ENTRY" "${COMMON[@]}" --action build --job "${JOB}_build" 2>&1 | tee "$CONSOLE/build.log";;
 run)
  EXTRA=();if [ "$BACKEND" = urma ];then EXTRA=(--phase-suite --api-only);fi
  bash "$ENTRY" "${COMMON[@]}" "${EXTRA[@]}" --action check --job "${JOB}_check" 2>&1 | tee "$CONSOLE/check.log"
  bash "$ENTRY" "${COMMON[@]}" "${EXTRA[@]}" --action run --job "${JOB}_baseline" 2>&1 | tee "$CONSOLE/baseline.log"
  if [ "$BACKEND" = urma ];then EXTRA+=(--stage-profile);else EXTRA+=(--cpu-diagnostics --cpu-light);fi
  bash "$ENTRY" "${COMMON[@]}" "${EXTRA[@]}" --action run --job "${JOB}_cpu" 2>&1 | tee "$CONSOLE/cpu.log";;
 *) echo 'action must be build or run' >&2;exit 2;;
esac
echo "FAST_NORMAL_COMMAND_PASSED action=$ACTION"
