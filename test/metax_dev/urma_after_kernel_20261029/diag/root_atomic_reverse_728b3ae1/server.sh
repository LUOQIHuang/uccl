#!/usr/bin/env bash
set -u
base=/home/f00955680/metax_dev/urma_after_kernel_20261029/diag
priv="$base/umdk_private_618957c3/lib"
trace="$base/urma_reg_trace_c6e72f9d/libtrace.so"
binary="$base/vendor_atomic_run_e34a17b6/vendor_atomic"
timeout -k 3s 100s env LD_LIBRARY_PATH="$priv:/opt/maca-3.7.0/lib" LD_PRELOAD="$trace" \
 PERFTEST_DIAG_ATOMIC_GPU_TARGET=1 "$binary" atomic_lat -d udmac0d1e2 -p 1 -A faa -s 8 -n 10 -I 0 \
 --eid_idx 0 -Q 1 -P 29897 --stdout --gpu-mode=metax
