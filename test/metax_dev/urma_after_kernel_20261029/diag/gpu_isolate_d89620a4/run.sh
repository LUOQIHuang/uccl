#!/usr/bin/env bash
set -u
ulimit -c 0
export LC_ALL=C
base=/home/f00955680/metax_dev/urma_after_kernel_20261029/diag
stage="$base/gpu_isolate_d89620a4"
module="$base/ep_private_b73a94c1/module"
priv="$base/umdk_private_618957c3/lib"
boot=$(cat /proc/sys/kernel/random/boot_id)
printf 'HOST=%s KERNEL=%s BOOT_ID=%s\n' "$(hostname -s)" "$(uname -r)" "$boot"
[ "$(hostname -s)" = worker1 ] && [ "$(uname -r)" = 6.6.0-gdr_f00955680_diag ] || exit 71
[ "$boot" != e640f58e-cdff-4f22-88ce-a405ab858e3e ] || { echo STOP_NEED_NEW_BOOT; exit 72; }
[ -e /sys/class/uburma/udmac0d1e2 ] || { echo STOP_URMA_DEVICE_NOT_READY; exit 73; }
run="$stage/runs/$boot"
[ ! -e "$run" ] || { echo STOP_RUN_ALREADY_EXISTS; exit 74; }
if ss -ltn 2>/dev/null | awk '$4 ~ /:29863$/ {found=1} END {exit !found}'; then echo STOP_PORT_IN_USE; exit 75; fi
mkdir -p "$run/tmp" "$run/cache" "$run/python/uccl" || exit 76
printf '%s  %s\n' '14f1b7b3c7734cfe8609473ecc96bb3478cc7c224bc54027922f75523b7dec10' "$module/ep.abi3.so" | sha256sum -c - || exit 77
source /home/f00955680/metax_dev/activate-uccl.sh || exit 78
unset UMDK_HOME UCCL_PROJECT_DIAG UCCL_EP_VERBS_REG UCCL_IB_HCA
export LD_LIBRARY_PATH="$priv${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export CUDA_VISIBLE_DEVICES=0 PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
export UCCL_EP_TRANSPORT=urma UCCL_URMA_TM=rc UCCL_URMA_DEV_NAME=udmac0d1e2
export MCCL_IB_DISABLE=1 MCCL_SOCKET_IFNAME=enp33s0 NCCL_SOCKET_IFNAME=enp33s0
export TMPDIR="$run/tmp" XDG_CACHE_HOME="$run/cache"
: > "$run/python/uccl/__init__.py"
ln -s "$module/ep.abi3.so" "$run/python/uccl/ep.abi3.so" || exit 79
export PYTHONPATH="$run/python:/home/f00955680/uccl-ep-xpu-integration/ep/bench"
python -u "$stage/orchestrator.py" "$run"
