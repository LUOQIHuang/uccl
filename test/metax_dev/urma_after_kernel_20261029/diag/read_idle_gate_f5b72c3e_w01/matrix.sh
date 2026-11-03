#!/usr/bin/env bash
# A bounded host-memory READ/WRITE comparison after all benchmark work.
set -u
ulimit -c 0
export LC_ALL=C
base=/home/f00955680/metax_dev/urma_after_kernel_20261029/diag
run="$base/urma_read_matrix_56a7b3e1"
priv="$base/umdk_private_618957c3/lib"
source_bin=/home/g00953922/umdk_metax_isolated/bin/urma_perftest
trace="$base/urma_reg_trace_c6e72f9d/libtrace.so"
binary_sha=dab2a9f14178019b89f86f3f13593d927062cc5387eb598c8f84c4214400b25b
provider_sha=18ea061e598330ba56ac07d7f9b4691414884aaf805c7f7a1973b1bc5c028967
library_sha=56d6374553c2843c13eb7f7bc98a5bb65fa2fa4eb5f3b4a331f56cf126fb1f22
peer=root@141.61.17.202
ssh_opts=(-o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=8 -o ConnectionAttempts=1 -o ServerAliveInterval=10 -o ServerAliveCountMax=2)
[ "$(hostname -s)" = worker1 ] && [ ! -e "$run" ] && [ -x "$source_bin" ] && [ -f "$trace" ] || exit 69
printf '%s  %s\n' "$binary_sha" "$source_bin" | sha256sum -c - || exit 70
printf '%s  %s\n' "$provider_sha" "$priv/urma/liburma-udma.so" | sha256sum -c - || exit 70
printf '%s  %s\n' "$library_sha" "$priv/liburma.so.0.0.3" | sha256sum -c - || exit 70

check_local() {
  [ "$(uname -r)" = 6.6.0-gdr_f00955680_diag ] && \
    [ "$(cat /proc/sys/kernel/random/boot_id)" = 57c7db3d-4b1d-4c6e-987c-d52cfac20a55 ] && \
    [ "$(cat /sys/module/metax/parameters/runpm_link)" = 0 ] || return 70
  for dev in /dev/mxcd /dev/uburma/udmac*; do
    [ -e "$dev" ] || return 71
    timeout -k 1s 5s fuser "$dev" >/dev/null 2>&1; rc=$?
    [ "$rc" -eq 1 ] || { echo "STOP_DEVICE_BUSY=$dev:$rc"; return 72; }
  done
  gpu=$(timeout -k 2s 12s mx-smi 2>&1) || return 73
  printf '%s\n' "$gpu" | grep -q 'no process found' && \
    printf '%s\n' "$gpu" | grep -q Available || return 73
  journal=$(timeout -k 2s 20s journalctl -b -k -q --no-pager 2>/dev/null) || return 74
  if printf '%s\n' "$journal" | grep -qE 'kill queue timeout|destroy ce ringbuf failed|decrease in the number of available ringbufs'; then return 74; fi
  if [ -r /sys/kernel/debug/mxcd/ceq ] && awk 'NF==3 && $1 ~ /^[0-9]+$/ && $2 ~ /^[0-9]+$/ && $3 != "0x0" {found=1} END {exit !found}' /sys/kernel/debug/mxcd/ceq; then return 74; fi
  return 0
}

check_peer() {
  local peer_output peer_rc
  peer_output=$(timeout -k 2s 45s ssh -T "${ssh_opts[@]}" "$peer" bash -s <<'REMOTE'
set -u
[ "$(hostname -s)" = master ] && [ "$(uname -r)" = 6.6.0-gdr_f00955680_diag ] && \
 [ "$(cat /proc/sys/kernel/random/boot_id)" = f22963d1-0acd-481e-b7d8-7e86203a7ba9 ] && \
 [ "$(cat /sys/module/metax/parameters/runpm_link)" = 0 ] || exit 70
for dev in /dev/mxcd /dev/uburma/udmac*; do
 [ -e "$dev" ] || exit 71
 timeout -k 1s 5s fuser "$dev" >/dev/null 2>&1; rc=$?
 [ "$rc" -eq 1 ] || { echo "STOP_DEVICE_BUSY=$dev:$rc"; exit 72; }
done
gpu=$(timeout -k 2s 12s mx-smi 2>&1) || exit 73
printf '%s\n' "$gpu" | grep -q 'no process found' && \
 printf '%s\n' "$gpu" | grep -q Available || exit 73
journal=$(timeout -k 2s 20s journalctl -b -k -q --no-pager 2>/dev/null) || exit 74
if printf '%s\n' "$journal" | grep -qE 'kill queue timeout|destroy ce ringbuf failed|decrease in the number of available ringbufs'; then exit 74; fi
if [ -r /sys/kernel/debug/mxcd/ceq ] && awk 'NF==3 && $1 ~ /^[0-9]+$/ && $2 ~ /^[0-9]+$/ && $3 != "0x0" {found=1} END {exit !found}' /sys/kernel/debug/mxcd/ceq; then exit 74; fi
printf 'PEER_PREFLIGHT_OK\n'
REMOTE
  )
  peer_rc=$?
  printf '%s\n' "$peer_output"
  [ "$peer_rc" -eq 0 ] && printf '%s\n' "$peer_output" | grep -q '^PEER_PREFLIGHT_OK$'
}

check_local && check_peer || { echo STOP_PREFLIGHT; exit 75; }
mkdir -m 700 "$run" || exit 76
cp "$source_bin" "$run/perftest" || exit 76
ssh -n -T "${ssh_opts[@]}" "$peer" "test ! -e '$run' && mkdir -m 700 '$run'" || exit 76
scp -p "${ssh_opts[@]}" "$run/perftest" "$peer:$run/perftest" </dev/null || exit 77
ssh -n -T "${ssh_opts[@]}" "$peer" \
 "printf '%s  %s\\n' '$binary_sha' '$run/perftest' '$provider_sha' '$priv/urma/liburma-udma.so' '$library_sha' '$priv/liburma.so.0.0.3' | sha256sum -c -" || exit 78

index=0
while read -r label device operation bytes; do
  [ -n "$label" ] || continue
  index=$((index+1))
  port=$((31600+index))
  check_local && check_peer || { echo "STOP_PREFLIGHT_CASE=$label"; exit 75; }
  if ss -H -ltn "sport = :$port" | grep -q .; then echo "STOP_PORT_IN_USE=$port"; exit 79; fi
  server_log="$run/${label}_server.log"
  client_log="$run/${label}_client_from_202.log"
  printf 'CASE_BEGIN name=%s device=%s operation=%s bytes=%s iterations=10\n' "$label" "$device" "$operation" "$bytes"
  timeout -k 3s 90s env LD_LIBRARY_PATH="$priv:$priv/urma:/opt/maca-3.7.0/lib" \
    LD_PRELOAD="$trace" "$run/perftest" "$operation" -d "$device" -s "$bytes" -n 10 \
    -I 0 --eid_idx 0 --ctp -P "$port" --stdout > "$server_log" 2>&1 &
  server_pid=$!
  ready=0
  for ((i=0; i<55; i++)); do
    if ss -H -ltn "sport = :$port" | grep -q .; then ready=1; break; fi
    kill -0 "$server_pid" 2>/dev/null || break
    sleep 1
  done
  if [ "$ready" -ne 1 ]; then
    wait "$server_pid"; server_rc=$?
    cat "$server_log"
    echo "STOP_SERVER_NOT_READY case=$label rc=$server_rc"
    exit 80
  fi
  timeout -k 3s 85s ssh -T "${ssh_opts[@]}" "$peer" bash -s -- "$run" "$priv" "$trace" "$label" "$device" "$operation" "$bytes" "$port" <<'CLIENT' > "$client_log" 2>&1
set -u
ulimit -c 0
run=$1; priv=$2; trace=$3; label=$4; device=$5; operation=$6; bytes=$7; port=$8
for dev in /dev/mxcd /dev/uburma/udmac*; do
 timeout -k 1s 5s fuser "$dev" >/dev/null 2>&1; rc=$?
 [ "$rc" -eq 1 ] || { echo "STOP_DEVICE_BUSY=$dev:$rc"; exit 72; }
done
timeout -k 3s 60s env LD_LIBRARY_PATH="$priv:$priv/urma:/opt/maca-3.7.0/lib" \
 LD_PRELOAD="$trace" "$run/perftest" "$operation" -d "$device" -s "$bytes" -n 10 \
 -I 0 --eid_idx 0 --ctp -P "$port" -S 141.61.17.204 --stdout > "$run/${label}_client.log" 2>&1
rc=$?
cat "$run/${label}_client.log"
printf 'CLIENT_RC=%s\n' "$rc"
exit "$rc"
CLIENT
  client_rc=$?
  wait "$server_pid"; server_rc=$?
  printf 'CASE_OUTPUT_SERVER=%s\n' "$label"; cat "$server_log"
  printf 'CASE_OUTPUT_CLIENT=%s\n' "$label"; cat "$client_log"
  if [ "$server_rc" -ne 0 ] || [ "$client_rc" -ne 0 ]; then
    echo "STOP_NONZERO_EXIT case=$label server=$server_rc client=$client_rc"; exit 81
  fi
  for log in "$server_log" "$client_log"; do
    grep -qE 'URMA_TRACE_BEGIN.*gpu=0' "$log" && \
      grep -qE 'URMA_TRACE_DONE.*ok=1' "$log" || { echo "STOP_HOST_REGISTRATION_TRACE case=$label"; exit 82; }
    if grep -qE 'Segmentation fault|Traceback|mcFree failed|Failed poll jfc|STOP_DEVICE_BUSY' "$log"; then
      echo "STOP_UNEXPECTED_ERROR case=$label"; exit 82
    fi
  done
  if grep -q 'Failed CR status' "$server_log" "$client_log"; then
    if grep -h 'Failed CR status' "$server_log" "$client_log" | \
       grep -vE 'Failed CR status, id:[0-9]+, status:4\.' | grep -q .; then
      echo "STOP_UNEXPECTED_CR case=$label"; exit 82
    fi
    client_errors=$(grep -c 'Failed CR status' "$client_log" || true)
    server_errors=$(grep -c 'Failed CR status' "$server_log" || true)
    outcome="CR_LOCAL_ACCESS_4_client${client_errors}_server${server_errors}"
  elif awk -v bytes="$bytes" '$1 == bytes && $2 == 10 && $6 + 0 > 0 {ok=1} END {exit !ok}' "$client_log"; then
    outcome=PASS
  else
    echo "STOP_MISSING_VALID_RESULT case=$label"; exit 83
  fi
  printf 'CASE_RESULT name=%s device=%s operation=%s bytes=%s outcome=%s server_rc=%s client_rc=%s\n' \
    "$label" "$device" "$operation" "$bytes" "$outcome" "$server_rc" "$client_rc"
done <<'CASES'
c0_write_4k udmac0d1e2 write_lat 4096
c1_write_4k udmac1d1e2 write_lat 4096
c0_read_4k udmac0d1e2 read_lat 4096
c1_read_4k udmac1d1e2 read_lat 4096
c1_read_8 udmac1d1e2 read_lat 8
CASES
printf 'HOST_READ_MATRIX_FINISHED results_require_inspection\n'
