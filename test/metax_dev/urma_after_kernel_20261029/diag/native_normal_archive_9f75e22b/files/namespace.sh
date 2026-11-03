#!/usr/bin/env bash
set -eu
mount -t tmpfs -o size=2g,mode=1777 tmpfs /dev/shm
printf 'ISOLATED_MOUNT_NAMESPACE=%s\n' "$(readlink /proc/self/ns/mnt)"
if find /dev/shm -maxdepth 1 -name 'uccl_barrier_*' -print -quit | grep -q .; then
 echo ISOLATED_SHM_NOT_EMPTY; exit 80
fi
exec bash "$@"
