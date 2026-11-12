#!/usr/bin/env bash
set -euo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
BACKEND=urma STEP= ACTION=run RANK= BUILD= JOB= TOKENS=4096 MASTER=141.61.17.202 PORT=32880 GID=3 PROFILE=legacy
ROOT="${URMA_DIAG_HOME:-${VERBS_DIAG_HOME:-${HOME:?}}}/metax_dev/reference_event_results"
while [ "$#" -gt 0 ];do
 case "$1" in
 --backend) BACKEND=${2:?};shift 2;; --step) STEP=${2:?};shift 2;; --action) ACTION=${2:?};shift 2;;
 --node-rank) RANK=${2:?};shift 2;; --build-dir) BUILD=${2:?};shift 2;; --job) JOB=${2:?};shift 2;;
 --tokens) TOKENS=${2:?};shift 2;; --master-addr) MASTER=${2:?};shift 2;; --port) PORT=${2:?};shift 2;;
 --output) ROOT=${2:?};shift 2;; --gid-index) GID=${2:?};shift 2;;
 --reference-profile) PROFILE=${2:?};shift 2;;
 *) echo "unknown reference-event argument $1" >&2;exit 2;;
 esac
done
[[ "$BACKEND" == urma || "$BACKEND" == verbs ]] && [[ "$RANK" == 0 || "$RANK" == 1 ]] && [[ "$JOB" =~ ^[a-zA-Z0-9_-]+$ ]] && [ -n "$BUILD" ] || { echo 'backend/rank/build/job required' >&2;exit 2; }
case "$STEP" in baseline|trace|post|off|batch|paced);;*) echo 'step must be baseline/trace/post/off/batch/paced' >&2;exit 2;;esac
case "$ACTION" in build|check|run);;*) exit 2;;esac
[[ "$TOKENS" == 128 || "$TOKENS" == 4096 ]] || exit 2
[[ "$PROFILE" == legacy || "$PROFILE" == boundaries ]] || exit 2
WARMUPS=50 SAMPLES=50
if [[ "$STEP" == trace || "$STEP" == batch || "$STEP" == paced ]];then WARMUPS=5 SAMPLES=10;fi
if [ "$TOKENS" = 128 ];then WARMUPS=1 SAMPLES=3;fi
COMMON=(--node-rank "$RANK" --job "$JOB" --mode normal --build-dir "$BUILD" --master-addr "$MASTER" --port "$PORT" --output "$ROOT" --normal-tokens "$TOKENS" --warmups "$WARMUPS" --samples "$SAMPLES" --build-jobs 1 --reference-profile "$PROFILE")
if [ "$BACKEND" = urma ];then
 ENTRY="$HERE/../urma_mode_parity/run.sh";COMMON+=(--backend urma --expected-kernel 6.6.0-gdr_f00955680_diag)
 if [ "$ACTION" != build ];then COMMON+=(--phase-suite --api-only --dispatch-focus);fi
else
 ENTRY="$HERE/../verbs_baseline/run.sh";COMMON+=(--variant current --gid-index "$GID" --expected-kernel 6.6.0-145.3.34.165.oe2403sp3.aarch64)
 if [ "$ACTION" != build ];then COMMON+=(--no-profile --repeats 1 --dispatch-focus);fi
fi
if [[ "$STEP" == trace || "$STEP" == off || "$STEP" == batch || "$STEP" == paced ]];then
 MODE=$STEP;[ "$STEP" != trace ] || MODE=on;[ "$ACTION" != build ] || MODE=build
 COMMON+=(--reference-events "$MODE")
fi
CONSOLE="$ROOT/$JOB/console/node$RANK";mkdir -p "$CONSOLE"
if [ "$ACTION" = run ];then
 [ ! -e "$ROOT/$JOB/node$RANK" ] || { echo 'result exists; use a new job' >&2;exit 2; }
 bash "$ENTRY" "${COMMON[@]}" --action check 2>&1 | tee "$CONSOLE/check.log"
fi
bash "$ENTRY" "${COMMON[@]}" --action "$ACTION" 2>&1 | tee "$CONSOLE/${ACTION}.log"
echo "REFERENCE_EVENT_COMMAND_PASSED backend=$BACKEND step=$STEP action=$ACTION"
