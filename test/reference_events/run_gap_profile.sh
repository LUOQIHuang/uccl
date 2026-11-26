#!/usr/bin/env bash
# One required matched-stage campaign; no optimization parameter scan.
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
RANK= BACKEND=urma ACTION=run JOB=normal_gap_gpu_01 MASTER=141.61.17.202 PORT=33980 GID=3 BUILD=
ROOT="$URMA_DIAG_HOME/metax_dev/normal_gap_gpu_results"
DEST="$HERE/results/normal_gap_gpu_01"
while [ "$#" -gt 0 ];do
 case "$1" in
 --node-rank) RANK=${2:?};shift 2;; --backend) BACKEND=${2:?};shift 2;;
 --action) ACTION=${2:?};shift 2;; --job) JOB=${2:?};shift 2;;
 --build-dir) BUILD=${2:?};shift 2;; --output) ROOT=${2:?};shift 2;;
 --destination) DEST=${2:?};shift 2;; --master-addr) MASTER=${2:?};shift 2;;
 --port) PORT=${2:?};shift 2;; --gid-index) GID=${2:?};shift 2;;
 *) echo "unknown argument $1" >&2;exit 2;;esac
done
[[ "$RANK" == 0 || "$RANK" == 1 ]] && [[ "$BACKEND" == urma || "$BACKEND" == verbs ]] && [[ "$JOB" =~ ^[a-zA-Z0-9_-]+$ ]] || exit 2
[[ "$ACTION" == build || "$ACTION" == run || "$ACTION" == collect ]] || exit 2
[[ "$PORT" =~ ^[0-9]+$ ]] && ((PORT>=1024 && PORT<65534)) || exit 2
if [ -z "$BUILD" ];then
 if [ "$BACKEND" = urma ];then BUILD="$URMA_DIAG_HOME/metax_dev/writes_first_batch_build/urma_v1"
 else BUILD="$URMA_DIAG_HOME/metax_dev/normal_gap_gpu_build/verbs_v1";fi
fi
PREFIX="${JOB}_${BACKEND}"
collect_one(){
 "$URMA_DIAG_PYTHON" "$HERE/../gpu_stage_recovery/collect.py" "$ROOT/$1" --node-rank "$RANK" --trace-build "$BUILD" --destination "$DEST/$BACKEND/$1"
}
if [ "$ACTION" = collect ];then
 for tag in build batch_pre paced trace post batch_post;do [ ! -d "$ROOT/${PREFIX}_${tag}" ] || collect_one "${PREFIX}_${tag}";done
 exit 0
fi
if [ "$ACTION" = build ];then
 [ "$BACKEND" = verbs ] || { echo 'URMA reuses the validated writes_first_batch module; no rebuild for this campaign' >&2;exit 2; }
 rc=0;collected=0
 bash "$HERE/run.sh" --backend verbs --action build --step trace --reference-profile boundaries --node-rank "$RANK" \
  --job "${PREFIX}_build" --build-dir "$BUILD" --output "$ROOT" --gid-index "$GID" || rc=$?
 collect_one "${PREFIX}_build" || collected=$?
 [ "$rc" != 0 ] || rc=$collected
 exit "$rc"
fi
for tag in batch_pre paced trace post batch_post;do
 [ ! -e "$ROOT/${PREFIX}_${tag}/node$RANK" ] && [ ! -e "$DEST/$BACKEND/${PREFIX}_${tag}/node$RANK" ] || { echo 'result exists; use a new job/destination' >&2;exit 2; }
done
if [ "$RANK" = 0 ];then DEVICE=udmac1d1e2;IFACE=enp40s0
else DEVICE=udmac0d1e2;IFACE=enp34s0f5u1u4;fi
if [ "$BACKEND" = urma ];then
 ENTRY="$HERE/../urma_mode_parity/run.sh"
 EXTRA=(--backend urma --phase-suite --api-only --normal-fadd-fence-plan peer-tail-off --require-normal-atomic-roles --require-normal-retire
  --urma-device "$DEVICE" --urma-device-plan 202c1-204c0 --gpu-pci "${URMA_DIAG_GPU_PCI:-0000:21:00.0}" --atomic-port auto --expected-kernel 6.6.0-gdr_f00955680_diag)
 IFACE=${URMA_DIAG_IFNAME:-$IFACE}
else
 ENTRY="$HERE/../verbs_baseline/run.sh"
 EXTRA=(--variant current --no-profile --repeats 1 --gid-index "$GID" --expected-kernel 6.6.0-145.3.34.165.oe2403sp3.aarch64)
 IFACE=${VERBS_DIAG_IFNAME:-$IFACE}
fi
CURRENT=
on_exit(){ local saved=$?;if [ -n "$CURRENT" ];then collect_one "$CURRENT" || true;fi;return "$saved"; }
trap on_exit EXIT
run_one(){
 local tag=$1 cadence=$2 name="${PREFIX}_${1}" rc=0 collected=0
 CURRENT=$name
 local console="$ROOT/$name/console/node$RANK";mkdir -p "$console"
 local args=("${EXTRA[@]}" --mode normal --dispatch-focus --reference-profile boundaries --reference-events "$cadence" --reference-gpu-only
  --normal-tokens 4096 --warmups 5 --samples 10 --node-rank "$RANK" --master-addr "$MASTER" --port "$PORT" --ifname "$IFACE"
  --case-timeout 600 --build-dir "$BUILD" --output "$ROOT" --job "$name")
 bash "$ENTRY" "${args[@]}" --action check 2>&1 | tee "$console/check.log" || rc=$?
 if [ "$rc" = 0 ];then bash "$ENTRY" "${args[@]}" --action run 2>&1 | tee "$console/run.log" || rc=$?;fi
 collect_one "$name" || collected=$?;CURRENT=
 [ "$rc" = 0 ] || return "$rc";return "$collected"
}
run_one batch_pre batch
run_one paced paced
run_one trace on
run_one post paced
run_one batch_post batch
echo "NORMAL_GAP_PROFILE_PASSED backend=$BACKEND node=$RANK destination=$DEST/$BACKEND"
