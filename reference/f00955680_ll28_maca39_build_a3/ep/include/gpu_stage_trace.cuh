#pragma once
// Six event streams for a bounded one-token, two-rank diagnostic.
namespace uccl_gpu_stage_trace {
static constexpr unsigned int kCapacity = 8192;
struct alignas(8) Trace {
  unsigned int count[6];
  unsigned int dropped;
  unsigned int reserved;
  unsigned long long ticks[6][kCapacity];
  unsigned long long device_before[6][kCapacity];
  unsigned long long device_after[6][kCapacity];
  unsigned int block_id[6][kCapacity];
  unsigned int epoch[6][kCapacity];
};
static_assert(sizeof(Trace) == 32 + 6 * kCapacity * (3 * sizeof(unsigned long long) + 2 * sizeof(unsigned int)), "trace ABI mismatch");
}
