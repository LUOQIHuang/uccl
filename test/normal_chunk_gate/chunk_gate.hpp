#pragma once

#include <array>
#include <atomic>
#include <chrono>
#include <cstdint>
#include <sstream>
#include <stdexcept>

namespace uccl::normal_chunk_gate {
constexpr unsigned proxies = 4, rings = 3, target = 6;
struct Observation {
  std::atomic<uint64_t> begin{0}, end{0}, wr{0};
};
struct Local {
  unsigned payloads = 0;
  uint64_t start = 0;
};
inline std::array<Observation, proxies * rings> observations;
inline std::atomic<uint64_t> active{0}, delay_ns{0};
inline uint64_t next_epoch = 0;  // Only the Python caller arms or ends a window.

inline uint64_t now_ns() {
  return std::chrono::duration_cast<std::chrono::nanoseconds>(
      std::chrono::steady_clock::now().time_since_epoch()).count();
}

inline void begin(unsigned delay_us) {
  if (active.load() || (delay_us != 0 && delay_us != 300)) {
    throw std::invalid_argument("invalid Normal chunk-gate window");
  }
  for (auto& record : observations) record.end.store(0, std::memory_order_relaxed);
  delay_ns.store(uint64_t(delay_us) * 1000, std::memory_order_relaxed);
  active.store(++next_epoch, std::memory_order_release);
}

inline bool hold(unsigned proxy, unsigned ring, uint64_t wr) {
  auto epoch = active.load(std::memory_order_acquire);
  if (!epoch || proxy >= proxies || ring >= rings) return false;
  static thread_local uint64_t seen_epoch = 0;
  static thread_local std::array<Local, rings> queue;
  if (seen_epoch != epoch) {
    queue = {};
    seen_epoch = epoch;
  }
  auto& local = queue[ring];
  if (local.payloads + 1 != target) {
    ++local.payloads;
    return false;
  }
  auto now = now_ns();
  if (!local.start) local.start = now;
  if (now - local.start < delay_ns.load(std::memory_order_relaxed)) return true;
  auto& record = observations[proxy * rings + ring];
  record.begin.store(local.start, std::memory_order_relaxed);
  record.wr.store(wr, std::memory_order_relaxed);
  record.end.store(now, std::memory_order_release);
  ++local.payloads;
  return false;
}

inline std::string end() {
  if (!active.exchange(0)) throw std::runtime_error("no Normal chunk-gate window");
  std::ostringstream out;
  out << "{\"delay_us\":" << delay_ns.load() / 1000 << ",\"payload\":" << target << ",\"channels\":[";
  bool comma = false;
  for (unsigned i = 0; i < observations.size(); ++i) {
    auto& record = observations[i];
    auto finish = record.end.load(std::memory_order_acquire);
    if (!finish) continue;
    if (comma) out << ',';
    comma = true;
    out << "{\"proxy\":" << i / rings << ",\"ring\":" << i % rings
        << ",\"wr_id\":" << record.wr.load() << ",\"begin_ns\":" << record.begin.load()
        << ",\"end_ns\":" << finish << '}';
  }
  out << "]}";
  return out.str();
}
}
