#!/usr/bin/env bash
set -euo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
export URMA_DIAG_HOME=${URMA_DIAG_HOME:-/home/h00918771}
export URMA_DIAG_PYTHON=${URMA_DIAG_PYTHON:-"$URMA_DIAG_HOME/metax_dev/venv-maca-py312/bin/python"}
export VERBS_DIAG_HOME="$URMA_DIAG_HOME" VERBS_DIAG_PYTHON="$URMA_DIAG_PYTHON"
export MACA_PATH=${MACA_PATH:-/opt/maca-3.9.0}
export URMA_DIAG_LIBRARY_DIR=${URMA_DIAG_LIBRARY_DIR:-"$URMA_DIAG_HOME/metax_dev/urma_after_kernel_20261029/diag/umdk_private_618957c3/lib"}
export VERBS_DIAG_LIBRARY_DIR="$URMA_DIAG_LIBRARY_DIR"
export URMA_DIAG_INCLUDE_DIR=${URMA_DIAG_INCLUDE_DIR:-"$URMA_DIAG_HOME/metax_dev/urma_sdk_private_include"}

KERNEL=6.6.0-gdr_f00955680_diag
ACTION= RANK= JOB=normal_chunk6_gate_01 MASTER=141.61.17.202 PORT=34300 IFACE=
TREE="$URMA_DIAG_HOME/metax_dev/normal_chunk6_gate_src_v1"
BUILD="$URMA_DIAG_HOME/metax_dev/normal_chunk6_gate_build/urma_v1"
ROOT="$URMA_DIAG_HOME/metax_dev/normal_chunk6_gate_results"
while [ "$#" -gt 0 ]; do
    case "$1" in
        --action) ACTION=${2:?};; --node-rank) RANK=${2:?};; --job) JOB=${2:?};;
        --master-addr) MASTER=${2:?};; --port) PORT=${2:?};; --ifname) IFACE=${2:?};;
        --tree) TREE=${2:?};; --build-dir) BUILD=${2:?};; --output) ROOT=${2:?};;
        *) echo "unknown argument: $1" >&2; exit 2;;
    esac
    shift 2
done
[[ ( "$ACTION" == setup || "$ACTION" == run ) && ( "$RANK" == 0 || "$RANK" == 1 ) && "$JOB" =~ ^[a-zA-Z0-9_-]+$ ]] || exit 2
[[ "$PORT" =~ ^[1-9][0-9]{3,4}$ ]] && (( PORT >= 1024 && PORT + 7 <= 65535 )) || exit 2
[[ $(uname -r) == "$KERNEL" ]] || { echo "expected $KERNEL; running $(uname -r)" >&2; exit 2; }
if [ "$RANK" = 0 ]; then DEVICE=udmac1d1e2; DEFAULT_IFACE=enp40s0
else DEVICE=udmac0d1e2; DEFAULT_IFACE=enp34s0f5u1u4; fi
IFACE=${IFACE:-${URMA_DIAG_IFNAME:-$DEFAULT_IFACE}}
ROOT="$ROOT/$JOB"
DEST="$HERE/results/$JOB"
unset NORMAL_CHUNK_GATE_US UCCL_IB_MAX_INFLIGHT_BYTES

if [ "$ACTION" = run ]; then
    for name in a1 b1 b2 a2; do
        if [[ -e "$ROOT/$name/node$RANK" || -e "$DEST/$name/node$RANK" ]]; then
            echo 'result exists; use a new --job on both nodes' >&2; exit 2
        fi
    done
fi
mkdir -p "$ROOT/setup/console/node$RANK"
setup_log=$(mktemp "$ROOT/setup/console/node$RANK/prepare.XXXXXX.log")
rc=0
"$URMA_DIAG_PYTHON" "$HERE/prepare.py" "$TREE" 2>&1 | tee "$setup_log" || rc=$?
"$URMA_DIAG_PYTHON" "$HERE/../gpu_stage_recovery/collect.py" "$ROOT/setup" --node-rank "$RANK" --destination "$DEST/setup"
[ "$rc" = 0 ] || exit "$rc"
cp -- "$TREE/.normal_chunk_gate.json" "$DEST/overlay_node$RANK.json"

run_one() {
    local action=$1 name=$2 delay=$3 port=$4 rc=0 collected=0
    local console="$ROOT/$name/console/node$RANK"
    local build_record=() launch=(bash)
    mkdir -p "$console"
    if [ "$action" != run ]; then console=$(mktemp -d "$console/$action.XXXXXX"); fi
    local args=(--action "$action" --job "$name" --node-rank "$RANK" --master-addr "$MASTER" --port "$port"
        --backend urma --mode normal --build-dir "$BUILD" --output "$ROOT" --reference-profile legacy)
    if [ "$action" = build ]; then
        args+=(--reference-events build --build-jobs 1)
        build_record=(--trace-build "$BUILD")
    else
        args+=(--phase-suite --api-only --dispatch-focus --reference-events paced --normal-tokens 4096 --warmups 5 --samples 10
            --expected-kernel "$KERNEL" --ifname "$IFACE" --case-timeout 600
            --urma-device "$DEVICE" --urma-device-plan 202c1-204c0 --gpu-pci "${URMA_DIAG_GPU_PCI:-0000:21:00.0}" --atomic-port auto
            --normal-fadd-fence-plan peer-tail-off --require-normal-atomic-roles --require-normal-retire
            --normal-window-plan default --normal-retire-plan ordered --normal-completion all --normal-submit-schedule lane-mixed)
        if [[ "$action" == run && "$RANK" == 1 ]]; then launch=(env "NORMAL_CHUNK_GATE_US=$delay" bash); fi
    fi
    "${launch[@]}" "$TREE/test/urma_mode_parity/run.sh" "${args[@]}" 2>&1 | tee "$console/$action.log" || rc=$?
    "$URMA_DIAG_PYTHON" "$HERE/../gpu_stage_recovery/collect.py" "$ROOT/$name" --node-rank "$RANK" \
        "${build_record[@]}" --destination "$DEST/$name" || collected=$?
    [ "$rc" = 0 ] || return "$rc"
    return "$collected"
}
if [ "$ACTION" = setup ]; then
    run_one build build 0 "$PORT"
    run_one check check 0 "$PORT"
    echo "NORMAL_CHUNK_GATE_SETUP_PASSED rank=$RANK job=$JOB"
    exit 0
fi
run_one check check 0 "$PORT"
run_one run a1 0 "$PORT"
run_one run b1 300 "$((PORT + 2))"
run_one run b2 300 "$((PORT + 4))"
run_one run a2 0 "$((PORT + 6))"
echo "NORMAL_CHUNK_GATE_RUN_PASSED rank=$RANK destination=$DEST"
