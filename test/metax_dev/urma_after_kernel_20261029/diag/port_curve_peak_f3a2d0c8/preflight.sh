#!/bin/bash
set -u
base=/home/f00955680/metax_dev/urma_after_kernel_20261029/diag
[ "$(uname -r)" = 6.6.0-gdr_f00955680_diag ] && [ "$(cat /sys/module/metax/parameters/runpm_link)" = 0 ] || exit 70
if ! journal=$(journalctl -b -k -q --no-pager 2>/dev/null); then exit 71; fi
if printf '%s\n' "$journal" | grep -qE 'kill queue timeout|destroy ce ringbuf failed|decrease in the number of available ringbufs'; then echo STOP_QUEUE_ERROR; exit 72; fi
if [ -r /sys/kernel/debug/mxcd/ceq ] && awk 'NF==3 && $1 ~ /^[0-9]+$/ && $2 ~ /^[0-9]+$/ && $3 != "0x0" {found=1} END {exit !found}' /sys/kernel/debug/mxcd/ceq; then echo STOP_NONZERO_QUEUE; exit 73; fi
timeout -k 1s 5s fuser /dev/mxcd >/dev/null 2>&1; rc=$?
[ "$rc" -eq 1 ] || { echo STOP_GPU_BUSY_OR_CHECK_FAILED="$rc"; exit 74; }
gpu=$(timeout -k 2s 12s mx-smi 2>&1) || exit 75
printf '%s\n' "$gpu" | grep -q 'no process found' && printf '%s\n' "$gpu" | grep -q Available || { echo STOP_GPU_NOT_IDLE; exit 76; }
atomic_ctl=$(setpci -s 0000:a0:08.0 CAP_EXP+28.w) || exit 77
[[ "$atomic_ctl" =~ ^[0-9a-fA-F]{4}$ ]] && (( 16#$atomic_ctl & 0x40 )) || exit 77
echo PREFLIGHT_OK host="$(hostname -s)" root_atomic="$atomic_ctl"
echo NETWORK_OPENERS_BEGIN
for f in /dev/uburma/udmac0d1e2 /dev/infiniband/uverbs*; do
 [ -e "$f" ] || continue
 printf '%s: ' "$f"
 timeout -k 1s 3s fuser "$f" 2>/dev/null || true
done
echo NETWORK_OPENERS_END
