#!/usr/bin/env bash
set -u
ulimit -c 0
export LC_ALL=C
base=/home/f00955680/metax_dev/urma_after_kernel_20261029/diag
run="$base/urma_write_slots_case_slot_1024_disjoint"
priv="$base/umdk_private_618957c3/lib"
src="$base/urma_write_slots_build_b5f8e901/write_slots"
trace="$base/urma_reg_trace_c6e72f9d/libtrace.so"
port=29758
peer=root@141.61.17.202
ssh_opts=(-o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=8 -o ConnectionAttempts=1)
[ "$(hostname -s)" = worker1 ] && [ -x "$src" ] && [ -f "$trace" ] && [ ! -e "$run" ] || exit 69
[ "$(uname -r)" = 6.6.0-gdr_f00955680_diag ] && [ "$(cat /sys/module/metax/parameters/runpm_link)" = 0 ] || exit 70
[ "$(cat /proc/sys/kernel/random/boot_id)" = 57c7db3d-4b1d-4c6e-987c-d52cfac20a55 ] || exit 70
if ! journal=$(journalctl -b -k -q --no-pager 2>/dev/null); then echo JOURNAL_UNAVAILABLE; exit 70; fi
if printf '%s\n' "$journal" | grep -qE 'kill queue timeout|destroy ce ringbuf failed|decrease in the number of available ringbufs'; then echo STOP_QUEUE_ERROR; exit 70; fi
if [ -r /sys/kernel/debug/mxcd/ceq ] && awk 'NF==3 && $1 ~ /^[0-9]+$/ && $2 ~ /^[0-9]+$/ && $3 != "0x0" {found=1} END {exit !found}' /sys/kernel/debug/mxcd/ceq; then echo STOP_NONZERO_QUEUE; exit 70; fi
for dev in /dev/mxcd /dev/uburma/udmac*; do
 [ -e "$dev" ] || exit 71
 timeout -k 1s 5s fuser "$dev" >/dev/null 2>&1; rc=$?
 [ "$rc" -eq 1 ] || { echo STOP_DEVICE_BUSY="$dev:$rc"; exit 72; }
done
gpu=$(timeout -k 2s 12s mx-smi 2>&1) || exit 72
printf '%s\n' "$gpu" | grep -q 'no process found' && printf '%s\n' "$gpu" | grep -q Available || { echo STOP_GPU_NOT_IDLE; exit 72; }
if ss -H -ltn "sport = :$port" | grep -q .; then echo STOP_PORT_IN_USE; exit 75; fi
mkdir -m 700 "$run" || exit 76
cp "$src" "$run/write_slots" || exit 76
ssh -T "${ssh_opts[@]}" "$peer" bash -s -- "$run" <<'PREFLIGHT'
set -u
run=$1
[ "$(hostname -s)" = master ] && [ "$(uname -r)" = 6.6.0-gdr_f00955680_diag ] && [ "$(cat /sys/module/metax/parameters/runpm_link)" = 0 ] || exit 70
[ "$(cat /proc/sys/kernel/random/boot_id)" = f22963d1-0acd-481e-b7d8-7e86203a7ba9 ] || exit 70
if ! journal=$(journalctl -b -k -q --no-pager 2>/dev/null); then exit 70; fi
if printf '%s\n' "$journal" | grep -qE 'kill queue timeout|destroy ce ringbuf failed|decrease in the number of available ringbufs'; then echo STOP_QUEUE_ERROR; exit 70; fi
if [ -r /sys/kernel/debug/mxcd/ceq ] && awk 'NF==3 && $1 ~ /^[0-9]+$/ && $2 ~ /^[0-9]+$/ && $3 != "0x0" {found=1} END {exit !found}' /sys/kernel/debug/mxcd/ceq; then echo STOP_NONZERO_QUEUE; exit 70; fi
for dev in /dev/mxcd /dev/uburma/udmac*; do
 [ -e "$dev" ] || exit 71
 timeout -k 1s 5s fuser "$dev" >/dev/null 2>&1; rc=$?
 [ "$rc" -eq 1 ] || { echo STOP_DEVICE_BUSY="$dev:$rc"; exit 72; }
done
gpu=$(timeout -k 2s 12s mx-smi 2>&1) || exit 72
printf '%s\n' "$gpu" | grep -q 'no process found' && printf '%s\n' "$gpu" | grep -q Available || { echo STOP_GPU_NOT_IDLE; exit 72; }
[ ! -e "$run" ] && mkdir -m 700 "$run"
PREFLIGHT
peer_rc=$?
[ "$peer_rc" -eq 0 ] || { echo STOP_PEER_PREFLIGHT="$peer_rc"; exit 76; }
scp -p "${ssh_opts[@]}" "$run/write_slots" "$peer:$run/write_slots" </dev/null || exit 77
sha=$(sha256sum "$run/write_slots" | awk '{print $1}')
[ "$sha" = "444676fb9648bbdf363049faa12670e2bd107d52b5640999339b3a156ec96f79" ] || { echo PROBE_SHA_MISMATCH; exit 78; }
ssh -n -T "${ssh_opts[@]}" "$peer" "printf '%s  %s\n' '$sha' '$run/write_slots' | sha256sum -c -" || exit 78
snapshot_local() {
 label=$1
 for chip in 0 1; do
  printf 'HOST=204 LABEL=%s CHIP=%s TIME=%s\n' "$label" "$chip" "$(date -Ins)" > "$run/${label}_$chip.txt"
  timeout -k 1s 6s ubctl -c "$chip" -d 1 -m dl -p 1 -f pkt_stats >> "$run/${label}_$chip.txt" 2>&1 || return 1
 done
}
snapshot_peer() {
 ssh -T "${ssh_opts[@]}" "$peer" bash -s -- "$run" "$1" <<'SNAPSHOT'
set -u
run=$1; label=$2
for chip in 0 1; do
 printf 'HOST=202 LABEL=%s CHIP=%s TIME=%s\n' "$label" "$chip" "$(date -Ins)" > "$run/${label}_$chip.txt"
 timeout -k 1s 6s ubctl -c "$chip" -d 1 -m dl -p 1 -f pkt_stats >> "$run/${label}_$chip.txt" 2>&1 || exit 1
done
SNAPSHOT
}
snapshot_local idle_before && snapshot_peer idle_before || exit 79
sleep 2
snapshot_local before && snapshot_peer before || exit 79
printf 'WRITE_BIN_SHA=%s MAIN=finegrained CONTROL=host_mapped BACKEND=urma DEVICE=udmac0d1e2 TM=rc\n' "$sha"
timeout -k 3s 100s env LD_LIBRARY_PATH="$priv:/opt/maca-3.7.0/lib" LD_PRELOAD="$trace" UCCL_URMA_DEV_NAME=udmac0d1e2 UCCL_URMA_TM=rc UCCL_DIAG_FINEGRAIN=1 UCCL_DIAG_PAYLOAD_BYTES=1048576 UCCL_DIAG_ITERATIONS=256 UCCL_DIAG_DISJOINT=1 "$run/write_slots" listen "$port" 0 > "$run/server.log" 2>&1 &
server_pid=$!
ready=0
for ((i=0;i<55;i++)); do
 if ss -H -ltn "sport = :$port" | grep -q .; then ready=1; break; fi
 kill -0 "$server_pid" 2>/dev/null || break
 sleep 1
done
if [ "$ready" -ne 1 ]; then
 echo SERVER_NOT_READY
 wait "$server_pid"; server_rc=$?
 cat "$run/server.log"; echo SERVER_RC="$server_rc"; exit 80
fi
ssh -T "${ssh_opts[@]}" "$peer" bash -s -- "$run" "$priv" "$trace" "$port" <<'CLIENT' > "$run/client_from_202.log" 2>&1
set -u
ulimit -c 0
run=$1; priv=$2; trace=$3; port=$4
for dev in /dev/mxcd /dev/uburma/udmac*; do
 timeout -k 1s 5s fuser "$dev" >/dev/null 2>&1; rc=$?
 [ "$rc" -eq 1 ] || { echo STOP_DEVICE_BUSY="$dev:$rc"; exit 72; }
done
timeout -k 3s 70s env LD_LIBRARY_PATH="$priv:/opt/maca-3.7.0/lib" LD_PRELOAD="$trace" UCCL_URMA_DEV_NAME=udmac0d1e2 UCCL_URMA_TM=rc UCCL_DIAG_FINEGRAIN=1 UCCL_DIAG_PAYLOAD_BYTES=1048576 UCCL_DIAG_ITERATIONS=256 UCCL_DIAG_DISJOINT=1 "$run/write_slots" 141.61.17.204 "$port" 0 > "$run/client.log" 2>&1
rc=$?
printf '%s\n' "$rc" > "$run/client.exit"
cat "$run/client.log"
echo CLIENT_RC="$rc"
exit "$rc"
CLIENT
client_rc=$?
wait "$server_pid"; server_rc=$?
snapshot_local after; local_snap_rc=$?
snapshot_peer after; peer_snap_rc=$?
printf 'SERVER_RC=%s CLIENT_SSH_RC=%s LOCAL_COUNTER_RC=%s PEER_COUNTER_RC=%s\n' "$server_rc" "$client_rc" "$local_snap_rc" "$peer_snap_rc"
cat "$run/server.log" "$run/client_from_202.log"
for label in idle_before before after; do for chip in 0 1; do cat "$run/${label}_$chip.txt"; done; done
ssh -n -T "${ssh_opts[@]}" "$peer" "cat '$run'/idle_before_*.txt '$run'/before_*.txt '$run'/after_*.txt" || exit 81
[ "$server_rc" -eq 0 ] && [ "$client_rc" -eq 0 ] && [ "$local_snap_rc" -eq 0 ] && [ "$peer_snap_rc" -eq 0 ] && grep -q 'WRITE_DATA_MATCH=1' "$run/server.log" && grep -q 'WRITE_DATA_MATCH=1' "$run/client_from_202.log" && grep -q 'MAPPED_HOST_GPU_FADD_PASS' "$run/server.log" && grep -q 'MAPPED_HOST_GPU_FADD_PASS' "$run/client_from_202.log" || exit 82
grep -q 'WRITE_LOAD layout=disjoint iterations=256 bytes_each=1048576 depth=32 payload_bytes=268435456 ' "$run/server.log" && grep -q 'WRITE_LOAD layout=disjoint iterations=256 bytes_each=1048576 depth=32 payload_bytes=268435456 ' "$run/client_from_202.log" || exit 83
printf 'TWO_NODE_WRITE_LOAD_PASS\n'
