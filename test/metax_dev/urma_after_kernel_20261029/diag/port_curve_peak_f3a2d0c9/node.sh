#!/bin/bash
set -u
ulimit -c 0
run=$1; name=$2; backend=$3; direction=$4; port=$5; endpoint=$6
priv=/home/f00955680/metax_dev/urma_after_kernel_20261029/diag/umdk_private_618957c3/lib
trace=/home/f00955680/metax_dev/urma_after_kernel_20261029/diag/urma_reg_trace_c6e72f9d/libtrace.so
if [ "$backend" = urma ]; then
 args=(env UCCL_CURVE_RUN="$name" UCCL_CURVE_DIRECTION="$direction" LD_LIBRARY_PATH="$priv:/opt/maca-3.7.0/lib" LD_PRELOAD="$trace" UCCL_URMA_DEV_NAME=udmac0d1e2 UCCL_URMA_TM=rc UCCL_DIAG_MAIN_HOST=0 UCCL_DIAG_PAYLOAD_BYTES=4194304 UCCL_DIAG_ITERATIONS=24576 timeout -k 3s 80s "$run/urma" "$endpoint" "$port" 0)
else
 args=(env UCCL_CURVE_RUN="$name" UCCL_CURVE_DIRECTION="$direction" LD_LIBRARY_PATH=/opt/maca-3.7.0/lib UCCL_DIAG_PAYLOAD_BYTES=4194304 timeout -k 3s 80s "$run/verbs" "$endpoint" "$port" 0 24576)
fi
setsid "${args[@]}" > "$run/${name}.log" 2>&1 &
pid=$!
python3 "$run/peak_sampler.py" "$name" "$pid" "$run/${name}.jsonl" "$run/${name}.done" > "$run/${name}.sampler.log" 2>&1 &
sampler=$!
wait "$pid"; rc=$?
touch "$run/${name}.done"
wait "$sampler"; sc=$?
echo NODE_RESULT="$name" HOST="$(hostname -s)" RC="$rc" SAMPLER_RC="$sc"
echo PROBE_BEGIN="$name"; cat "$run/${name}.log"; echo PROBE_END="$name"
echo SAMPLES_BEGIN="$name"; cat "$run/${name}.jsonl"; echo SAMPLES_END="$name"
cat "$run/${name}.sampler.log"
[ "$rc" -eq 0 ] && [ "$sc" -eq 0 ] || exit 81
grep -q 'PEAK_WINDOW' "$run/${name}.log" && grep -q 'WRITE_DATA_MATCH=1' "$run/${name}.log" || exit 82
if [ "$backend" = urma ]; then
 grep -q 'BULK_WRITE_DATA_MATCH=1' "$run/${name}.log" && grep -q 'MAIN_KIND=FINEGRAIN_GPU' "$run/${name}.log" && grep -q 'MAPPED_HOST_GPU_FADD_PASS' "$run/${name}.log" || exit 83
else
 grep -q 'GPU_KIND=FINEGRAIN_GPU GPU_REG=DMA_BUF' "$run/${name}.log" || exit 84
fi
