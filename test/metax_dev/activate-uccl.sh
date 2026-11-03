if [ -f /home/f00955680/metax_dev/env.sh ]; then
    source /home/f00955680/metax_dev/env.sh || return 1
fi
export MACA_PATH=/opt/maca-3.7.0
export MACA_CLANG_PATH="$MACA_PATH/mxgpu_llvm/bin"
export CUCC_PATH="$MACA_PATH/tools/cu-bridge"
export CUDA_HOME="$CUCC_PATH" CUDA_PATH="$CUCC_PATH"
export CUCC_TARGETS=xcore1000 PYTORCH_NVCC="$CUCC_PATH/bin/cucc"
export PATH="$CUCC_PATH/bin:$CUCC_PATH/tools:$MACA_CLANG_PATH:$PATH"
export LD_LIBRARY_PATH="$MACA_PATH/lib:$MACA_PATH/mxgpu_llvm/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
source /home/f00955680/metax_dev/venv-maca-py312/bin/activate
