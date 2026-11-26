#!/usr/bin/env bash
# Short native-verbs acceptance on the current URMA/diag kernel.
set -euo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
export URMA_DIAG_HOME=${URMA_DIAG_HOME:-/home/h00918771}
export URMA_DIAG_PYTHON=${URMA_DIAG_PYTHON:-"$URMA_DIAG_HOME/metax_dev/venv-maca-py312/bin/python"}
export VERBS_DIAG_HOME="$URMA_DIAG_HOME" VERBS_DIAG_PYTHON="$URMA_DIAG_PYTHON"
export MACA_PATH=${MACA_PATH:-/opt/maca-3.9.0}
KERNEL=6.6.0-gdr_f00955680_diag
ACTION= RANK= JOB=verbs_same_kernel_01 HCA=rocep40s0f0 GID=3 IFACE=
MASTER=141.61.17.202 PORT=34080
ROOT="$URMA_DIAG_HOME/metax_dev/verbs_same_kernel_results"
BUILD="$URMA_DIAG_HOME/metax_dev/verbs_same_kernel_build/current_v1"
while [ "$#" -gt 0 ]; do
  case "$1" in
    --action) ACTION=${2:?}; shift 2;; --node-rank) RANK=${2:?}; shift 2;;
    --job) JOB=${2:?}; shift 2;; --build-dir) BUILD=${2:?}; shift 2;;
    --output) ROOT=${2:?}; shift 2;; --master-addr) MASTER=${2:?}; shift 2;;
    --port) PORT=${2:?}; shift 2;; --hca) HCA=${2:?}; shift 2;;
    --gid-index) GID=${2:?}; shift 2;; --ifname) IFACE=${2:?}; shift 2;;
    *) echo "unknown argument $1" >&2; exit 2;;
  esac
done
[[ "$RANK" == 0 || "$RANK" == 1 ]] && [[ "$JOB" =~ ^[a-zA-Z0-9_-]+$ ]] &&
  [[ "$HCA" =~ ^[a-zA-Z0-9_.-]+$ && "$GID" =~ ^[0-9]+$ ]] || exit 2
[[ "$PORT" =~ ^[0-9]+$ ]] && ((PORT>=1024 && PORT<65534)) || exit 2
case "$ACTION" in inventory|build|check|smoke) ;; *) exit 2;; esac
[[ $(uname -r) == "$KERNEL" ]] || { echo "expected $KERNEL; running $(uname -r)" >&2; exit 2; }
if [ -z "$IFACE" ]; then
  if [ "$RANK" = 0 ]; then IFACE=enp40s0; else IFACE=enp34s0f5u1u4; fi
  IFACE=${VERBS_DIAG_IFNAME:-$IFACE}
fi
CONSOLE="$ROOT/$JOB/console/node$RANK"
DEST="$HERE/../reference_events/results/$JOB"
if [ "$ACTION" = smoke ] && { [ -e "$ROOT/$JOB/node$RANK" ] || [ -e "$DEST/node$RANK" ]; }; then
  echo 'smoke result exists; use a new job' >&2; exit 2
fi
mkdir -p "$CONSOLE"
CONSOLE=$(mktemp -d "$CONSOLE/$ACTION.XXXXXX")
collect_on_exit(){
  local original=$? collect_status=0
  "$VERBS_DIAG_PYTHON" "$HERE/../gpu_stage_recovery/collect.py" "$ROOT/$JOB" \
    --node-rank "$RANK" --trace-build "$BUILD" --destination "$DEST" || collect_status=$?
  if [ "$original" = 0 ]; then return "$collect_status"; fi
  return "$original"
}
trap 'collect_on_exit || exit $?' EXIT
ARGS=(--variant current --mode normal --build-dir "$BUILD" --build-jobs 1 --output "$ROOT"
  --job "$JOB" --node-rank "$RANK" --master-addr "$MASTER" --port "$PORT" --hca "$HCA"
  --gid-index "$GID" --ifname "$IFACE" --expected-kernel "$KERNEL" --case-timeout 300)

if [ "$ACTION" = build ]; then
  bash "$HERE/run.sh" "${ARGS[@]}" --reference-events build --action build 2>&1 | tee "$CONSOLE/build.log"
  exit 0
fi
{
  uname -r
  cat /proc/sys/kernel/random/boot_id
  "$VERBS_DIAG_PYTHON" "$HERE/on_diag_check.py" inventory --hca "$HCA" --gid-index "$GID"
  ibv_devinfo -d "$HCA" -i 1 -v
} 2>&1 | tee "$CONSOLE/inventory.log"
if [ "$ACTION" = inventory ]; then exit 0; fi

SMOKE=(--no-profile --repeats 1 --normal-tokens 128 --warmups 1 --samples 3
  --dispatch-focus --reference-events on --reference-profile legacy)
if [ "$ACTION" = check ]; then
  bash "$HERE/../reference_events/check_blockers_gpu.sh" 2>&1 | tee "$CONSOLE/gpu_check.log"
  bash "$HERE/run.sh" "${ARGS[@]}" "${SMOKE[@]}" --action check 2>&1 | tee "$CONSOLE/check.log"
  exit 0
fi
bash "$HERE/run.sh" "${ARGS[@]}" "${SMOKE[@]}" --action run 2>&1 | tee "$CONSOLE/smoke.log"
"$VERBS_DIAG_PYTHON" "$HERE/on_diag_check.py" verify --hca "$HCA" --gid-index "$GID" \
  --node "$ROOT/$JOB/node$RANK" | tee "$CONSOLE/path_proof.log"
