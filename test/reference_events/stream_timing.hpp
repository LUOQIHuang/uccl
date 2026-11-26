#pragma once

#include <array>
#include <cuda_runtime.h>
#include <iomanip>
#include <sstream>
#include <stdexcept>

namespace uccl::normal_stream_timing {

// All four markers are enqueued by the caller of Buffer::internode_dispatch.
struct Window {
  std::array<cudaEvent_t, 4> events{};
  cudaStream_t stream = nullptr;
  unsigned recorded = 0;
  bool active = false;
};
inline thread_local Window window;

inline void check(cudaError_t result) {
  if (result != cudaSuccess) throw std::runtime_error(cudaGetErrorString(result));
}

inline void close() {
  window.active = false;
  cudaError_t first_error = cudaSuccess;
  for (auto& event : window.events) {
    if (!event) continue;
    const auto result = cudaEventDestroy(event);
    if (first_error == cudaSuccess) first_error = result;
    event = nullptr;
  }
  check(first_error);
}

inline void prepare() {
  if (window.events.front()) throw std::logic_error("already prepared Normal stream timing");
  for (auto& event : window.events) {
    const auto result = cudaEventCreate(&event);
    if (result != cudaSuccess) {
      close();
      check(result);
    }
  }
}

inline void begin() {
  if (!window.events.back() || window.active) throw std::logic_error("Normal stream timing not ready");
  window.recorded = 0;
  window.active = true;
}

inline void mark(unsigned index, cudaStream_t stream) {
  if (!window.active) return;
  if (index != window.recorded || index >= window.events.size() || (index && stream != window.stream)) {
    throw std::logic_error("expected one cached Normal Dispatch on a single communication stream");
  }
  window.stream = stream;
  check(cudaEventRecord(window.events[index], stream));
  ++window.recorded;
}

inline std::string end() {
  const bool complete = window.active && window.recorded == window.events.size();
  window.active = false;
  if (!complete) throw std::logic_error("missing cached Normal Dispatch stream markers");
  constexpr std::array<char const*, 3> names{"cached_notify_us", "notify_to_dispatch_us", "dispatch_comm_us"};
  std::ostringstream out;
  out << std::setprecision(12) << '{';
  for (unsigned i = 0; i < names.size(); ++i) {
    float ms = 0;
    check(cudaEventElapsedTime(&ms, window.events[i], window.events[i+1]));
    if (i) out << ',';
    out << '"' << names[i] << "\":" << double(ms) * 1000;
  }
  out << '}';
  return out.str();
}
}
