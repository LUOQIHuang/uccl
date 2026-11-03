#!/usr/bin/env bash
set -u
ulimit -c 0
export LC_ALL=C
stage=/home/f00955680/metax_dev/urma_after_kernel_20261029/diag/bench_pair_2274e1bc
bench=/home/f00955680/uccl-ep-xpu-integration/ep/bench
base=/home/f00955680/metax_dev/urma_after_kernel_20261029/diag
priv="$base/umdk_private_618957c3/lib"
mode=${1:-}
host=$(hostname -s)
case "$mode" in
 normal) port=29851; script=test_internode.py; token=ht ;;
 ll) port=29852; script=test_low_latency.py; token=ll ;;
 *) echo INVALID_MODE; exit 70 ;;
esac
case "$host" in
 master) rank=0; module="$base/ep_crossbuild_66ea72a3/module"; expected=f102729f4abdc491018ba8a22b214491e8bfa9f6c05bed16df83fad82e4cb406; expected_boot=1a3db15f-3086-45ce-b1ac-544a37d4f153 ;;
 worker1) rank=1; module="$base/ep_private_b73a94c1/module"; expected=14f1b7b3c7734cfe8609473ecc96bb3478cc7c224bc54027922f75523b7dec10; expected_boot=e640f58e-cdff-4f22-88ce-a405ab858e3e ;;
 *) echo INVALID_HOST; exit 71 ;;
esac
[ "$(uname -r)" = 6.6.0-gdr_f00955680_diag ] && [ "$(cat /proc/sys/kernel/random/boot_id)" = "$expected_boot" ] || exit 72
[ -e /sys/class/uburma/udmac0d1e2 ] && [ -d /sys/module/udma_metax_p2p_bridge ] || exit 73
run="$stage/$mode"
[ ! -e "$run" ] || { echo RUN_EXISTS; exit 74; }
mkdir -p "$run/tmp" "$run/cache" "$run/python/uccl" || exit 75
printf 'HOST=%s NODE_RANK=%s MODE=%s PORT=%s BOOT_ID=%s\n' "$host" "$rank" "$mode" "$port" "$expected_boot"
printf '%s  %s\n' "$expected" "$module/ep.abi3.so" | sha256sum -c - || exit 76
printf '%s  %s\n' \
 '606d5a088fe5656aa6052862520c45a9092ca08bd1b7b3bd273683f9b549188c' "$bench/buffer.py" \
 '5d9a7be095a3851ed300e67936bbad91ea75ef8c5ecf6e5bb87b0944441a5199' "$bench/utils.py" \
 'd679e4bdad167251dbc9d2c2f12634501b0f72db33231e1f1ce26d06ebac1b5f' "$bench/test_internode.py" \
 'f29b0c21c95f62efa810e20b879f10f4e482472547b9d50bec4178d8d95d88e3' "$bench/test_low_latency.py" | sha256sum -c - || exit 77
for f in /dev/shm/uccl_barrier_*_uid0_${token}_th*; do [ -e "$f" ] || continue; printf 'EXISTING_SHARED_BARRIER=%s\n' "$f"; exit 78; done
if ss -ltn 2>/dev/null | awk -v p=":$port" '$4 ~ p"$" {found=1} END {exit !found}'; then echo PORT_IN_USE; exit 79; fi
timeout -k 2s 12s mx-smi > "$run/gpu_before.txt" 2>&1 || { cat "$run/gpu_before.txt"; exit 80; }
cat "$run/gpu_before.txt"
grep -q 'no process found' "$run/gpu_before.txt" && grep -q Available "$run/gpu_before.txt" || { echo GPU_NOT_IDLE_STOP; exit 81; }
source /home/f00955680/metax_dev/activate-uccl.sh || exit 82
if [ "$host" = master ]; then source /home/f00955680/metax_dev/native-libs-3.7.0.38-G1NK1l/activate.sh || exit 83; fi
unset UMDK_HOME UCCL_PROJECT_DIAG UCCL_EP_VERBS_REG UCCL_IB_HCA
export LD_LIBRARY_PATH="$priv${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export CUDA_VISIBLE_DEVICES=0 PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
export UCCL_EP_TRANSPORT=urma UCCL_URMA_TM=rc UCCL_URMA_DEV_NAME=udmac0d1e2
export MCCL_IB_DISABLE=1 MCCL_SOCKET_IFNAME=enp33s0 NCCL_SOCKET_IFNAME=enp33s0
export MCCL_DEBUG=INFO NCCL_DEBUG=INFO
export TMPDIR="$run/tmp" XDG_CACHE_HOME="$run/cache"
export UCCL_EP_MODULE_ROOT="$module" UCCL_EP_EXPECTED_SHA="$expected" UCCL_PRIVATE_UMDK="$priv"
: > "$run/python/uccl/__init__.py"
ln -s "$module/ep.abi3.so" "$run/python/uccl/ep.abi3.so" || exit 84
export PYTHONPATH="$run/python:$bench"
printf 'BACKEND=%s TM=%s DEVICE=%s MCCL_IB_DISABLE=%s SOCKET=%s\n' "$UCCL_EP_TRANSPORT" "$UCCL_URMA_TM" "$UCCL_URMA_DEV_NAME" "$MCCL_IB_DISABLE" "$MCCL_SOCKET_IFNAME"
if [ "$mode" = normal ]; then
 args=(--num-processes=1 --num-tokens=128 --hidden=7168 --num-topk-groups=2 --num-topk=8 --num-experts=256 --test-ll-compatibility --pressure-test-mode=0)
else
 args=(--num-processes=1 --num-tokens=128 --hidden=2048 --num-topk=8 --num-experts=16 --disable-nvlink --pressure-test-mode=0)
fi
cd "$bench" || exit 85
printf 'BENCH_BEGIN=%s\n' "$(date -Ins)"
timeout -k 5s 130s torchrun --nnodes=2 --nproc_per_node=1 --node_rank="$rank" \
 --master_addr=141.61.17.202 --master_port="$port" --no-python \
 python -u "$stage/launcher.py" "$script" "${args[@]}" > "$run/bench.log" 2>&1
rc=$?
printf 'BENCH_END=%s\n' "$(date -Ins)"
cat "$run/bench.log"
printf 'BENCH_RC=%s MODE=%s HOST=%s\n' "$rc" "$mode" "$host"
printf '%s\n' "$rc" > "$run/status"
exit "$rc"
