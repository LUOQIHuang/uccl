#!/bin/bash
set -euo pipefail
umask 077
package=/home/f00955680/metax_dev/urma_after_kernel_20261029/diag/kernel_package_e9a4c331
candidate="$package/grub_custom_204.cfg"
bootdir=/boot/efi/EFI/openEuler
target="$bootdir/custom.cfg"
grubcfg="$bootdir/grub.cfg"
release=6.6.0-gdr_f00955680_diag

test "$(id -u)" = 0
test "$(hostname -s)" = worker1
test "$(uname -r)" = '6.6.0-gdr_g00953922_v1+'
test ! -e "$target"
test ! -L "$target"
test -d "/usr/lib/modules/$release"
printf '%s  %s\n' \
 '0f76ef8f4b27d74cb516c75d0f499e01c8754d8945341558ac34d7fa0db8ebc1' "$candidate" \
 'ed28f4a9a8a424bcc3530dddfbbbfe072350ef24b26bf4cb6c014f7ef210789a' "$grubcfg" \
 '8f287f925e91a033cd530437786b439c9762dcfac9cbe18fb4e72cd3c7255300' "$package/Image-$release" \
 'f4c7ffb3e86e89be773956aca03b62674a474a47bc72f4160375b5ad85f3e7fc' "$package/initramfs-$release.img" | sha256sum -c -
grub2-script-check "$candidate"
test "$(grub2-probe --target=fs_uuid /home)" = da3a49e7-e5f7-4d5e-8c72-23f4d54cec0f
stage=$(mktemp "$bootdir/.uccl-grub-stage.XXXXXX")
cleanup() { if test -n "${stage:-}" && test -e "$stage"; then rm -f -- "$stage"; fi; }
trap cleanup EXIT
cp -- "$candidate" "$stage"
chmod 0644 "$stage"
cmp -s "$candidate" "$stage"
sync -f "$stage"
mv -T -n -- "$stage" "$target"
if test -e "$stage"; then echo '目标已存在，未覆盖'; exit 6; fi
stage=''
cmp -s "$candidate" "$target"
printf '%s  %s\n' 'ed28f4a9a8a424bcc3530dddfbbbfe072350ef24b26bf4cb6c014f7ef210789a' "$grubcfg" | sha256sum -c -
printf '%s  %s\n' \
 '56d6374553c2843c13eb7f7bc98a5bb65fa2fa4eb5f3b4a331f56cf126fb1f22' /home/g00953922/umdk_metax_isolated/lib/liburma.so.0.0.3 \
 'a1220e32ab34cdcb52142ac122d44da0cf509c16b0cbcc9c3fdf70e30c583daa' /usr/lib64/liburma.so.0 \
 'ad8bb0e5153549f4b76f1a8eeffcab62f503a1114e2373ceed8784c45e88105a' /usr/lib64/libummu.so.1 | sha256sum -c -
printf 'TEMPORARY_MENU_INSTALLED=%s\n' "$target"
echo '现有 GRUB 主配置及默认项未修改；重启仍由用户操作。'
