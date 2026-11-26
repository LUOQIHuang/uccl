# BLOCKERS Study — 202 GPU CUDA Initialization Failure

## Date
2026-11-26 19:11 CST

## Summary
BLOCKERS.zh-CN.md study could not complete because 202 (master, rank=0)
GPU CUDA initialization failed during the `pre` case run phase.

## What Passed
- **Setup (both nodes)**: build + check PASSED on 202 and 204
- **Run check phase**: PASSED on both nodes

## What Failed
- **Run pre phase**: `RuntimeError: CUDA driver initialization failed, you might not have a CUDA gpu.`
  - 202 (rank=0): CUDA init failed, all checks False, rc=1
  - 204 (rank=1): Connection refused (202 never started the benchmark)

## 204 GPU Status
- GPU works correctly: `cuda_available=True`, `GPU_OK sum=0.0`
- No issues on 204.

## 202 GPU Diagnostics
- mx-smi: GPU visible, MetaX C500, 860/65536 MiB, Available, 0% util
- lspci: GPU at 0000:21:00.0, METAX driver in use
- dmesg: metax driver reloaded successfully, IP blocks init done, firmware loaded
- lsmod: metax, udma, udma_metax_p2p_bridge all loaded
- MPS: GPU=0040, bridge=0060, NIC bridge=0060 (same as prior successful tests)
- /dev/dri: card0, card1, renderD128 all present

## Fix Attempts (all failed)
1. **FLR (Function Level Reset)**: `mx-smi --flr` — reset done, CUDA still fails
2. **VFLR**: `mx-smi --vfflr` — "Operation not support in target device"
3. **Persistence mode**: set to Enable — no effect
4. **Driver reload**: `rmmod udma_metax_p2p_bridge + udma + metax; modprobe metax + udma + udma_metax_p2p_bridge` — all succeeded, mx-smi shows GPU Available, but CUDA init STILL fails

## Root Cause
Unknown. GPU kernel driver works (mx-smi, dmesg OK), but torch/MACA user-space
CUDA initialization fails. Likely requires machine reboot to restore GPU state.

## Prior Context
- Previous tests (CREDIT_AB, normal_completion, writes_first_batch) all passed
  on this same GPU without issues.
- The MPS bridge register had reset from 0060 to 0020 (noticed at start of this
  test), was manually restored to 0060. This may indicate a PCIe bus event.
- 202 GPU CUDA init failure persists across FLR and driver reload.

## Overlay Patch Applied (for build compilation)
- Fixed fifo_stage_test.cpp compilation: added -include fadd_path_probe.hpp
  and thread_idx=0 to mock cfg_ struct (same pattern as urma_proxy_plan_test fix)
- This fix is in test/normal_completion/overlay.py and should be committed
  for the blockers study to build successfully.
