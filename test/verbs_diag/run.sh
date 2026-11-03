#!/usr/bin/env bash
set -euo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
TASK_ROOT=${VERBS_DIAG_HOME:-${HOME:?HOME is required}}
DEV_ROOT=${VERBS_DIAG_DEV_ROOT:-"$TASK_ROOT/metax_dev"}
export VERBS_DIAG_HOME="$TASK_ROOT" VERBS_DIAG_DEV_ROOT="$DEV_ROOT"
# Copied activation scripts/venvs can retain their owner's absolute paths.
# Only source an activation script when the user explicitly selects it.
if [ -n "${VERBS_DIAG_ACTIVATE:-}" ]; then
  [ -f "$VERBS_DIAG_ACTIVATE" ] || { echo "ACTIVATION_SCRIPT_MISSING=$VERBS_DIAG_ACTIVATE" >&2; exit 2; }
  source "$VERBS_DIAG_ACTIVATE"
fi
export MACA_PATH=${MACA_PATH:-/opt/maca-3.7.0}
export MACA_CLANG_PATH="$MACA_PATH/mxgpu_llvm/bin"
export CUCC_PATH="$MACA_PATH/tools/cu-bridge"
export CUDA_HOME="$CUCC_PATH" CUDA_PATH="$CUCC_PATH" CUCC_TARGETS=xcore1000
export PATH="$CUCC_PATH/bin:$CUCC_PATH/tools:$MACA_CLANG_PATH:$MACA_PATH/bin:$PATH"
export LD_LIBRARY_PATH="$MACA_PATH/lib:$MACA_PATH/mxgpu_llvm/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
for candidate in "$DEV_ROOT/native-libs-3.7.0.38-G1NK1l" "$DEV_ROOT/native-libs-3.7.0.38-G1NK1l/lib" "$DEV_ROOT/native-libs-3.7.0.38-G1NK1l/lib64"; do
  if [ -d "$candidate" ]; then export LD_LIBRARY_PATH="$candidate:$LD_LIBRARY_PATH"; fi
done
if [ -n "${VERBS_DIAG_EXTRA_LIBRARY_DIRS:-}" ]; then
  export LD_LIBRARY_PATH="$VERBS_DIAG_EXTRA_LIBRARY_DIRS:$LD_LIBRARY_PATH"
fi
PRIVATE_LIB=${VERBS_DIAG_LIBRARY_DIR:-"$DEV_ROOT/urma_after_kernel_20261029/diag/umdk_private_618957c3/lib"}
if [ -d "$PRIVATE_LIB" ]; then export LD_LIBRARY_PATH="$PRIVATE_LIB${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"; fi
if [ -n "${VERBS_DIAG_PYTHON:-}" ]; then
  PYTHON_BIN=$VERBS_DIAG_PYTHON
elif [ -x "$DEV_ROOT/venv-maca-py312/bin/python" ]; then
  PYTHON_BIN="$DEV_ROOT/venv-maca-py312/bin/python"
else
  PYTHON_BIN=$(command -v python3) || { echo 'Set VERBS_DIAG_PYTHON to your MetaX Python interpreter' >&2; exit 2; }
fi
[ -x "$PYTHON_BIN" ] || { echo "PYTHON_NOT_EXECUTABLE=$PYTHON_BIN" >&2; exit 2; }
printf 'DIAG_ROOT=%s\nDIAG_PYTHON=%s\n' "$TASK_ROOT" "$PYTHON_BIN"
exec "$PYTHON_BIN" "$HERE/driver.py" "$@"
