#include "ep_configs.cuh"
__global__ void precision_compile_probe(float* out) {
  auto packed=__nv_cvt_float2_to_fp8x2(make_float2(1.0f,2.0f),__NV_SATFINITE,__NV_E4M3);
  out[0]=__half2float(__float2half(1.0f));(void)packed;
}
