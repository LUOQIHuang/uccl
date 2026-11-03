#!/bin/bash
set -euo pipefail
sandbox=$1
stage=$2
release=$3
for d in usr etc var proc sys work dev run tmp root; do mkdir -p "$sandbox/$d"; done
for d in usr etc var proc sys; do
 mount --bind "/$d" "$sandbox/$d"
 mount -o remount,bind,ro "$sandbox/$d"
done
for d in bin sbin lib lib64; do
 if test -L "/$d"; then target=$(readlink "/$d"); target=${target#/}; ln -s "$target" "$sandbox/$d"
 elif test -d "/$d"; then mkdir -p "$sandbox/$d"; mount --bind "/$d" "$sandbox/$d"; mount -o remount,bind,ro "$sandbox/$d"; fi
done
ln -s /proc/self/fd "$sandbox/dev/fd"
ln -s fd/0 "$sandbox/dev/stdin"
ln -s fd/1 "$sandbox/dev/stdout"
ln -s fd/2 "$sandbox/dev/stderr"
for d in null zero random urandom; do : > "$sandbox/dev/$d"; mount --bind "/dev/$d" "$sandbox/dev/$d"; done
mount --bind "$stage" "$sandbox/work"
mount --bind "$stage/root/lib/modules" "$sandbox/usr/lib/modules"
mount -o remount,bind,ro "$sandbox/usr/lib/modules"
chroot "$sandbox" /usr/bin/python3 -c 'import errno,os; p="/etc/passwd"; fd=-1
try: fd=os.open(p,os.O_WRONLY)
except OSError as e:
 if e.errno!=errno.EROFS: raise
else:
 os.close(fd); raise SystemExit("shared filesystem unexpectedly writable")
p="/work/.mount_test"; fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600); os.close(fd); os.unlink(p)
print("PRIVATE_NAMESPACE_OK",flush=True)'
chroot "$sandbox" /usr/bin/env TMPDIR=/work/tmp PATH=/usr/sbin:/usr/bin:/sbin:/bin /usr/bin/dracut \
 --conf /dev/null --confdir /work/empty_config --no-hostonly --no-hostonly-cmdline \
 --kmoddir "/usr/lib/modules/$release" --kver "$release" --tmpdir /work/tmp --logfile /work/dracut.log \
 --add-drivers 'metax udma udma_metax_p2p_bridge ummu ummu_core ubcore uburma' "/work/initramfs-$release.img"
chroot "$sandbox" /usr/bin/env TMPDIR=/work/tmp /usr/bin/lsinitrd -m "/work/initramfs-$release.img" > "$stage/initramfs_modules.txt"
chroot "$sandbox" /usr/bin/env TMPDIR=/work/tmp /usr/bin/lsinitrd "/work/initramfs-$release.img" > "$stage/initramfs_contents.txt"
