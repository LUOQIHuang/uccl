import os
import torch.distributed as dist

dist.init_process_group("gloo")
dist.barrier()
print(f"CPU_WORKER_DONE rank={os.environ['RANK']}", flush=True)
dist.destroy_process_group()
