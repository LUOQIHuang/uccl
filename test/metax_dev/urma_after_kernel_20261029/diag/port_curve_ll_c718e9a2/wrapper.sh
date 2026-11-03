#!/bin/bash
set -u
stage=$1; pair=$2; backend=$3; name=$4; index=$5
run="$pair/$backend/ll/$name"
setsid env UCCL_CURVE_RUN="$name" bash "$stage/${backend}_node.sh" ll "$name" 128 7168 8 "$index" > "$stage/${name}.driver.log" 2>&1 &
pid=$!
python3 "$stage/peak_sampler.py" "$name" "$pid" "$stage/${name}.samples.jsonl" "$stage/${name}.done" > "$stage/${name}.sampler.log" 2>&1 &
sampler=$!
wait "$pid"; rc=$?
touch "$stage/${name}.done"
wait "$sampler"; sc=$?
echo LL_NODE_RESULT="$name" HOST="$(hostname -s)" RC="$rc" SAMPLER_RC="$sc"
echo DRIVER_BEGIN; cat "$stage/${name}.driver.log"; echo DRIVER_END
if [ -f "$run/bench.log" ]; then echo BENCH_LOG_BEGIN; cat "$run/bench.log"; echo BENCH_LOG_END; fi
echo SAMPLES_BEGIN; cat "$stage/${name}.samples.jsonl"; echo SAMPLES_END
cat "$stage/${name}.sampler.log"
[ "$rc" -eq 0 ] && [ "$sc" -eq 0 ] || exit 80
grep -q 'LL_PORT_WINDOW_BEGIN' "$run/bench.log" && grep -q 'LL_PORT_WINDOW_END' "$run/bench.log" || exit 81
grep -q 'BENCH_SAFE_UTILS_LOADED lazy_init=1' "$run/bench.log" || exit 82
grep -q 'All correctness tests passed' "$run/bench.log" && grep -q 'PHASE=BENCH_SCRIPT_DONE' "$run/bench.log" || exit 83
