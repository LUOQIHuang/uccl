
#include "normal_receiver_waits.cuh"
#include <sstream>
#include <stdexcept>

namespace uccl::normal_receiver_waits {
__device__ State state;

inline void check(cudaError_t result) {
  if (result != cudaSuccess) throw std::runtime_error(cudaGetErrorString(result));
}

void begin() {
  State empty{};
  empty.enabled = 1;
  check(cudaMemcpyToSymbol(state, &empty, sizeof(empty)));
}

std::string end() {
  State recorded{};
  auto result = cudaMemcpyFromSymbol(&recorded, state, sizeof(recorded));
  unsigned off = 0;
  auto disable = cudaMemcpyToSymbol(state, &off, sizeof(off));
  check(result);
  check(disable);
  if (recorded.next_done != channels) throw std::runtime_error("wrong receiver completion coverage");
  int device = 0;
  cudaDeviceProp props{};
  check(cudaGetDevice(&device));
  check(cudaGetDeviceProperties(&props, device));
  std::ostringstream out;
  out << "{\"nominal_clock_khz\":" << props.clockRate << ",\"receiver_waits\":[";
  for (unsigned i = 0; i < channels; ++i) {
    const auto& c = recorded.counters[i];
    if (i) out << ',';
    out << "{\"channel\":" << i << ",\"done_order\":" << recorded.done_order[i]
        << ",\"remote_tail_cycles\":" << c.remote_tail << ",\"mixed_tail_cycles\":" << c.mixed_tail
        << ",\"remote_epoch_cycles\":" << c.remote_epoch << ",\"local_space_cycles\":" << c.local_space
        << ",\"forwarder_cycles\":" << c.forwarder << '}';
  }
  out << "]}";
  return out.str();
}
}
