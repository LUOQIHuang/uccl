#pragma once

#include <cstddef>
#include <cstdint>
#include <stdexcept>

// Normal mirrors verbs' per-ring data QPs, with a separate control endpoint.
// LL mirrors verbs' single data queue per peer. WRITE and its notifying FADD
// must select the same lane; GPU ring IDs occupy the high 32 bits of wr_id.
struct UrmaMode {
  static constexpr uint32_t kWireVersion = 2;
  static constexpr size_t kMaxChannels = 8;
  bool normal = true;
  size_t channels = 1;

  size_t endpoint_count() const { return normal ? channels + 1 : 1; }
  size_t data_lane(uint64_t wr_id) const {
    return normal ? 1 + ((wr_id >> 32) % channels) : 0;
  }
  int64_t atomic_value(int32_t value, bool combine) const {
    return !normal && combine ? 1 : static_cast<int64_t>(value);
  }
  void validate() const {
    if (!channels || channels > kMaxChannels || (!normal && channels != 1))
      throw std::invalid_argument("invalid URMA mode/channel count");
  }
};

struct UrmaWorkRequest {
  enum class Kind { Write, Atomic, Barrier };
  Kind kind = Kind::Write;
  uint64_t wr_id = 0;
  // Internal WRITE completions use a sentinel wr_id but still belong to the
  // original GPU ring. UINT32_MAX means derive the ring from wr_id.
  uint32_t ring = UINT32_MAX;
  uint64_t local_offset = 0;
  uint64_t remote_offset = 0;
  uint32_t bytes = 0;
  int64_t value = 0;
  bool fence = true;
};
