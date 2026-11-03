#!/bin/bash
set -euo pipefail
umask 077
release=6.6.0-gdr_f00955680_diag
package=/home/f00955680/metax_dev/urma_after_kernel_20261029/diag/kernel_package_e9a4c331
source_dir="$package/stripped/root/lib/modules/$release"
target_dir="/usr/lib/modules/$release"
manifest="$package/modules.sha256"
image="$package/Image-$release"
initrd="$package/initramfs-$release.img"

if test "$(id -u)" != 0; then echo '必须在 204 上以 root 运行'; exit 1; fi
if test "$(hostname -s)" != worker1; then echo '节点不对：此脚本只适用于 204/worker1'; exit 2; fi
if test "$(uname -r)" != '6.6.0-gdr_g00953922_v1+'; then echo '当前内核已变化，停止并重新核对'; exit 3; fi
if test -e "$target_dir" || test -L "$target_dir"; then echo '目标内核模块目录已存在，停止以防覆盖'; exit 4; fi
test -d "$source_dir"
test -f "$manifest"
printf '%s  %s\n' \
  '8f287f925e91a033cd530437786b439c9762dcfac9cbe18fb4e72cd3c7255300' "$image" \
  'f4c7ffb3e86e89be773956aca03b62674a474a47bc72f4160375b5ad85f3e7fc' "$initrd" \
  '56d6374553c2843c13eb7f7bc98a5bb65fa2fa4eb5f3b4a331f56cf126fb1f22' /home/g00953922/umdk_metax_isolated/lib/liburma.so.0.0.3 \
  'a1220e32ab34cdcb52142ac122d44da0cf509c16b0cbcc9c3fdf70e30c583daa' /usr/lib64/liburma.so.0 \
  'ad8bb0e5153549f4b76f1a8eeffcab62f503a1114e2373ceed8784c45e88105a' /usr/lib64/libummu.so.1 | sha256sum -c -
available_kb=$(df -Pk /usr/lib/modules | awk 'NR==2 {print $4}')
if test "$available_kb" -lt 524288; then echo '系统盘剩余空间不足 512 MiB'; exit 5; fi
stage=$(mktemp -d "/usr/lib/modules/.${release}.stage.XXXXXX")
cleanup() { if test -n "${stage:-}" && test -d "$stage"; then rm -rf -- "$stage"; fi; }
trap cleanup EXIT
cp -a -- "$source_dir/." "$stage/"
(cd "$stage" && sha256sum -c "$manifest" > "$package/install_modules_verify.log")
mv -T -n -- "$stage" "$target_dir"
if test -e "$stage"; then echo '目标目录被其他操作创建，未覆盖'; exit 6; fi
stage=''
modprobe -C /dev/null -d / -S "$release" --show-depends udma_metax_p2p_bridge > "$package/installed_dependencies.txt"
grep -qE '/updates/.*/udma_metax_p2p_bridge\.ko' "$package/installed_dependencies.txt"
grep -qE '/updates/.*/udma\.ko' "$package/installed_dependencies.txt"
grep -qE '/extra/.*/metax\.ko' "$package/installed_dependencies.txt"
printf '%s  %s\n' \
  '56d6374553c2843c13eb7f7bc98a5bb65fa2fa4eb5f3b4a331f56cf126fb1f22' /home/g00953922/umdk_metax_isolated/lib/liburma.so.0.0.3 \
  'a1220e32ab34cdcb52142ac122d44da0cf509c16b0cbcc9c3fdf70e30c583daa' /usr/lib64/liburma.so.0 \
  'ad8bb0e5153549f4b76f1a8eeffcab62f503a1114e2373ceed8784c45e88105a' /usr/lib64/libummu.so.1 | sha256sum -c -
echo "MODULES_PREPARED=$target_dir"
echo '未运行 DKMS、未修改已有内核、UMDK、GRUB 默认项或 /boot。'
