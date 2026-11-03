#include <cuda_runtime.h>
#include <cuda/atomic>
#include <cstdint>

// Each notification corresponds to eight non-overlapping relaxed WRITE WRs.
// The sender waits for this GPU's acknowledgement before reusing the target.
__global__ void poll_sequence(uint64_t* signal, uint32_t const* payload,
                              uint64_t* status, uint32_t expected_base,
                              int rounds) {
  if (blockIdx.x || threadIdx.x) return;
  auto ready = cuda::atomic_ref<uint64_t, cuda::thread_scope_system>(status[0]);
  auto done = cuda::atomic_ref<uint64_t, cuda::thread_scope_system>(status[1]);
  auto stop = cuda::atomic_ref<uint64_t, cuda::thread_scope_system>(status[7]);
  auto count = cuda::atomic_ref<uint64_t, cuda::thread_scope_system>(signal[0]);
  ready.store(1, cuda::memory_order_release);
  for (int sequence = 1; sequence <= rounds; ++sequence) {
    uint64_t const start = clock64();
    uint64_t observed = 0;
    do {
      observed = count.load(cuda::memory_order_acquire);
      if (stop.load(cuda::memory_order_acquire) || clock64() - start > 20000000000ULL) {
        status[3] = 1;
        done.store(static_cast<uint64_t>(sequence), cuda::memory_order_release);
        return;
      }
    } while (observed < static_cast<uint64_t>(sequence));
    uint64_t bad = observed != static_cast<uint64_t>(sequence);
    uint32_t const expected = expected_base ^ static_cast<uint32_t>(sequence);
    for (int i = 0; i < 1024; ++i)
      if (__builtin_nontemporal_load(payload + i) != expected) ++bad;
    if (bad && status[4] == 0) status[4] = static_cast<uint64_t>(sequence);
    status[2] += bad;
    done.store(static_cast<uint64_t>(sequence), cuda::memory_order_release);
  }
}

static cudaStream_t stream = nullptr;
extern "C" int relax_poll_start(uint64_t* signal, uint32_t const* payload,
                                uint64_t* status, uint32_t expected_base,
                                int rounds) {
  cudaError_t rc = cudaStreamCreateWithFlags(&stream, cudaStreamNonBlocking);
  if (rc != cudaSuccess) return static_cast<int>(rc);
  poll_sequence<<<1, 1, 0, stream>>>(signal, payload, status, expected_base, rounds);
  rc = cudaGetLastError();
  return static_cast<int>(rc);
}

extern "C" int relax_poll_finish() {
  if (!stream) return 0;
  cudaError_t rc = cudaStreamSynchronize(stream);
  cudaError_t destroyed = cudaStreamDestroy(stream);
  stream = nullptr;
  return static_cast<int>(rc == cudaSuccess ? destroyed : rc);
}
