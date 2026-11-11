from pathlib import Path
import os
import sys
from datetime import timedelta

diag = Path("/home/f00955680/metax_dev/current-test/ll-stride256").resolve()
sys.path.insert(0, str(diag / "module"))
sys.path.insert(1, "/home/f00955680/uccl-ep-xpu-integration/ep/bench")

import torch
import torch.distributed as dist
import uccl.ep as ep
from buffer import Buffer
from test_low_latency import test_main

extension = Path(ep.__file__).resolve()
assert extension.is_relative_to(diag / "module"), \
    f"导入的不是诊断扩展: {extension}"
print("DIAG_EXTENSION:", extension, flush=True)

hidden = int(os.environ["DIAG_HIDDEN"])
assert hidden in (2048, 7168, 8192)
tokens, experts, topk = 128, 16, 8
torch.cuda.set_device(0)
dist.init_process_group(backend="nccl", timeout=timedelta(seconds=180))
torch.set_default_dtype(torch.bfloat16)
torch.set_default_device("cuda:0")
rank, world = dist.get_rank(), dist.get_world_size()
assert world == 2
group = dist.new_group(list(range(world)))

size = Buffer.get_low_latency_rdma_size_hint(tokens, hidden, world, experts)
buf = Buffer(
    group, num_rdma_bytes=size, low_latency_mode=True,
    num_qps_per_rank=experts // world,
    allow_nvlink_for_low_latency_mode=False,
    explicitly_destroy=True, allow_mnnvl=False,
)
assert buf.scratch.is_cuda

# 与已核对的非 PER_EXPERT_BATCHING LowLatencyLayout 对应。
base = buf.scratch.data_ptr()
scratch_bytes = buf.scratch.numel() * buf.scratch.element_size()
signal_bytes = ((experts * 4 + 127) // 128) * 128
dispatch_max = 16 + max(hidden * 2, hidden + (hidden // 128) * 4)
combine_msg = hidden * 2
send_cap = max(tokens * dispatch_max, experts * tokens * combine_msg)
recv_cap = max(experts * tokens * dispatch_max,
               experts * tokens * combine_msg)
wire = 16 + hidden + (hidden // 128) * 4
stride = ((wire + 255) // 256) * 256
needed = (tokens - 1) * stride + wire

bank0 = base + signal_bytes * 2
bank1 = bank0 + send_cap
recv0 = bank1 + send_cap
layout_end = recv0 + recv_cap * 2

for i, addr in enumerate((bank0, bank1)):
    print(
        f"RANK={rank} BANK={i} base={addr:#x} mod256={addr % 256} "
        f"capacity={send_cap} needed={needed} wire={wire} stride={stride}",
        flush=True,
    )
assert bank0 % 256 == 0 and bank1 % 256 == 0, \
    "发送 bank 基址未按 256 字节对齐，停止测试"
assert needed <= send_cap, "发送 bank 容量不足，停止测试"
assert bank0 >= base and layout_end <= base + min(size, scratch_bytes), \
    "布局超出实际 GPU 缓冲区，停止测试"
print(f"RANK={rank} BANK_BOUNDS_PASS", flush=True)

test_main(tokens, hidden, experts, topk, rank, world, group, buf,
          seed=0, skip_benchmark=True)
print(f"RANK={rank} HIDDEN={hidden} CORRECTNESS_PASS", flush=True)
dist.barrier()
buf.destroy()
dist.destroy_process_group()
print(f"RANK={rank} HIDDEN={hidden} CLEANUP_DONE", flush=True)
