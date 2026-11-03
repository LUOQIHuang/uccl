#!/usr/bin/env bash
set -u
[ "${UCCL_ROOT_ATOMIC_DIAG:-}" = 1 ] && [ "$#" -gt 0 ] || { echo ROOT_GUARD_INVALID; exit 70; }
port=0000:a0:08.0
pci=${UCCL_DIAG_SETPCI:-setpci}
orig=$("$pci" -s "$port" CAP_EXP+28.w) || exit 71
[[ "$orig" =~ ^[0-9a-fA-F]{4}$ ]] || exit 71
old=$((16#$orig))
if (( old & 0x40 )); then echo ROOT_REQ_ALREADY_ENABLED; exit 72; fi
armed=0
finish() {
  local saved=$? after
  trap - EXIT HUP INT TERM
  if [ "$armed" -eq 1 ]; then
    if ! "$pci" -s "$port" CAP_EXP+28.w=0000:0040; then echo ROOT_RESTORE_WRITE_FAILED; exit 89; fi
    after=$("$pci" -s "$port" CAP_EXP+28.w) || { echo ROOT_RESTORE_READ_FAILED; exit 89; }
    if ! [[ "$after" =~ ^[0-9a-fA-F]{4}$ ]] || (( (16#$after & 0x40) != (old & 0x40) )); then
      echo "ROOT_RESTORE_MISMATCH original=$orig after=$after"; exit 89
    fi
    printf 'ROOT_RESTORED original=%s after=%s\n' "$orig" "$after"
    if (( (16#$after & ~0x40) != (old & ~0x40) )); then echo ROOT_OTHER_BITS_CHANGED; fi
  fi
  exit "$saved"
}
trap finish EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
armed=1
"$pci" -s "$port" CAP_EXP+28.w=0040:0040 || { echo ROOT_ENABLE_WRITE_FAILED; exit 73; }
enabled=$("$pci" -s "$port" CAP_EXP+28.w) || exit 73
[[ "$enabled" =~ ^[0-9a-fA-F]{4}$ ]] || exit 73
printf 'ROOT_ENABLED original=%s enabled=%s\n' "$orig" "$enabled"
if (( !(16#$enabled & 0x40) || (16#$enabled & ~0x40) != (old & ~0x40) )); then
  echo ROOT_ENABLE_READBACK_FAILED; exit 74
fi
"$@"
rc=$?
printf 'ROOT_GUARDED_TEST_RC=%s\n' "$rc"
exit "$rc"
