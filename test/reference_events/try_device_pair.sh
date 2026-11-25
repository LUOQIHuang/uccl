#!/usr/bin/env bash
# One fixed device intervention; retain the previous workload and fence plan.
set -euo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
RANK= JOB=urma_c1_c0_01 PORT=33480 PART=connect
export URMA_DIAG_HOME=${URMA_DIAG_HOME:-/home/h00918771}
export VERBS_DIAG_HOME="$URMA_DIAG_HOME"
export URMA_DIAG_PYTHON=${URMA_DIAG_PYTHON:-"$URMA_DIAG_HOME/metax_dev/venv-maca-py312/bin/python"}
export VERBS_DIAG_PYTHON="$URMA_DIAG_PYTHON"
export MACA_PATH=${MACA_PATH:-/opt/maca-3.9.0}
export URMA_DIAG_LIBRARY_DIR=${URMA_DIAG_LIBRARY_DIR:-"$URMA_DIAG_HOME/metax_dev/urma_after_kernel_20261029/diag/umdk_private_618957c3/lib"}
BUILD="$URMA_DIAG_HOME/metax_dev/normal_scope_build/urma_v1"
ROOT="$URMA_DIAG_HOME/metax_dev/normal_scope_results"
DEST="$HERE/results/urma_device_pair_01"
while [ "$#" -gt 0 ];do
 case "$1" in
 --node-rank) RANK=${2:?};shift 2;; --job) JOB=${2:?};shift 2;;
 --build-dir) BUILD=${2:?};shift 2;; --output) ROOT=${2:?};shift 2;;
 --destination) DEST=${2:?};shift 2;; --port) PORT=${2:?};shift 2;;
 --part) PART=${2:?};shift 2;;
 *) echo "unknown argument $1" >&2;exit 2;;
 esac
done
[[ "$RANK" == 0 || "$RANK" == 1 ]] || { echo '--node-rank 0 or 1 required' >&2;exit 2; }
[[ "$PART" == connect || "$PART" == smoke || "$PART" == coverage ]] || { echo 'part must be connect/smoke/coverage' >&2;exit 2; }
if [ "$RANK" = 0 ];then DEVICE=udmac1d1e2;IFACE=enp40s0
else DEVICE=udmac0d1e2;IFACE=enp34s0f5u1u4;fi
IFACE=${URMA_DIAG_IFNAME:-$IFACE}
echo "DEVICE_PAIR node=$RANK device=$DEVICE job=$JOB"
if [ "$PART" != connect ];then
 exec bash "$HERE/run_boundaries.sh" --backend urma --part "$PART" \
  --node-rank "$RANK" --master-addr 141.61.17.202 --port "$PORT" \
  --normal-fadd-fence-plan peer-tail-off --ifname "$IFACE" \
  --gpu-pci "${URMA_DIAG_GPU_PCI:-0000:21:00.0}" --atomic-port auto \
  --urma-device "$DEVICE" --urma-device-plan 202c1-204c0 \
  --build-dir "$BUILD" --job "$JOB" --output "$ROOT" --destination "$DEST"
fi
exec bash "$HERE/recover.sh" --step paced --reference-profile boundaries \
 --normal-fadd-fence-plan peer-tail-off --node-rank "$RANK" \
 --master-addr 141.61.17.202 --port "$PORT" --ifname "$IFACE" \
 --gpu-pci "${URMA_DIAG_GPU_PCI:-0000:21:00.0}" --atomic-port auto --case-timeout 300 \
 --urma-device "$DEVICE" --urma-device-plan 202c1-204c0 \
 --build-dir "$BUILD" --job "$JOB" --output "$ROOT" --destination "$DEST"
