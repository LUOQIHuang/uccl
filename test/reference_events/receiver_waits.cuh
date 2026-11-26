#pragma once

#include <string>

namespace uccl::normal_receiver_waits {
constexpr unsigned channels = 12;
struct Counters {
  unsigned long long remote_tail, mixed_tail, remote_epoch, local_space, forwarder;
};
struct State {
  unsigned enabled, next_done;
  unsigned done_order[channels];
  Counters counters[channels];
};

#if defined(__CUDACC__)
extern __device__ State state;
#endif

void begin();
std::string end();
}
