#!/usr/bin/env bash
set -euo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
export URMA_DIAG_HOME=${URMA_DIAG_HOME:-/home/h00918771}
export VERBS_DIAG_HOME="$URMA_DIAG_HOME"
export VERBS_DIAG_PYTHON=${URMA_DIAG_PYTHON:-"$URMA_DIAG_HOME/metax_dev/venv-maca-py312/bin/python"}
export MACA_PATH=${MACA_PATH:-/opt/maca-3.9.0}

KERNEL=6.6.0-gdr_f00955680_diag
ACTION= RANK= JOB=verbs_same_kernel_fifo_01 MASTER=141.61.17.202 PORT=34220 HCA= IFACE= GID=3
TREE="$URMA_DIAG_HOME/metax_dev/verbs_same_kernel_fifo_src_v1"
BUILD="$URMA_DIAG_HOME/metax_dev/verbs_same_kernel_build/fifo_v1"
ROOT="$URMA_DIAG_HOME/metax_dev/verbs_same_kernel_fifo_results"
while [ "$#" -gt 0 ]; do
    case "$1" in
        --action) ACTION=${2:?};; --node-rank) RANK=${2:?};; --job) JOB=${2:?};;
        --master-addr) MASTER=${2:?};; --port) PORT=${2:?};; --ifname) IFACE=${2:?};;
        --hca) HCA=${2:?};; --gid-index) GID=${2:?};; --tree) TREE=${2:?};;
        --build-dir) BUILD=${2:?};; --output) ROOT=${2:?};;
        *) echo "unknown argument: $1" >&2; exit 2;;
    esac
    shift 2
done
[[ ( "$ACTION" == setup || "$ACTION" == run ) && ( "$RANK" == 0 || "$RANK" == 1 ) && "$JOB" =~ ^[a-zA-Z0-9_-]+$ ]] || exit 2
[[ "$PORT" =~ ^[1-9][0-9]{3,4}$ ]] && (( PORT >= 1024 && PORT <= 65530 )) || exit 2
[[ "$GID" =~ ^[0-9]+$ ]] || exit 2
[[ $(uname -r) == "$KERNEL" ]] || { echo "expected $KERNEL; running $(uname -r)" >&2; exit 2; }
if [ "$RANK" = 0 ]; then DEFAULT_HCA=rocep161s0f0; DEFAULT_IFACE=enp40s0
else DEFAULT_HCA=rocep40s0f0; DEFAULT_IFACE=enp34s0f5u1u4; fi
HCA=${HCA:-$DEFAULT_HCA}
IFACE=${IFACE:-${VERBS_DIAG_IFNAME:-$DEFAULT_IFACE}}
ROOT="$ROOT/$JOB"
DEST="$HERE/results/$JOB"
unset UCCL_IB_MAX_INFLIGHT_BYTES

if [ "$ACTION" = run ]; then
    for name in pre probe post; do
        if [[ -e "$ROOT/$name/node$RANK" || -e "$DEST/$name/node$RANK" ]]; then
            echo 'result exists; use a new --job on both nodes' >&2; exit 2
        fi
    done
fi
mkdir -p "$ROOT/setup/console/node$RANK"
setup_log=$(mktemp "$ROOT/setup/console/node$RANK/prepare.XXXXXX.log")
rc=0
{
    "$VERBS_DIAG_PYTHON" "$HERE/prepare.py" "$TREE" &&
    "$VERBS_DIAG_PYTHON" "$HERE/../verbs_baseline/on_diag_check.py" inventory --hca "$HCA" --gid-index "$GID"
} 2>&1 | tee "$setup_log" || rc=$?
"$VERBS_DIAG_PYTHON" "$HERE/../gpu_stage_recovery/collect.py" "$ROOT/setup" --node-rank "$RANK" --destination "$DEST/setup"
[ "$rc" = 0 ] || exit "$rc"
cp -- "$TREE/.verbs_fifo_probe.json" "$DEST/overlay_node$RANK.json"

run_one() {
    local action=$1 name=$2 capture=$3 port=$4 rc=0 collected=0
    local console="$ROOT/$name/console/node$RANK"
    mkdir -p "$console"
    if [ "$action" != run ]; then console=$(mktemp -d "$console/$action.XXXXXX"); fi
    local args=(--action "$action" --job "$name" --node-rank "$RANK" --master-addr "$MASTER" --port "$port"
        --ifname "$IFACE" --hca "$HCA" --gid-index "$GID" --expected-kernel "$KERNEL"
        --variant current --mode normal --build-dir "$BUILD" --output "$ROOT" --case-timeout 600
        --reference-profile boundaries)
    if [ "$action" = build ]; then
        args+=(--reference-events build --build-jobs 1)
    else
        args+=(--no-profile --repeats 1 --dispatch-focus --normal-tokens 4096 --warmups 5 --samples 10 --reference-events paced)
    fi
    VERBS_FIFO_CAPTURE="$capture" bash "$TREE/test/verbs_baseline/run.sh" "${args[@]}" 2>&1 | tee "$console/$action.log" || rc=$?
    if [ "$action" = build ]; then
        "$VERBS_DIAG_PYTHON" "$HERE/../gpu_stage_recovery/collect.py" "$ROOT/$name" --node-rank "$RANK" \
            --trace-build "$BUILD" --destination "$DEST/$name" || collected=$?
    else
        "$VERBS_DIAG_PYTHON" "$HERE/../gpu_stage_recovery/collect.py" "$ROOT/$name" --node-rank "$RANK" \
            --destination "$DEST/$name" || collected=$?
    fi
    [ "$rc" = 0 ] || return "$rc"
    return "$collected"
}
if [ "$ACTION" = setup ]; then
    run_one build build 0 "$PORT"
    run_one check check 0 "$PORT"
    echo "VERBS_FIFO_SETUP_PASSED rank=$RANK job=$JOB"
    exit 0
fi
run_one check check 0 "$PORT"
run_one run pre 0 "$PORT"
run_one run probe 1 "$((PORT + 2))"
run_one run post 0 "$((PORT + 4))"
echo "VERBS_FIFO_RUN_PASSED rank=$RANK destination=$DEST"
