#!/usr/bin/env bash
set -u
ulimit -c 0
export LC_ALL=C
base=/home/f00955680/metax_dev/urma_after_kernel_20261029/diag
src="$base/vendor_atomic_5b4ea6d2/vendor_atomic"
run="$base/vendor_atomic_root_ab_f4199c2e"
priv="$base/umdk_private_618957c3/lib"
trace="$base/urma_reg_trace_c6e72f9d/libtrace.so"
sha=42914f8cfefbc0c2bab993cb3fd5d328d6ff6e845cbabbd3eae9c4616866d239
peer=root@141.61.17.202
port=29896
opts=(-o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=8 -o ConnectionAttempts=1)
[ "$(hostname -s)" = worker1 ] && [ "$(uname -r)" = 6.6.0-gdr_f00955680_diag ] && [ ! -e "$run" ] || exit 69
[ "$(cat /sys/module/metax/parameters/runpm_link)" = 0 ] || exit 70
printf '%s  %s\n' "$sha" "$src" '18ea061e598330ba56ac07d7f9b4691414884aaf805c7f7a1973b1bc5c028967' "$priv/urma/liburma-udma.so" | sha256sum -c - || exit 71
if ss -H -ltn "sport = :$port" | grep -q .; then echo STOP_PORT_CONFLICT; exit 72; fi
gpu=$(timeout 15s mx-smi) || exit 73
printf '%s\n' "$gpu" | grep -q Available || exit 73
printf 'SHARED_URMA_USERS_BEFORE_204\n'
fuser -v /dev/uburma/udmac0d1e2 2>&1 || true
mkdir -m 700 "$run" || exit 74
cp "$src" "$run/vendor_atomic" || exit 74
ssh -T "${opts[@]}" "$peer" bash -s -- "$run" <<'PRE'
set -eu
run=$1
[ "$(hostname -s)" = master ] && [ "$(uname -r)" = 6.6.0-gdr_f00955680_diag ]
[ "$(cat /sys/module/metax/parameters/runpm_link)" = 0 ]
[ ! -e "$run" ]
echo SHARED_URMA_USERS_BEFORE_202
fuser -v /dev/uburma/udmac0d1e2 2>&1 || true
mkdir -m 700 "$run"
PRE
[ "$?" -eq 0 ] || exit 75
scp -q -p "${opts[@]}" "$run/vendor_atomic" "$peer:$run/vendor_atomic" </dev/null || exit 76
printf 'VENDOR_MICROTEST BACKEND=URMA TRANSPORT=RC GPU_TARGET=204 HOST_RESULT=202 BYTES=8 ITERS=10\n'
timeout -k 3s 70s env LD_LIBRARY_PATH="$priv:/opt/maca-3.7.0/lib" LD_PRELOAD="$trace" \
 PERFTEST_DIAG_ATOMIC_GPU_TARGET=1 "$run/vendor_atomic" atomic_lat -d udmac0d1e2 \
 -p 1 -A faa -s 8 -n 10 -I 0 --eid_idx 0 -Q 1 -P "$port" --stdout --gpu-mode=metax > "$run/server.log" 2>&1 &
server_pid=$!
ready=0
for ((i=0;i<25;i++)); do
 if ss -H -ltn "sport = :$port" | grep -q .; then ready=1; break; fi
 kill -0 "$server_pid" 2>/dev/null || break
 sleep 1
done
if [ "$ready" -ne 1 ]; then
 wait "$server_pid"; rc=$?
 cat "$run/server.log"
 printf 'SERVER_NOT_READY rc=%s\n' "$rc"
 exit 77
fi
timeout -k 3s 65s ssh -T "${opts[@]}" "$peer" bash -s -- "$run" "$priv" "$trace" "$port" "$sha" <<'CLIENT' > "$run/client_from_202.log" 2>&1
set -u
ulimit -c 0
run=$1; priv=$2; trace=$3; port=$4; sha=$5
printf '%s  %s\n' "$sha" "$run/vendor_atomic" '18ea061e598330ba56ac07d7f9b4691414884aaf805c7f7a1973b1bc5c028967' "$priv/urma/liburma-udma.so" | sha256sum -c - || exit 71
timeout -k 3s 55s env -u PERFTEST_DIAG_ATOMIC_GPU_TARGET LD_LIBRARY_PATH="$priv:/opt/maca-3.7.0/lib" LD_PRELOAD="$trace" \
 "$run/vendor_atomic" atomic_lat -d udmac0d1e2 -p 1 -A faa -s 8 -n 10 -I 0 \
 --eid_idx 0 -Q 1 -P "$port" -S 141.61.17.204 --stdout > "$run/client.log" 2>&1
rc=$?
printf '%s\n' "$rc" > "$run/client.exit"
cat "$run/client.log"
printf 'CLIENT_RC=%s\n' "$rc"
exit "$rc"
CLIENT
client_rc=$?
wait "$server_pid"; server_rc=$?
printf '%s\n' "$server_rc" > "$run/server.exit"
printf 'SERVER_RC=%s CLIENT_SSH_RC=%s\n' "$server_rc" "$client_rc"
cat "$run/server.log" "$run/client_from_202.log"
printf 'SHARED_URMA_USERS_AFTER_204\n'
fuser -v /dev/uburma/udmac0d1e2 2>&1 || true
ssh -n -T "${opts[@]}" "$peer" 'fuser -v /dev/uburma/udmac0d1e2 2>&1' || true
if [ "$server_rc" -ne 0 ] || [ "$client_rc" -ne 0 ]; then echo VENDOR_MICROTEST_PROCESS_ERROR; exit 81; fi
if grep -qE 'Failed CR status|Failed to post|Segmentation fault|VENDOR_ATOMIC_.*FAILED' "$run/server.log" "$run/client_from_202.log"; then echo VENDOR_MICROTEST_TRANSPORT_ERROR; exit 82; fi
if ! grep -q 'URMA_TRACE_BEGIN.*access=14 gpu=1' "$run/server.log" || ! grep -q 'strategy=METAX' "$run/server.log"; then echo VENDOR_GPU_TARGET_NOT_VERIFIED; exit 83; fi
if grep -q 'VENDOR_ATOMIC_GPU_TARGET value=21 expected=21 match=1' "$run/server.log" && \
   grep -q 'VENDOR_ATOMIC_HOST_RESULT value=20 expected=20 match=1' "$run/client_from_202.log"; then
 echo VENDOR_GPU_FADD_VALUE_PASS
else
 echo VENDOR_GPU_FADD_VALUE_MISMATCH
 exit 84
fi
