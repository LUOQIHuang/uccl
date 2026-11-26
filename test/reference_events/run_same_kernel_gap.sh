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
VERBS_BUILD="$URMA_DIAG_HOME/metax_dev/verbs_same_kernel_build/current_v1"
URMA_BUILD="$URMA_DIAG_HOME/metax_dev/writes_first_batch_build/urma_v1"
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
    *) echo 'study must be gap or receive' >&2; exit 2;;
esac
[[ ( "$RANK" == 0 || "$RANK" == 1 ) && "$JOB" =~ ^[a-zA-Z0-9_-]+$ ]] || exit 2
[[ "$ACTION" == check || "$ACTION" == run ]] || exit 2
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
unset UCCL_IB_MAX_INFLIGHT_BYTES

if [ "$ACTION" = run ]; then
    for name in "${cases[@]}"; do
        if [[ -e "$ROOT/$name/node$RANK" || -e "$DEST/$name/node$RANK" ]]; then
            echo 'result exists; choose a new --job on both nodes' >&2; exit 2
        fi
    done
fi

run_one() {
    local action=$1 name=$2 backend=$3 plan=$4 port=$5 cadence=${6:-paced} rc=0 collected=0
    local console="$ROOT/$name/console/node$RANK" entry build
    local build_record=()
    mkdir -p -- "$console"
    if [ "$action" = check ]; then console=$(mktemp -d "$console/check.XXXXXX"); fi
    local args=(--action "$action" --job "$name" --node-rank "$RANK" --master-addr "$MASTER"
        --port "$port" --ifname "$IFACE" --output "$ROOT" --expected-kernel "$KERNEL"
        --mode normal --normal-tokens 4096 --warmups 5 --samples 10 --case-timeout 600
        --dispatch-focus --reference-events "$cadence" "${profile[@]}")
    if [ "$backend" = verbs ]; then
        entry="$HERE/../verbs_baseline/run.sh"; build=$VERBS_BUILD
        args+=(--variant current --no-profile --repeats 1 --hca "$HCA" --gid-index "$GID")
    else
        entry="$HERE/../urma_mode_parity/run.sh"; build=$URMA_BUILD
        args+=(--backend urma --phase-suite --api-only --urma-device "$DEVICE" --urma-device-plan 202c1-204c0
            --gpu-pci "${URMA_DIAG_GPU_PCI:-0000:21:00.0}" --atomic-port auto
            --normal-fadd-fence-plan "$plan" --require-normal-atomic-roles --require-normal-retire
            --normal-window-plan default --normal-retire-plan ordered --normal-completion all
            --normal-submit-schedule lane-mixed)
    fi
    if [ "$action" = run ]; then build_record=(--trace-build "$build"); fi
    bash "$entry" "${args[@]}" --build-dir "$build" 2>&1 | tee "$console/$action.log" || rc=$?
    "$URMA_DIAG_PYTHON" "$HERE/../gpu_stage_recovery/collect.py" "$ROOT/$name" \
        --node-rank "$RANK" "${build_record[@]}" --destination "$DEST/$name" || collected=$?
    [ "$rc" = 0 ] || return "$rc"
    return "$collected"
}

run_one check check_verbs verbs default "$PORT"
if [ "$STUDY" = gap ]; then run_one check check_urma_all_on urma all-on "$PORT"; fi
run_one check check_urma_tail_off urma peer-tail-off "$PORT"
if [ "$ACTION" = check ]; then
    echo "SAME_KERNEL_GAP_CHECK_PASSED rank=$RANK job=$JOB"
    exit 0
fi

if [ "$STUDY" = receive ]; then
    for i in "${!cases[@]}"; do
        name=${cases[i]}
        backend=verbs; plan=default; cadence=paced
        if [[ "$name" == urma_* ]]; then backend=urma; plan=peer-tail-off; fi
        if [[ "$name" == *_probe ]]; then cadence=on; fi
        run_one run "$name" "$backend" "$plan" "$((PORT + 2*i))" "$cadence"
    done
    echo "SAME_KERNEL_RECEIVE_RUN_PASSED rank=$RANK destination=$DEST"
    exit 0
fi

run_one run "${cases[0]}" verbs default "$PORT"
run_one run "${cases[1]}" urma all-on "$((PORT + 2))"
run_one run "${cases[2]}" urma peer-tail-off "$((PORT + 4))"
run_one run "${cases[3]}" urma peer-tail-off "$((PORT + 6))"
run_one run "${cases[4]}" urma all-on "$((PORT + 8))"
run_one run "${cases[5]}" verbs default "$((PORT + 10))"
echo "SAME_KERNEL_GAP_RUN_PASSED rank=$RANK destination=$DEST"
