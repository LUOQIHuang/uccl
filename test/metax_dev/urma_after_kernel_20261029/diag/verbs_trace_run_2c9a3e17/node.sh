#!/usr/bin/env bash
set -u
ulimit -c 0
export LC_ALL=C
base=/home/f00955680/metax_dev/urma_after_kernel_20261029/diag
stage="$base/bench_pair_2274e1bc"
bench=/home/f00955680/uccl-ep-xpu-integration/ep/bench
priv="$base/umdk_private_618957c3/lib"
trace="$base/verbs_trace_d4b7c2f8/libverbs_trace.so"
run="$base/verbs_trace_run_2c9a3e17"
port=29992
host=$(hostname -s)
case "$host" in
 master) rank=0; module="$base/ep_host_atomic_from_204_4ed19a6b/module"; boot=f22963d1-0acd-481e-b7d8-7e86203a7ba9 ;;
 worker1) rank=1; module="$base/ep_host_atomic_4ed19a6b/module"; boot=57c7db3d-4b1d-4c6e-987c-d52cfac20a55 ;;
 *) exit 70 ;;
esac
expected=144f1fca126eb8971a5278c3baf0b44e1a29fb7b03ce51c928feb7abc19af798
[ "$(uname -r)" = 6.6.0-gdr_f00955680_diag ] && [ "$(cat /proc/sys/kernel/random/boot_id)" = "$boot" ] || exit 71
[ "$(cat /sys/module/metax/parameters/runpm_link)" = 0 ] && [ -d /sys/class/infiniband/rocep40s0f0 ] || exit 72
printf '%s  %s\n' "$expected" "$module/ep.abi3.so" \
 '32d2d8f07920792cbfe4f04d859bc5cc5602fbfae1d05c61079b115012232dca' "$trace" \
 '57b2f5cbe7967eed5f69f6f4b5862befb37e96be1a369fb6e67f95ba5f055a26' "$stage/launcher.py" \
 'f29b0c21c95f62efa810e20b879f10f4e482472547b9d50bec4178d8d95d88e3' "$bench/test_low_latency.py" \
 '606d5a088fe5656aa6052862520c45a9092ca08bd1b7b3bd273683f9b549188c' "$bench/buffer.py" \
 '5d9a7be095a3851ed300e67936bbad91ea75ef8c5ecf6e5bb87b0944441a5199' "$bench/utils.py" | sha256sum -c - || exit 73
for f in /dev/shm/uccl_barrier_*_uid0_ll_th*; do [ -e "$f" ] || continue; printf 'STOP_EXISTING_LL_BARRIER=%s\n' "$f"; exit 74; done
for check in "$port" "$((port+100))"; do
 if ss -H -ltn "sport = :$check" | grep -q .; then echo STOP_PORT_IN_USE="$check"; exit 75; fi
done
timeout 12s mx-smi > "$run/gpu_before.txt" 2>&1 || exit 76
grep -q Available "$run/gpu_before.txt" || { echo STOP_GPU_UNAVAILABLE; exit 76; }
if [ -r /sys/kernel/debug/mxcd/ceq ] && awk 'NF==3 && $1 ~ /^[0-9]+$/ && $2 ~ /^[0-9]+$/ && $3 != "0x0" {found=1} END {exit !found}' /sys/kernel/debug/mxcd/ceq; then echo STOP_NONZERO_QUEUE_STATUS; exit 77; fi
source /home/f00955680/metax_dev/activate-uccl.sh || exit 78
if [ "$host" = master ]; then source /home/f00955680/metax_dev/native-libs-3.7.0.38-G1NK1l/activate.sh || exit 79; fi
unset UMDK_HOME UCCL_PROJECT_DIAG UCCL_URMA_TM UCCL_URMA_DEV_NAME
export LD_LIBRARY_PATH="$priv${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export CUDA_VISIBLE_DEVICES=0 PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
export UCCL_EP_TRANSPORT=verbs UCCL_EP_VERBS_REG=dmabuf UCCL_IB_HCA=rocep40s0f0 UCCL_METAX_URMA_HOST_ATOMICS=1
export MCCL_IB_DISABLE=1 MCCL_SOCKET_IFNAME=enp33s0 NCCL_SOCKET_IFNAME=enp33s0
export MCCL_DEBUG=INFO NCCL_DEBUG=INFO TMPDIR="$run/tmp" XDG_CACHE_HOME="$run/cache"
export UCCL_EP_MODULE_ROOT="$module" UCCL_EP_EXPECTED_SHA="$expected" UCCL_PRIVATE_UMDK="$priv"
mkdir -p "$run/tmp" "$run/cache" "$run/python/uccl" || exit 80
: > "$run/python/uccl/__init__.py"
ln -s "$module/ep.abi3.so" "$run/python/uccl/ep.abi3.so" || exit 80
export PYTHONPATH="$run/python:$bench"
if ! timeout -k 2s 50s python - "$rank" "$((port+100))" <<'READY'
import socket, sys, time
rank, port = map(int, sys.argv[1:])
if rank == 0:
    s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.settimeout(40); s.bind(('0.0.0.0', port)); s.listen(1)
    conn, _ = s.accept(); conn.settimeout(5)
    assert conn.recv(5) == b'READY'; conn.sendall(b'OK'); conn.close(); s.close()
else:
    end = time.monotonic() + 40
    while True:
        try: s = socket.create_connection(('141.61.17.202', port), timeout=2); break
        except OSError:
            if time.monotonic() >= end: raise
            time.sleep(0.5)
    s.settimeout(5); s.sendall(b'READY'); assert s.recv(2) == b'OK'; s.close()
print('PAIR_PREFLIGHT_OK', flush=True)
READY
then echo STOP_PAIR_PREFLIGHT; exit 81; fi
guard="$base/torchrun_cpu_exit_a9c40b17/torchrun_exit_guard.py"
printf '%s  %s\n' '9cba31fe18074bbf5b6fbe2345b570100663a3ed9da95cf6e118eedb409740c7' "$guard" | sha256sum -c - || exit 82
export UCCL_TORCHRUN_NODE_RANK="$rank" UCCL_EXIT_GUARD=1
unset UCCL_DIAG_EXIT_DELAY
export LD_PRELOAD="$trace:/usr/lib64/libibverbs.so.1"
cd "$bench" || exit 83
printf 'BENCH_START host=%s rank=%s backend=verbs gpu_registration=dmabuf\n' "$host" "$rank"
timeout -k 10s 300s python -u "$guard" --nnodes=2 --nproc_per_node=1 --node_rank="$rank" \
 --master_addr=141.61.17.202 --master_port="$port" --no-python \
 python -u "$stage/launcher.py" test_low_latency.py --num-processes=1 --num-tokens=128 \
 --hidden=2048 --num-topk=8 --num-experts=16 --disable-nvlink --pressure-test-mode=0 > "$run/bench.log" 2>&1
rc=$?
printf 'BENCH_PROCESS_RC=%s\n' "$rc"
if [ "$rc" -eq 0 ]; then
 count=$(grep -c '^Start experiment with settings:' "$run/bench.log" || true)
 printf 'CORRECTNESS_CASES=%s\n' "$count"
 [ "$count" -eq 16 ] || rc=91
 grep -q 'All correctness tests passed!' "$run/bench.log" || rc=91
 grep -q 'GPU MR registration path=dmabuf requested=dmabuf' "$run/bench.log" || rc=92
 grep -q 'control MR registration requested=dmabuf' "$run/bench.log" || rc=92
 grep -q "UCCL_EXIT_ACK_OK node_rank=$rank" "$run/bench.log" || rc=93
 grep -q 'PHASE=BENCH_SCRIPT_DONE' "$run/bench.log" || rc=93
 if grep -qE 'Traceback \(most recent call last\)|DistNetworkError|Segmentation fault|AssertionError|ChildFailedError|VERBS_TRACE_.*FAILED|VERBS_TRACE_MISSING_CONTEXT' "$run/bench.log"; then rc=94; fi
fi
printf 'BENCH_SELECTED_OUTPUT host=%s\n' "$host"
grep -E 'VERBS_TRACE_|control MR registration requested|GPU MR registration path|All correctness tests passed|PHASE=BENCH_SCRIPT_DONE|UCCL_EXIT_ACK_OK|Traceback|AssertionError|ChildFailedError|DistNetworkError|Segmentation fault' "$run/bench.log" | head -100 || true
if [ "$rc" -ne 0 ]; then tail -60 "$run/bench.log"; fi
printf 'BENCH_RC=%s HOST=%s LOG=%s\n' "$rc" "$host" "$run/bench.log"
exit "$rc"
