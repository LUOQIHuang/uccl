#!/usr/bin/env bash
set -u
set -o pipefail
ulimit -c 0
export LC_ALL=C
base=/home/f00955680/metax_dev/urma_after_kernel_20261029/diag/fine_probe_stock_204
mode=${1:-}
bytes=${2:-}
attempt=${3:-}
case "$mode:$bytes" in
 normal:268435456|fine:268435456|normal:403243776|fine:403243776|normal:483892608|fine:483892608|normal:536870912|fine:536870912) ;;
 *) echo STOP_UNAPPROVED_CASE; exit 70 ;;
esac
case "$attempt" in ''|*[!a-z0-9_-]*) echo STOP_INVALID_ATTEMPT; exit 70 ;; esac
stock=6.6.0-145.3.22.153.oe2403sp3.aarch64
boot=$(cat /proc/sys/kernel/random/boot_id)
[ "$(hostname -s)" = worker1 ] && [ "$(uname -r)" = "$stock" ] || { echo STOP_WRONG_HOST_OR_KERNEL; exit 71; }
[ "$boot" != e640f58e-cdff-4f22-88ce-a405ab858e3e ] || { echo STOP_NEED_NEW_BOOT; exit 71; }
printf '%s  %s\n' 'f0eb28b8fd2d3b205ba1a093487271ca2412891fbde24e70c7dbc3183178f86f' "$base/probe" | sha256sum -c - || exit 72
command -v fuser >/dev/null || { echo STOP_FUSER_UNAVAILABLE; exit 72; }
run="$base/runs/$boot/${mode}_${bytes}_${attempt}"
[ ! -e "$run" ] || { echo STOP_CASE_ALREADY_EXISTS; exit 73; }
mkdir -p "$run/tmp" || exit 74
printf 'HOST=%s KERNEL=%s BOOT_ID=%s CASE=%s:%s\n' "$(hostname -s)" "$(uname -r)" "$boot" "$mode" "$bytes"
sha256sum "$base/probe" "$base/probe.cpp" /opt/maca-3.7.0/lib/libmcruntime.so
timeout -k 2s 12s mx-smi > "$run/gpu_before.txt" 2>&1 || { cat "$run/gpu_before.txt"; exit 75; }
cat "$run/gpu_before.txt"
grep -q 'no process found' "$run/gpu_before.txt" && grep -q Available "$run/gpu_before.txt" || { echo STOP_GPU_BUSY; exit 76; }
fuser /dev/mxcd > "$run/device_users.txt" 2>&1
user_rc=$?
if [ "$user_rc" -eq 0 ]; then cat "$run/device_users.txt"; echo STOP_DEVICE_IN_USE; exit 77; fi
[ "$user_rc" -eq 1 ] || { echo STOP_DEVICE_USER_CHECK_FAILED; exit 77; }
source /home/f00955680/metax_dev/activate-uccl.sh || exit 78
export CUDA_VISIBLE_DEVICES=0 TMPDIR="$run/tmp"
export LD_LIBRARY_PATH="/opt/maca-3.7.0/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
printf 'CASE_BEGIN=%s\n' "$(date -Ins)"
timeout -k 10s 900s "$base/probe" "$mode" "$bytes" 2>&1 | tee "$run/probe.log"
rc=${PIPESTATUS[0]}
printf 'CASE_END=%s CASE_RC=%s\n' "$(date -Ins)" "$rc"
printf '%s\n' "$rc" > "$run/status"
if [ "$rc" -ne 0 ]; then echo STOP_AFTER_FIRST_FAILURE; exit "$rc"; fi
grep -q "PROBE_OK MODE=$mode LOGICAL_BYTES=$bytes" "$run/probe.log" || exit 80
printf 'CASE_OK=%s:%s\n' "$mode" "$bytes"
