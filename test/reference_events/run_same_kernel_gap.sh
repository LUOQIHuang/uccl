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
RANK= ACTION=check STUDY=gap JOB= MASTER=141.61.17.202 PORT= GID=3 HCA= IFACE=
VERBS_BUILD= URMA_BUILD= STREAM_TREE=
ROOT="$URMA_DIAG_HOME/metax_dev/same_kernel_gap_results"
while [ "$#" -gt 0 ]; do
    case "$1" in
        --node-rank) RANK=${2:?};;
        --action) ACTION=${2:?};;
        --study) STUDY=${2:?};;
        --job) JOB=${2:?};;
        --master-addr) MASTER=${2:?};;
        --port) PORT=${2:?};;
        --gid-index) GID=${2:?};;
        --hca) HCA=${2:?};;
        --ifname) IFACE=${2:?};;
        --verbs-build) VERBS_BUILD=${2:?};;
        --urma-build) URMA_BUILD=${2:?};;
        --stream-tree) STREAM_TREE=${2:?};;
        --output) ROOT=${2:?};;
        *) echo "unknown argument: $1" >&2; exit 2;;
    esac
    shift 2
done
case "$STUDY" in
    gap)
        JOB=${JOB:-normal_same_kernel_gap_01}; PORT=${PORT:-34180}
        cases=(rdma_pre urma_all_on_a1 urma_tail_off_b1 urma_tail_off_b2 urma_all_on_a2 rdma_post)
        profile=(--reference-profile legacy)
        ;;
    receive)
        JOB=${JOB:-normal_same_kernel_receive_01}; PORT=${PORT:-34280}
        cases=(rdma_pre rdma_probe rdma_post urma_pre urma_probe urma_post)
        profile=(--reference-profile boundaries --reference-gpu-only)
        ;;
    reverse)
        JOB=${JOB:-normal_reverse_load_02}; PORT=${PORT:-34360}
        cases=(rdma_pre rdma_half rdma_post urma_pre urma_half urma_post)
        profile=(--reference-profile legacy)
        ;;
    stream|waits)
        if [ "$STUDY" = waits ]; then
            PORT=${PORT:-34340}; JOB=${JOB:-normal_same_kernel_waits_02}; variant=waits_v2
        else PORT=${PORT:-34320}; JOB=${JOB:-normal_same_kernel_stream_01}; variant=stream; fi
        cases=(rdma_pre rdma_probe rdma_post urma_pre urma_probe urma_post)
        profile=(--reference-profile legacy)
        STREAM_TREE=${STREAM_TREE:-"$URMA_DIAG_HOME/metax_dev/normal_${variant}_src_v1"}
        VERBS_BUILD=${VERBS_BUILD:-"$URMA_DIAG_HOME/metax_dev/normal_${variant}_build/verbs_v1"}
        URMA_BUILD=${URMA_BUILD:-"$URMA_DIAG_HOME/metax_dev/normal_${variant}_build/urma_v1"}
        ;;
    *) echo 'study must be gap, receive, reverse, stream or waits' >&2; exit 2;;
esac
[[ ( "$RANK" == 0 || "$RANK" == 1 ) && "$JOB" =~ ^[a-zA-Z0-9_-]+$ ]] || exit 2
[[ "$ACTION" == check || "$ACTION" == run || ( "$ACTION" == setup && ( "$STUDY" == stream || "$STUDY" == waits ) ) ]] || exit 2
[[ "$PORT" =~ ^[1-9][0-9]{3,4}$ ]] && (( PORT >= 1024 && PORT + 11 <= 65535 )) || exit 2
[[ "$GID" =~ ^[0-9]+$ ]] || exit 2
[[ $(uname -r) == "$KERNEL" ]] || { echo "expected $KERNEL; running $(uname -r)" >&2; exit 2; }
if [ "$RANK" = 0 ]; then
    DEFAULT_HCA=rocep161s0f0; DEFAULT_IFACE=enp40s0; DEVICE=udmac1d1e2
else
    DEFAULT_HCA=rocep40s0f0; DEFAULT_IFACE=enp34s0f5u1u4; DEVICE=udmac0d1e2
fi
HCA=${HCA:-$DEFAULT_HCA}
IFACE=${IFACE:-${URMA_DIAG_IFNAME:-$DEFAULT_IFACE}}
DEST="$HERE/results/$JOB"
ROOT="$ROOT/$JOB"
SOURCE="$HERE/.."
VERBS_BUILD=${VERBS_BUILD:-"$URMA_DIAG_HOME/metax_dev/verbs_same_kernel_build/current_v1"}
URMA_BUILD=${URMA_BUILD:-"$URMA_DIAG_HOME/metax_dev/writes_first_batch_build/urma_v1"}
unset UCCL_IB_MAX_INFLIGHT_BYTES NORMAL_STREAM_TIMING NORMAL_RECEIVER_WAITS NORMAL_REVERSE_LOAD

if [ "$ACTION" = run ]; then
    for name in "${cases[@]}"; do
        if [[ -e "$ROOT/$name/node$RANK" || -e "$DEST/$name/node$RANK" ]]; then
            echo 'result exists; choose a new --job on both nodes' >&2; exit 2
        fi
    done
fi

if [[ "$STUDY" == stream || "$STUDY" == waits ]]; then
    console="$ROOT/setup/console/node$RANK"; mkdir -p "$console"
    prep_log=$(mktemp "$console/prepare.XXXXXX.log")
    rc=0
    overlay_args=("$STREAM_TREE")
    if [ "$STUDY" = waits ]; then overlay_args+=(--receiver-waits); fi
    "$URMA_DIAG_PYTHON" "$HERE/prepare_stream_timing.py" "${overlay_args[@]}" 2>&1 | tee "$prep_log" || rc=$?
    "$URMA_DIAG_PYTHON" "$HERE/../gpu_stage_recovery/collect.py" "$ROOT/setup" \
        --node-rank "$RANK" --destination "$DEST/setup"
    [ "$rc" = 0 ] || exit "$rc"
    cp -- "$STREAM_TREE/.normal_stream_timing.json" "$DEST/overlay_node$RANK.json"
    SOURCE="$STREAM_TREE/test"
fi

run_one() {
    local action=$1 name=$2 backend=$3 plan=$4 port=$5 cadence=${6:-paced} rc=0 collected=0
    local console="$ROOT/$name/console/node$RANK" entry build
    local build_record=() launch=(bash)
    mkdir -p -- "$console"
    if [ "$action" = check ]; then console=$(mktemp -d "$console/check.XXXXXX"); fi
    local args=(--action "$action" --job "$name" --node-rank "$RANK" --master-addr "$MASTER"
        --port "$port" --ifname "$IFACE" --output "$ROOT" --expected-kernel "$KERNEL"
        --mode normal --normal-tokens 4096 --warmups 5 --samples 10 --case-timeout 600 "${profile[@]}")
    if [ "$backend" = verbs ]; then
        entry="$SOURCE/verbs_baseline/run.sh"; build=$VERBS_BUILD
        args+=(--variant current --hca "$HCA" --gid-index "$GID")
    else
        entry="$SOURCE/urma_mode_parity/run.sh"; build=$URMA_BUILD
        args+=(--backend urma)
    fi
    if [ "$action" = build ]; then
        args+=(--reference-events build --build-jobs 1)
    else
        args+=(--dispatch-focus --reference-events "$cadence")
        if [ "$backend" = verbs ]; then args+=(--no-profile --repeats 1)
        else args+=(--phase-suite --api-only --urma-device "$DEVICE" --urma-device-plan 202c1-204c0
            --gpu-pci "${URMA_DIAG_GPU_PCI:-0000:21:00.0}" --atomic-port auto
            --normal-fadd-fence-plan "$plan" --require-normal-atomic-roles --require-normal-retire
            --normal-window-plan default --normal-retire-plan ordered --normal-completion all
            --normal-submit-schedule lane-mixed)
        fi
    fi
    if [ "$action" != check ]; then build_record=(--trace-build "$build"); fi
    if [[ ( "$STUDY" == stream || "$STUDY" == waits ) && "$action" == run && "$RANK" == 0 && "$name" == *_probe ]]; then
        if [ "$STUDY" = waits ]; then launch=(env NORMAL_RECEIVER_WAITS=1 bash)
        else launch=(env NORMAL_STREAM_TIMING=1 bash); fi
    fi
    if [[ "$STUDY" == reverse && "$action" == run ]]; then
        if [[ "$name" == *_half ]]; then launch=(env NORMAL_REVERSE_LOAD=half_reverse bash)
        else launch=(env NORMAL_REVERSE_LOAD=both bash); fi
    fi
    "${launch[@]}" "$entry" "${args[@]}" --build-dir "$build" 2>&1 | tee "$console/$action.log" || rc=$?
    "$URMA_DIAG_PYTHON" "$HERE/../gpu_stage_recovery/collect.py" "$ROOT/$name" \
        --node-rank "$RANK" "${build_record[@]}" --destination "$DEST/$name" || collected=$?
    [ "$rc" = 0 ] || return "$rc"
    return "$collected"
}

if [ "$ACTION" = setup ]; then
    run_one build build_verbs verbs default "$PORT"
    run_one build build_urma urma default "$PORT"
fi
run_one check check_verbs verbs default "$PORT"
if [ "$STUDY" = gap ]; then run_one check check_urma_all_on urma all-on "$PORT"; fi
run_one check check_urma_tail_off urma peer-tail-off "$PORT"
if [ "$ACTION" != run ]; then
    echo "SAME_KERNEL_${STUDY^^}_${ACTION^^}_PASSED rank=$RANK job=$JOB"
    exit 0
fi

if [ "$STUDY" != gap ]; then
    for i in "${!cases[@]}"; do
        name=${cases[i]}
        backend=verbs; plan=default; cadence=paced
        if [[ "$name" == urma_* ]]; then backend=urma; plan=peer-tail-off; fi
        if [[ "$STUDY" == receive && "$name" == *_probe ]]; then cadence=on; fi
        run_one run "$name" "$backend" "$plan" "$((PORT + 2*i))" "$cadence"
    done
    echo "SAME_KERNEL_${STUDY^^}_RUN_PASSED rank=$RANK destination=$DEST"
    exit 0
fi

run_one run "${cases[0]}" verbs default "$PORT"
run_one run "${cases[1]}" urma all-on "$((PORT + 2))"
run_one run "${cases[2]}" urma peer-tail-off "$((PORT + 4))"
run_one run "${cases[3]}" urma peer-tail-off "$((PORT + 6))"
run_one run "${cases[4]}" urma all-on "$((PORT + 8))"
run_one run "${cases[5]}" verbs default "$((PORT + 10))"
echo "SAME_KERNEL_GAP_RUN_PASSED rank=$RANK destination=$DEST"
