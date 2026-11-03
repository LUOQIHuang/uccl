// Small local probe of the same system-scope acquire used by the LL kernel.
#include <cuda_runtime.h>
#include <cuda/atomic>
#include <cstdint>
#include <cstdio>
#include <cstdlib>

__global__ void load_mapped_words(uint64_t* words, uint64_t* output) {
  if (blockIdx.x == 0 && threadIdx.x == 0) {
    output[0] = cuda::atomic_ref<uint64_t, cuda::thread_scope_system>(words[0])
                    .load(cuda::memory_order_acquire);
    output[1] = cuda::atomic_ref<uint64_t, cuda::thread_scope_system>(words[1])
                    .load(cuda::memory_order_acquire);
  }
}

static void check(cudaError_t rc, char const* name) {
  if (rc != cudaSuccess) {
    std::fprintf(stderr, "%s failed: %d (%s)\n", name, static_cast<int>(rc),
                 cudaGetErrorString(rc));
    std::exit(1);
  }
}

int main() {
  check(cudaSetDevice(0), "cudaSetDevice");
  uint64_t* host = nullptr;
  uint64_t* device_alias = nullptr;
  uint64_t* output = nullptr;
  check(cudaHostAlloc(reinterpret_cast<void**>(&host), 8192, cudaHostAllocMapped),
        "cudaHostAlloc");
  check(cudaHostGetDevicePointer(reinterpret_cast<void**>(&device_alias), host, 0),
        "cudaHostGetDevicePointer");
  check(cudaMalloc(reinterpret_cast<void**>(&output), 2 * sizeof(uint64_t)),
        "cudaMalloc output");
  std::printf("HOST=%p GPU_ALIAS=%p HOST_MOD4096=%zu\n", static_cast<void*>(host),
              static_cast<void*>(device_alias),
              static_cast<size_t>(reinterpret_cast<uintptr_t>(host) % 4096));
  std::fflush(stdout);
  for (uint64_t step = 0; step < 2; ++step) {
    host[0] = 11 + step;
    host[1] = 17 - 3 * step;
    __atomic_thread_fence(__ATOMIC_RELEASE);
    load_mapped_words<<<1, 1>>>(device_alias, output);
    check(cudaGetLastError(), "kernel launch");
    check(cudaDeviceSynchronize(), "kernel synchronize");
    uint64_t actual[2]{};
    check(cudaMemcpy(actual, output, sizeof(actual), cudaMemcpyDeviceToHost),
          "cudaMemcpy output");
    std::printf("STEP=%llu CPU=%llu,%llu GPU_ACQUIRE=%llu,%llu\n",
                static_cast<unsigned long long>(step),
                static_cast<unsigned long long>(host[0]),
                static_cast<unsigned long long>(host[1]),
                static_cast<unsigned long long>(actual[0]),
                static_cast<unsigned long long>(actual[1]));
    std::fflush(stdout);
    if (actual[0] != 11 + step || actual[1] != 17 - 3 * step) return 2;
  }
  check(cudaFree(output), "cudaFree output");
  check(cudaFreeHost(host), "cudaFreeHost");
  std::puts("GPU_MAPPED_HOST_ACQUIRE_PASS");
  return 0;
}
