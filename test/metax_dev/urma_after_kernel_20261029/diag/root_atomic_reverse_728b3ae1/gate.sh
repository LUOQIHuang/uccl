#!/usr/bin/env bash
set -u
run=/home/f00955680/metax_dev/urma_after_kernel_20261029/diag/root_atomic_reverse_728b3ae1
gpu=$(timeout 15s mx-smi) || exit 72
printf '%s\n' "$gpu" | grep -q Available && printf '%s\n' "$gpu" | grep -q 'no process found' || { echo GPU_BUSY_STOP; exit 72; }
fuser /dev/mxcd >/dev/null 2>&1; frc=$?; [ "$frc" -eq 1 ] || { echo GPU_DEVICE_BUSY_OR_UNKNOWN="$frc"; exit 72; }
unset UCCL_DIAG_SETPCI
export UCCL_ROOT_ATOMIC_DIAG=1
exec bash "$run/guard.sh" bash "$run/server.sh"
