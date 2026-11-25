#!/usr/bin/env bash
# A single 128-token control; always collect the failed run as well.
set -euo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
RANK= JOB= BUILD= STEP=baseline MASTER=141.61.17.202 PORT=32880
IFNAME=${URMA_DIAG_IFNAME:-${UCCL_SOCKET_IFNAME:-enp33s0}}
GPU_PCI=${URMA_DIAG_GPU_PCI:-0000:a1:00.0} ATOMIC_PORT=${URMA_DIAG_ATOMIC_PORT:-auto}
PROFILE=legacy FENCE=default TIMEOUT=300
DEVICE=udmac0d1e2 DEVICE_PLAN=same
ROOT="${URMA_DIAG_HOME:-${VERBS_DIAG_HOME:-${HOME:?}}}/metax_dev/reference_event_results"
DEST="$HERE/results"
while [ "$#" -gt 0 ]; do
  case "$1" in
    --node-rank) RANK=${2:?}; shift 2;; --job) JOB=${2:?}; shift 2;;
    --build-dir) BUILD=${2:?}; shift 2;; --step) STEP=${2:?}; shift 2;;
    --master-addr) MASTER=${2:?}; shift 2;; --port) PORT=${2:?}; shift 2;;
    --output) ROOT=${2:?}; shift 2;; --destination) DEST=${2:?}; shift 2;;
    --ifname) IFNAME=${2:?}; shift 2;; --gpu-pci) GPU_PCI=${2:?}; shift 2;;
    --atomic-port) ATOMIC_PORT=${2:?}; shift 2;; --case-timeout) TIMEOUT=${2:?}; shift 2;;
    --reference-profile) PROFILE=${2:?}; shift 2;; --normal-fadd-fence-plan) FENCE=${2:?}; shift 2;;
    --urma-device) DEVICE=${2:?}; shift 2;; --urma-device-plan) DEVICE_PLAN=${2:?}; shift 2;;
    *) echo "unknown recovery argument $1" >&2; exit 2;;
  esac
done
[[ "$RANK" == 0 || "$RANK" == 1 ]] && [[ "$JOB" =~ ^[a-zA-Z0-9_-]+$ ]] && [ -n "$BUILD" ] || exit 2
case "$STEP" in baseline|off|trace|paced);; *) exit 2;; esac
[ ! -e "$ROOT/$JOB/node$RANK" ] || { echo 'result exists; use a new job' >&2; exit 2; }
PY=${URMA_DIAG_PYTHON:-${VERBS_DIAG_PYTHON:-python3}}
SNAP="$ROOT/$JOB/console/node$RANK/connection"
SNAP_ARGS=(--output "$SNAP" --device "$DEVICE" --ifname "$IFNAME" --gpu-pci "$GPU_PCI" --atomic-port "$ATOMIC_PORT")
"$PY" "$HERE/connection_diag.py" "${SNAP_ARGS[@]}" --phase before
RUN_RC=0 SNAP_RC=0 COLLECT_RC=0
bash "$HERE/run.sh" --backend urma --action run --step "$STEP" \
  --node-rank "$RANK" --job "$JOB" --build-dir "$BUILD" --tokens 128 \
  --master-addr "$MASTER" --port "$PORT" --output "$ROOT" --ifname "$IFNAME" \
  --gpu-pci "$GPU_PCI" --atomic-port "$ATOMIC_PORT" --case-timeout "$TIMEOUT" \
  --urma-device "$DEVICE" --urma-device-plan "$DEVICE_PLAN" \
  --reference-profile "$PROFILE" --normal-fadd-fence-plan "$FENCE" || RUN_RC=$?
"$PY" "$HERE/connection_diag.py" "${SNAP_ARGS[@]}" --phase after \
  --run-root "$ROOT/$JOB/node$RANK" || SNAP_RC=$?
BUILD_ARG=--base-build
[ "$STEP" = baseline ] || BUILD_ARG=--trace-build
"$PY" "$HERE/../gpu_stage_recovery/collect.py" "$ROOT/$JOB" \
  --node-rank "$RANK" "$BUILD_ARG" "$BUILD" --destination "$DEST/$JOB" || COLLECT_RC=$?
echo "CONNECTION_CONTROL_RESULT run_rc=$RUN_RC snapshot_rc=$SNAP_RC collect_rc=$COLLECT_RC destination=$DEST/$JOB"
if [ "$RUN_RC" -ne 0 ]; then exit "$RUN_RC"; fi
if [ "$SNAP_RC" -ne 0 ]; then exit "$SNAP_RC"; fi
exit "$COLLECT_RC"
