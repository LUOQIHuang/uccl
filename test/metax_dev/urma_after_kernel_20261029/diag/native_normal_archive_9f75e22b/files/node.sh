#!/usr/bin/env bash
set -u
ulimit -c 0
export LC_ALL=C
stage=/home/f00955680/metax_dev/urma_after_kernel_20261029/diag/bench_pair_2274e1bc
bench=/home/f00955680/uccl-ep-xpu-integration/ep/bench
base=/home/f00955680/metax_dev/urma_after_kernel_20261029/diag
priv="$base/umdk_private_618957c3/lib"
mode=${1:-}
case_id=${2:-}
case_tokens=${3:-}
case_hidden=${4:-}
case_topk=${5:-}
case_index=${6:-}
[[ "$case_id" =~ ^[a-z0-9_]+$ && "$case_tokens" =~ ^[0-9]+$ && "$case_hidden" =~ ^[0-9]+$ && "$case_topk" =~ ^[0-9]+$ && "$case_index" =~ ^[0-9]+$ ]] || { echo INVALID_CASE; exit 69; }
port=$((30100 + case_index))
host=$(hostname -s)
case "$mode" in
 normal) script=test_internode.py; token=ht ;;
 ll) script=test_low_latency.py; token=ll ;;
 *) echo INVALID_MODE; exit 70 ;;
esac
case "$host" in
 master) rank=0; module="$base/ep_integration_from_204_8ce27f40/module"; expected=cd3fbbcc7a6607d267de38d398e6e3bec44d5b3157668f5c7c8feeb3d0ccad2c; expected_boot=f22963d1-0acd-481e-b7d8-7e86203a7ba9 ;;
 worker1) rank=1; module="$base/ep_integration_8ce27f40/module"; expected=cd3fbbcc7a6607d267de38d398e6e3bec44d5b3157668f5c7c8feeb3d0ccad2c; expected_boot=57c7db3d-4b1d-4c6e-987c-d52cfac20a55 ;;
 *) echo INVALID_HOST; exit 71 ;;
esac
[ "$(uname -r)" = 6.6.0-gdr_f00955680_diag ] && [ "$(cat /proc/sys/kernel/random/boot_id)" = "$expected_boot" ] || exit 72
[ -e /sys/class/uburma/udmac0d1e2 ] && [ -d /sys/module/udma_metax_p2p_bridge ] || exit 73
[ "$(cat /sys/module/metax/parameters/runpm_link)" = 0 ] || { echo STOP_RUNPM_LINK; exit 73; }
pm=$(cat /sys/bus/pci/devices/0000:a1:00.0/power/runtime_status)
case "$pm" in active|suspended) ;; *) echo STOP_RUNTIME_PM="$pm"; exit 73 ;; esac
printf '%s  %s\n' '57b2f5cbe7967eed5f69f6f4b5862befb37e96be1a369fb6e67f95ba5f055a26' "$stage/launcher.py" | sha256sum -c - || exit 73
if ! journal=$(journalctl -b -k -q --no-pager 2>/dev/null); then echo STOP_JOURNAL_UNAVAILABLE; exit 73; fi
errors=$(printf '%s\n' "$journal" | awk '/kill queue timeout|destroy ce ringbuf failed|results in a decrease in the number of available ringbufs/ {n++} END {print n+0}')
[ "$errors" -eq 0 ] || { echo STOP_GPU_QUEUE_ERRORS="$errors"; exit 73; }
if [ -r /sys/kernel/debug/mxcd/ceq ] && awk 'NF==3 && $1 ~ /^[0-9]+$/ && $2 ~ /^[0-9]+$/ && $3 != "0x0" {found=1} END {exit !found}' /sys/kernel/debug/mxcd/ceq; then echo STOP_NONZERO_QUEUE_STATUS; exit 73; fi
for dev in /dev/mxcd; do
 [ -e "$dev" ] || { echo STOP_DEVICE_MISSING="$dev"; exit 73; }
 timeout -k 1s 5s fuser "$dev" >/dev/null 2>&1
 frc=$?
 [ "$frc" -eq 1 ] || { echo STOP_DEVICE_BUSY_OR_CHECK_FAILED="$dev:$frc"; exit 73; }
done
run="$stage/urma_native_ns_4be02acd/$mode/$case_id"
[ ! -e "$run" ] || { echo RUN_EXISTS; exit 74; }
mkdir -p "$run/tmp" "$run/cache" "$run/python/uccl" || exit 75
printf 'HOST=%s NODE_RANK=%s MODE=%s CASE=%s TOKENS=%s HIDDEN=%s TOPK=%s PORT=%s BOOT_ID=%s\n' "$host" "$rank" "$mode" "$case_id" "$case_tokens" "$case_hidden" "$case_topk" "$port" "$expected_boot"
printf '%s  %s\n' "$expected" "$module/ep.abi3.so" | sha256sum -c - || exit 76
printf '%s  %s\n' \
 '606d5a088fe5656aa6052862520c45a9092ca08bd1b7b3bd273683f9b549188c' "$bench/buffer.py" \
 '5d9a7be095a3851ed300e67936bbad91ea75ef8c5ecf6e5bb87b0944441a5199' "$bench/utils.py" \
 'd679e4bdad167251dbc9d2c2f12634501b0f72db33231e1f1ce26d06ebac1b5f' "$bench/test_internode.py" \
 'f29b0c21c95f62efa810e20b879f10f4e482472547b9d50bec4178d8d95d88e3' "$bench/test_low_latency.py" | sha256sum -c - || exit 77
for f in /dev/shm/uccl_barrier_*_uid0_${token}_th*; do [ -e "$f" ] || continue; printf 'EXISTING_SHARED_BARRIER=%s\n' "$f"; exit 78; done
for check_port in "$port" "$((port + 100))" "$((port + 200))"; do
 if ss -ltn 2>/dev/null | awk -v p=":$check_port" '$4 ~ p"$" {found=1} END {exit !found}'; then echo PORT_IN_USE="$check_port"; exit 79; fi
done
timeout -k 2s 12s mx-smi > "$run/gpu_before.txt" 2>&1 || { cat "$run/gpu_before.txt"; exit 80; }
cat "$run/gpu_before.txt"
grep -q 'no process found' "$run/gpu_before.txt" && grep -q Available "$run/gpu_before.txt" || { echo GPU_NOT_IDLE_STOP; exit 81; }
source /home/f00955680/metax_dev/activate-uccl.sh || exit 82
if [ "$host" = master ]; then source /home/f00955680/metax_dev/native-libs-3.7.0.38-G1NK1l/activate.sh || exit 83; fi
unset UMDK_HOME UCCL_PROJECT_DIAG UCCL_EP_VERBS_REG UCCL_IB_HCA
export LD_LIBRARY_PATH="$priv${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export CUDA_VISIBLE_DEVICES=0 PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
export UCCL_EP_TRANSPORT=urma UCCL_URMA_TM=rc UCCL_URMA_DEV_NAME=udmac0d1e2 UCCL_METAX_URMA_HOST_ATOMICS=0
export MCCL_IB_DISABLE=1 MCCL_SOCKET_IFNAME=enp33s0 NCCL_SOCKET_IFNAME=enp33s0
export MCCL_DEBUG=INFO NCCL_DEBUG=INFO
export TMPDIR="$run/tmp" XDG_CACHE_HOME="$run/cache"
export UCCL_EP_MODULE_ROOT="$module" UCCL_EP_EXPECTED_SHA="$expected" UCCL_PRIVATE_UMDK="$priv"
: > "$run/python/uccl/__init__.py"
ln -s "$module/ep.abi3.so" "$run/python/uccl/ep.abi3.so" || exit 84
export PYTHONPATH="$run/python:$bench"
printf 'BACKEND=%s TM=%s DEVICE=%s HOST_ATOMICS=%s MCCL_IB_DISABLE=%s SOCKET=%s\n' "$UCCL_EP_TRANSPORT" "$UCCL_URMA_TM" "$UCCL_URMA_DEV_NAME" "$UCCL_METAX_URMA_HOST_ATOMICS" "$MCCL_IB_DISABLE" "$MCCL_SOCKET_IFNAME"
atomic_ctl=$(setpci -s 0000:a0:08.0 CAP_EXP+28.w) || exit 86
[[ "$atomic_ctl" =~ ^[0-9a-fA-F]{4}$ ]] && (( 16#$atomic_ctl & 0x40 )) || { echo ROOT_ATOMIC_REQUEST_DISABLED; exit 86; }
printf 'ROOT_ATOMIC_CONTROL=%s\n' "$atomic_ctl"
if [ "$mode" = normal ]; then
 args=(--num-processes=1 --num-tokens="$case_tokens" --hidden="$case_hidden" --num-topk-groups=2 --num-topk="$case_topk" --num-experts=256 --test-ll-compatibility --pressure-test-mode=0)
else
 args=(--num-processes=1 --num-tokens="$case_tokens" --hidden="$case_hidden" --num-topk="$case_topk" --num-experts=288 --pressure-test-mode=0)
fi
cd "$bench" || exit 85
printf '%s  %s\n' '2c7cb4afe250c1e6568590aeca4248a54d4b20e63d15ce208f10b22d914e9712' "$base/urma_reg_trace_c6e72f9d/libtrace.so" | sha256sum -c - || exit 86
if ! timeout -k 2s 80s python - "$rank" "$((port + 100))" <<'PEER_READY'
import socket, sys, time
rank=int(sys.argv[1]); port=int(sys.argv[2]); started=time.monotonic()
print('PAIR_PREFLIGHT_START',rank,port,started,flush=True)
if rank == 0:
    s=socket.socket(); s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
    s.settimeout(70); s.bind(('0.0.0.0',port)); s.listen(1)
    conn,_=s.accept(); conn.settimeout(5)
    if conn.recv(5) != b'READY': raise RuntimeError('bad peer preflight')
    conn.sendall(b'OK'); conn.close(); s.close()
else:
    end=time.monotonic()+70
    while True:
        try:
            s=socket.create_connection(('141.61.17.202',port), timeout=2); break
        except OSError:
            if time.monotonic()>=end: raise
            time.sleep(0.5)
    s.settimeout(5); s.sendall(b'READY')
    if s.recv(2) != b'OK': raise RuntimeError('bad peer ack')
    s.close()
print('PAIR_PREFLIGHT_OK elapsed_s=',round(time.monotonic()-started,3),flush=True)
PEER_READY
then echo STOP_PAIR_PREFLIGHT; exit 87; fi
export LD_PRELOAD="$base/urma_reg_trace_c6e72f9d/libtrace.so"
printf 'BENCH_BEGIN=%s\n' "$(date -Ins)"
guard="$base/torchrun_cpu_exit_a9c40b17/torchrun_exit_guard.py"
printf '%s  %s\n' '9cba31fe18074bbf5b6fbe2345b570100663a3ed9da95cf6e118eedb409740c7' "$guard" | sha256sum -c - || exit 86
export UCCL_TORCHRUN_NODE_RANK="$rank" UCCL_EXIT_GUARD=1
unset UCCL_DIAG_EXIT_DELAY
timeout -k 10s 2400s python -u "$guard" --nnodes=2 --nproc_per_node=1 --node_rank="$rank" \
 --master_addr=141.61.17.202 --master_port="$port" --no-python \
 python -u "$stage/launcher.py" "$script" "${args[@]}" > "$run/bench.log" 2>&1
rc=$?
printf 'TORCHRUN_RC=%s\n' "$rc"
printf 'BENCH_END=%s\n' "$(date -Ins)"
if [ "$rc" -eq 0 ]; then
 if [ "$mode" = ll ]; then
  tested=$(grep -c '^Start experiment with settings:' "$run/bench.log" || true)
  printf 'CORRECTNESS_CASES=%s\n' "$tested"
  [ "$tested" -eq 16 ] || rc=93
  grep -q 'All correctness tests passed!' "$run/bench.log" && grep -q 'Dispatch + combine bandwidth:' "$run/bench.log" && grep -q 'Dispatch bandwidth:' "$run/bench.log" || rc=90
 else
  tested=$(grep -c '^\[testing\] Running with' "$run/bench.log" || true)
  printf 'CORRECTNESS_CASES=%s\n' "$tested"
  [ "$tested" -eq 24 ] || rc=93
  grep -q 'Best dispatch (FP8)' "$run/bench.log" && grep -q 'Best dispatch (BF16)' "$run/bench.log" && grep -q 'Best combine:' "$run/bench.log" || rc=90
 fi
 grep -q 'URMA_TRACE_BEGIN.*len=81960 access=14 gpu=1' "$run/bench.log" || rc=94
 grep -q 'URMA_TRACE_BEGIN.*gpu=0' "$run/bench.log" || rc=94
 grep -q '\[Proxy\] UCCL_EP_TRANSPORT=urma initialized' "$run/bench.log" || rc=94
 grep -q "UCCL_EXIT_ACK_OK node_rank=$rank" "$run/bench.log" || rc=95
 grep -q 'PHASE=BENCH_SCRIPT_DONE' "$run/bench.log" || rc=91
 if grep -qE 'Traceback \(most recent call last\)|DistNetworkError|Segmentation fault|AssertionError|ChildFailedError|Failed CR status|mcFree failed' "$run/bench.log"; then rc=92; fi
fi
grep -E 'URMA_TRACE_BEGIN.*(gpu=1|gpu=0)|\[Proxy\] UCCL_EP_TRANSPORT=urma initialized' "$run/bench.log" | head -16 || true
printf 'LOG_PATH=%s\n' "$run/bench.log"
grep -E 'All correctness tests passed|Dispatch \+ combine bandwidth:|Dispatch bandwidth:|Dispatch send/recv time:|Best dispatch|Best combine:|BENCH_SCRIPT_DONE|UCCL_EXIT_ACK_OK|Traceback|AssertionError|ChildFailedError|DistNetworkError|Segmentation fault' "$run/bench.log" | tail -18 || true
[ "$rc" -eq 0 ] || tail -50 "$run/bench.log"
printf 'BENCH_RC=%s MODE=%s HOST=%s\n' "$rc" "$mode" "$host"
printf '%s\n' "$rc" > "$run/status"
exit "$rc"
