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

// Use the same system-scoped acquire primitive as the LL receive kernel.
// The loop is finite, so a missing notification cannot indefinitely occupy a GPU.
__global__ void poll_control(uint64_t* words, uint32_t const* payload,
                             uint64_t initial, uint32_t expected) {
  if (blockIdx.x || threadIdx.x) return;
  cuda::atomic_ref<uint64_t, cuda::thread_scope_system>(words[2])
      .store(1, cuda::memory_order_release);
  uint64_t value = initial;
  uint64_t const start = clock64();
  for (uint64_t i = 0; i < 100000000ULL; ++i) {
    value = cuda::atomic_ref<uint64_t, cuda::thread_scope_system>(words[0])
                .load(cuda::memory_order_acquire);
    if (value != initial || clock64() - start > 10000000000ULL) break;
  }
  uint64_t mismatches = 0;
  if (value != initial) {
    for (int i = 0; i < 1024; ++i)
      if (__builtin_nontemporal_load(payload + i) != expected) ++mismatches;
  }
  words[3] = value;
  words[4] = mismatches;
  words[5] = value != initial ? payload[0] : 0;
  cuda::atomic_ref<uint64_t, cuda::thread_scope_system>(words[6])
      .store(value != initial ? 1 : 2, cuda::memory_order_release);
}

static cudaStream_t polling_stream = nullptr;
extern "C" int gpu_poll_start(uint64_t* words, uint32_t const* payload,
                              uint64_t initial, uint32_t expected) {
  cudaError_t rc = cudaStreamCreateWithFlags(&polling_stream, cudaStreamNonBlocking);
  if (rc != cudaSuccess) return static_cast<int>(rc);
  poll_control<<<1, 1, 0, polling_stream>>>(words, payload, initial, expected);
  rc = cudaGetLastError();
  return static_cast<int>(rc);
}

extern "C" int gpu_poll_finish() {
  cudaError_t rc = cudaStreamSynchronize(polling_stream);
  cudaError_t free_rc = cudaStreamDestroy(polling_stream);
  polling_stream = nullptr;
  return static_cast<int>(rc == cudaSuccess ? free_rc : rc);
}
