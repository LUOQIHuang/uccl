#!/usr/bin/env bash
set -euo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
export URMA_DIAG_HOME=${URMA_DIAG_HOME:-/home/h00918771}
export VERBS_DIAG_HOME="$URMA_DIAG_HOME"
export URMA_DIAG_PYTHON=${URMA_DIAG_PYTHON:-"$URMA_DIAG_HOME/metax_dev/venv-maca-py312/bin/python"}
export VERBS_DIAG_PYTHON="$URMA_DIAG_PYTHON"
export MACA_PATH=${MACA_PATH:-/opt/maca-3.9.0}
export URMA_DIAG_LIBRARY_DIR=${URMA_DIAG_LIBRARY_DIR:-"$URMA_DIAG_HOME/metax_dev/urma_after_kernel_20261029/diag/umdk_private_618957c3/lib"}
export VERBS_DIAG_LIBRARY_DIR="$URMA_DIAG_LIBRARY_DIR"
export URMA_DIAG_INCLUDE_DIR=${URMA_DIAG_INCLUDE_DIR:-"$URMA_DIAG_HOME/metax_dev/urma_sdk_private_include"}
RANK= ACTION=run JOB=urma_writes_first_batch_01 MASTER=141.61.17.202 PORT=33880
BUILD="$URMA_DIAG_HOME/metax_dev/writes_first_batch_build/urma_v1"
ROOT="$URMA_DIAG_HOME/metax_dev/writes_first_batch_results"
DEST="$HERE/results/urma_writes_first_batch_01"
while [ "$#" -gt 0 ];do
 case "$1" in
 --node-rank) RANK=${2:?};shift 2;; --action) ACTION=${2:?};shift 2;;
 --job) JOB=${2:?};shift 2;; --build-dir) BUILD=${2:?};shift 2;;
 --output) ROOT=${2:?};shift 2;; --destination) DEST=${2:?};shift 2;;
 --master-addr) MASTER=${2:?};shift 2;; --port) PORT=${2:?};shift 2;;
 *) echo "unknown argument $1" >&2;exit 2;;esac
done
[[ "$RANK" == 0 || "$RANK" == 1 ]] && [[ "$JOB" =~ ^[a-zA-Z0-9_-]+$ ]] || exit 2
[[ "$ACTION" == build || "$ACTION" == run ]] || exit 2
[[ "$PORT" =~ ^[0-9]+$ ]] && ((PORT>=1024 && PORT<65534)) || exit 2
if [ "$RANK" = 0 ];then DEVICE=udmac1d1e2;IFACE=enp40s0
else DEVICE=udmac0d1e2;IFACE=enp34s0f5u1u4;fi
IFACE=${URMA_DIAG_IFNAME:-$IFACE}
if [ "$ACTION" = build ];then
 rc=0;collected=0
 bash "$HERE/run.sh" --backend urma --action build --step batch --reference-profile boundaries \
  --node-rank "$RANK" --build-dir "$BUILD" --job "${JOB}_build" --output "$ROOT" || rc=$?
 "$URMA_DIAG_PYTHON" "$HERE/../gpu_stage_recovery/collect.py" "$ROOT/${JOB}_build" --node-rank "$RANK" --trace-build "$BUILD" --destination "$DEST/${JOB}_build" || collected=$?
 [ "$rc" = 0 ] || exit "$rc";exit "$collected"
fi
unset UCCL_IB_MAX_INFLIGHT_BYTES
for tag in r01_lane_mixed r01_writes_first r02_writes_first r02_lane_mixed;do
 [ ! -e "$ROOT/${JOB}_${tag}/node$RANK" ] && [ ! -e "$DEST/${JOB}_${tag}/node$RANK" ] || { echo 'result exists; use a new job' >&2;exit 2; }
done
run_one(){
 local tag=$1 schedule=$2 name="${JOB}_${1}" rc=0 collected=0
 local console="$ROOT/$name/console/node$RANK"
 mkdir -p "$console"
 local args=(--backend urma --mode normal --phase-suite --api-only --dispatch-focus
 --reference-events batch --reference-profile boundaries --normal-submit-schedule "$schedule" --normal-submit-witness
 --normal-fadd-fence-plan peer-tail-off --require-normal-atomic-roles --require-normal-retire
 --normal-window-plan default --normal-retire-plan ordered --normal-completion all
 --node-rank "$RANK" --master-addr "$MASTER" --port "$PORT" --ifname "$IFACE"
 --urma-device "$DEVICE" --urma-device-plan 202c1-204c0 --gpu-pci "${URMA_DIAG_GPU_PCI:-0000:21:00.0}" --atomic-port auto
 --expected-kernel 6.6.0-gdr_f00955680_diag --normal-tokens 4096 --warmups 5 --samples 10
 --case-timeout 600 --build-dir "$BUILD" --output "$ROOT" --job "$name")
 bash "$HERE/../urma_mode_parity/run.sh" "${args[@]}" --action check 2>&1 | tee "$console/check.log" || rc=$?
 if [ "$rc" = 0 ];then bash "$HERE/../urma_mode_parity/run.sh" "${args[@]}" --action run 2>&1 | tee "$console/run.log" || rc=$?;fi
 "$URMA_DIAG_PYTHON" "$HERE/../gpu_stage_recovery/collect.py" "$ROOT/$name" --node-rank "$RANK" --trace-build "$BUILD" --destination "$DEST/$name" || collected=$?
 [ "$rc" = 0 ] || return "$rc";return "$collected"
}
run_one r01_lane_mixed lane-mixed
run_one r01_writes_first writes-first
run_one r02_writes_first writes-first
run_one r02_lane_mixed lane-mixed
echo "WRITES_FIRST_BATCH_PASSED rank=$RANK destination=$DEST"
