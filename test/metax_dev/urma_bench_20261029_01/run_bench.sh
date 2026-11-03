#!/usr/bin/env bash
set -o pipefail

run_dir=/home/f00955680/metax_dev/urma_bench_20261029_01
bench=/home/f00955680/uccl-ep-xpu-integration/ep/bench
module=/home/f00955680/metax_dev/current-test/ll-stride256/module
mode=${1:-}
case "$mode" in
  normal) port=29641; script=test_internode.py ;;
  ll) port=29642; script=test_low_latency.py ;;
  *) echo 'INVALID_MODE'; exit 90 ;;
esac

finish() {
  rc=$?
  printf 'BENCH_RC=%s MODE=%s HOST=%s\n' "$rc" "$mode" "$(hostname -s)"
  printf '%s\n' "$rc" > "$run_dir/$mode.status.tmp"
  mv "$run_dir/$mode.status.tmp" "$run_dir/$mode.status"
}
trap finish EXIT

host=$(hostname -s)
case "$host" in
  master) rank=0 ;;
  worker1) rank=1 ;;
  *) echo 'UNKNOWN_HOST'; exit 91 ;;
esac
source /home/f00955680/metax_dev/activate-uccl.sh || exit 92
if [ "$host" = master ]; then
  source /home/f00955680/metax_dev/native-libs-3.7.0.38-G1NK1l/activate.sh || exit 93
fi
unset UMDK_HOME UCCL_PROJECT_DIAG UCCL_EP_VERBS_REG UCCL_IB_HCA
export LD_LIBRARY_PATH="/home/g00953922/umdk_metax_isolated/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export CUDA_VISIBLE_DEVICES=0 PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
export UCCL_EP_TRANSPORT=urma UCCL_URMA_TM=rc UCCL_URMA_DEV_NAME=udmac0d1e2
export MCCL_IB_DISABLE=1 MCCL_SOCKET_IFNAME=enp33s0 NCCL_SOCKET_IFNAME=enp33s0
export MCCL_DEBUG=INFO NCCL_DEBUG=INFO
export TMPDIR="$run_dir/tmp" XDG_CACHE_HOME="$run_dir/cache"
mkdir -p "$TMPDIR" "$XDG_CACHE_HOME" || exit 94
cd "$bench" || exit 95
export PYTHONPATH="$module:$bench"

printf 'HOST=%s NODE_RANK=%s MODE=%s PORT=%s\n' "$host" "$rank" "$mode" "$port"
printf 'UCCL_EP_TRANSPORT=%s UCCL_URMA_TM=%s UCCL_URMA_DEV_NAME=%s MCCL_IB_DISABLE=%s MCCL_SOCKET_IFNAME=%s\n' \
  "$UCCL_EP_TRANSPORT" "$UCCL_URMA_TM" "$UCCL_URMA_DEV_NAME" "$MCCL_IB_DISABLE" "$MCCL_SOCKET_IFNAME"
GIT_OPTIONAL_LOCKS=0 git -C /home/f00955680/uccl-ep-xpu-integration rev-parse HEAD
sha256sum "$module/ep.abi3.so"

launcher=$(cat <<'PY'
import ctypes, os
ctypes.CDLL('/opt/maca-3.7.0/lib/libmcruntime.so', mode=os.RTLD_NOW | os.RTLD_GLOBAL)
print('MCRUNTIME_PRELOAD_OK', flush=True)
import pathlib, runpy, sys
from datetime import timedelta
import torch
import torch.distributed as dist
import uccl.ep as ep
import utils
from buffer import Buffer
module = pathlib.Path('/home/f00955680/metax_dev/current-test/ll-stride256/module').resolve()
bench = pathlib.Path('/home/f00955680/uccl-ep-xpu-integration/ep/bench').resolve()
extension = pathlib.Path(ep.__file__).resolve()
print('EXTENSION:', extension, flush=True)
print('BUFFER_MODULE:', pathlib.Path(__import__('buffer').__file__).resolve(), flush=True)
assert extension.is_relative_to(module), 'unexpected extension path'
assert pathlib.Path(utils.__file__).resolve().is_relative_to(bench), 'unexpected utils path'
assert pathlib.Path(__import__('buffer').__file__).resolve().is_relative_to(bench), 'unexpected buffer path'
size = ep.Config(24, 8, 512, 16, 512).get_rdma_buffer_size_hint(7168 * 2, 2)
print('RDMA_HINT_BYTES:', size, flush=True)
assert size > 0, 'missing multi-node buffer hint'
def lazy_init(local_rank, num_local_ranks):
    torch.cuda.set_device(local_rank)
    dist.init_process_group(backend='nccl', timeout=timedelta(seconds=180))
    torch.set_default_dtype(torch.bfloat16)
    torch.set_default_device(f'cuda:{local_rank}')
    return dist.get_rank(), dist.get_world_size(), dist.new_group(list(range(dist.get_world_size())))
utils.init_dist_under_torchrun = lazy_init
script = sys.argv.pop(1)
runpy.run_path(script, run_name='__main__')
PY
)

if [ "$mode" = normal ]; then
  args=(--num-processes=1 --num-tokens=128 --hidden=7168 --num-topk-groups=2 --num-topk=8 --num-experts=256 --test-ll-compatibility --pressure-test-mode=0)
else
  args=(--num-processes=1 --num-tokens=128 --hidden=2048 --num-topk=8 --num-experts=16 --disable-nvlink --pressure-test-mode=0)
fi

timeout -k 10s 600s torchrun --nnodes=2 --nproc_per_node=1 --node_rank="$rank" \
  --master_addr=141.61.17.202 --master_port="$port" --no-python \
  python -u -c "$launcher" "$script" "${args[@]}"
exit "$?"
