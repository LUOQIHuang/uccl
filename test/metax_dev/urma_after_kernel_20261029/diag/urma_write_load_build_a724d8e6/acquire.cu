#include <cuda_runtime.h>
#include <cuda/atomic>
#include <cstdint>

__global__ void load_control_words(uint64_t* words, uint64_t* output) {
  if (blockIdx.x == 0 && threadIdx.x == 0) {
    output[0] = cuda::atomic_ref<uint64_t, cuda::thread_scope_system>(words[0])
                    .load(cuda::memory_order_acquire);
    output[1] = cuda::atomic_ref<uint64_t, cuda::thread_scope_system>(words[1])
                    .load(cuda::memory_order_acquire);
  }
}

extern "C" int gpu_acquire_control(uint64_t* mapped, uint64_t* host_result) {
  uint64_t* output = nullptr;
  cudaError_t rc = cudaMalloc(reinterpret_cast<void**>(&output), 2 * sizeof(uint64_t));
  if (rc != cudaSuccess) return static_cast<int>(rc);
  load_control_words<<<1, 1>>>(mapped, output);
  rc = cudaGetLastError();
  if (rc == cudaSuccess) rc = cudaDeviceSynchronize();
  if (rc == cudaSuccess)
    rc = cudaMemcpy(host_result, output, 2 * sizeof(uint64_t), cudaMemcpyDeviceToHost);
  cudaError_t free_rc = cudaFree(output);
  return static_cast<int>(rc == cudaSuccess ? free_rc : rc);
}
