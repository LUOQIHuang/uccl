export MACA_PATH=/opt/maca-3.7.0
export MACA_CLANG_PATH=$MACA_PATH/mxgpu_llvm/bin
export CUCC_PATH=$MACA_PATH/tools/cu-bridge
export CUDA_PATH=$CUCC_PATH
export PATH=$CUCC_PATH/bin:$CUCC_PATH/tools:$MACA_CLANG_PATH:$MACA_PATH/bin:$PATH
export LD_LIBRARY_PATH=$MACA_PATH/lib:$MACA_PATH/mxgpu_llvm/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}
