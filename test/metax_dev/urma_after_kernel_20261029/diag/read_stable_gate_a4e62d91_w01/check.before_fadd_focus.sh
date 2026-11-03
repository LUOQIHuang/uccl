set -u
expected=$1
[ "$(cat /proc/sys/kernel/random/boot_id)" = "$expected" ] || { echo IDLE_FATAL_BOOT; exit 3; }
[ "$(uname -r)" = 6.6.0-gdr_f00955680_diag ] && [ "$(cat /sys/module/metax/parameters/runpm_link)" = 0 ] || { echo IDLE_FATAL_ENV; exit 3; }
for dev in /dev/mxcd /dev/uburma/udmac*; do
 [ -e "$dev" ] || { echo IDLE_FATAL_MISSING_DEVICE; exit 3; }
 timeout -k 1s 5s fuser "$dev" >/dev/null 2>&1; rc=$?
 [ "$rc" -eq 1 ] && continue
 [ "$rc" -eq 0 ] && { echo IDLE_BUSY_DEVICE; exit 2; }
 echo IDLE_FATAL_FUSER; exit 3
done
gpu=$(timeout -k 2s 12s mx-smi 2>&1) || { echo IDLE_FATAL_GPU_QUERY; exit 3; }
printf '%s\n' "$gpu" | grep -q Available || { echo IDLE_FATAL_GPU_STATE; exit 3; }
printf '%s\n' "$gpu" | grep -q 'no process found' || { echo IDLE_BUSY_GPU; exit 2; }
echo IDLE_CHECK_OK
