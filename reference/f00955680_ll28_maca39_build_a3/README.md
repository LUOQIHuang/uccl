# f00955680 ll28_maca39_build_a3 EP Stage Trace Reference

This directory contains stage-trace instrumented EP source files copied from
`/home/f00955680/metax_dev/stage_latency_20261108/ll28_maca39_build_a3/tree/ep/`
on 2026-11-11 for code review.

## Files

| File | Purpose |
| --- | --- |
| `ep/src/internode_ll.cu` | LL Dispatch / Combine GPU stage tracing |
| `ep/src/internode.cu` | Normal Dispatch / Combine GPU stage tracing |
| `ep/src/proxy.cpp` | CPU Proxy polling, parsing, submission and completion events |
| `ep/include/gpu_stage_trace.cuh` | GPU trace record structures |
| `ep/include/cpu_stage_trace.hpp` | CPU trace records and timestamps |
| `ep/src/rdma.cpp` | Verbs/RDMA transport and tracing |
| `ep/src/urma_transport.cpp` | URMA transport and tracing |

## Source

- Build tree: `ll28_maca39_build_a3` (MACA 3.9.0)
- Date: 2026-11-04 ~ 2026-11-09
- Copied: 2026-11-11
