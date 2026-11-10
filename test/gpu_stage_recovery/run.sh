#!/usr/bin/env bash
set -euo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
BACKEND= STEP= RANK= BUILD= JOB= ACTION=run MASTER=141.61.17.202 PORT=32480 GID=3 TOKENS=128
ROOT="${URMA_DIAG_HOME:-${VERBS_DIAG_HOME:-${HOME:?}}}/metax_dev/stage_recovery_results"
while [ "$#" -gt 0 ];do
 case "$1" in
 --backend) BACKEND=${2:?};shift 2;; --step) STEP=${2:?};shift 2;;
 --node-rank) RANK=${2:?};shift 2;; --build-dir) BUILD=${2:?};shift 2;;
 --job) JOB=${2:?};shift 2;; --action) ACTION=${2:?};shift 2;;
 --master-addr) MASTER=${2:?};shift 2;; --port) PORT=${2:?};shift 2;;
 --tokens) TOKENS=${2:?};shift 2;; --gid-index) GID=${2:?};shift 2;;
 --output) ROOT=${2:?};shift 2;;
 *) echo "unknown recovery argument: $1" >&2;exit 2;;
 esac
done
[[ "$BACKEND" == urma || "$BACKEND" == verbs ]] && [[ "$RANK" == 0 || "$RANK" == 1 ]] &&
 [[ "$JOB" =~ ^[a-zA-Z0-9_-]+$ ]] && [ -n "$BUILD" ] || { echo 'backend/rank/job/build required' >&2;exit 2; }
case "$STEP" in cpu|bypass|access|clock|kernels|waits|operators|all);;*) echo 'invalid step' >&2;exit 2;;esac
case "$ACTION" in build|check|run);;*) echo 'invalid action' >&2;exit 2;;esac
[[ "$TOKENS" == 128 || "$TOKENS" == 4096 ]] || { echo 'tokens must be 128 or 4096' >&2;exit 2; }
COMMON=(--action "$ACTION" --node-rank "$RANK" --job "$JOB" --mode normal --build-dir "$BUILD" --master-addr "$MASTER" --port "$PORT" --output "$ROOT" --normal-tokens "$TOKENS" --warmups 1 --samples 3)
if [ "$BACKEND" = urma ];then
 ENTRY="$HERE/../urma_mode_parity/run.sh"
 COMMON+=(--backend urma)
 if [ "$ACTION" != build ];then COMMON+=(--phase-suite --api-only --stage-profile --capture-layout --timeline);fi
else
 ENTRY="$HERE/../verbs_baseline/run.sh"
 COMMON+=(--variant current --gid-index "$GID")
 if [ "$ACTION" != build ];then COMMON+=(--no-profile --repeats 1 --stage-suite --cpu-diagnostics --timeline);fi
fi
if [ "$STEP" != cpu ];then
 if [ "$ACTION" = build ];then COMMON+=(--gpu-stage-build)
 else COMMON+=(--gpu-stages --gpu-stage-mode "$STEP");fi
fi
CONSOLE="$ROOT/$JOB/console/node$RANK";mkdir -p "$CONSOLE"
bash "$ENTRY" "${COMMON[@]}" 2>&1 | tee "$CONSOLE/${ACTION}.log"
echo "GPU_STAGE_RECOVERY_COMMAND_PASSED backend=$BACKEND step=$STEP action=$ACTION"
