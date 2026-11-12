#pragma once

#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <stdexcept>

// Diagnostic A/B only. Default transport semantics remain unchanged. Parse
// once per transport, never getenv in the WR hot path. Atomic fences are not
// controlled by this switch.
inline bool urma_ll_relaxed_write_from_env() {
  auto const* value = std::getenv("UCCL_URMA_LL_WRITE_ORDER");
  if (!value || !*value || !std::strcmp(value,"default") || !std::strcmp(value,"strong"))
    return false;
  if (!std::strcmp(value,"relax")) return true;
  throw std::invalid_argument("UCCL_URMA_LL_WRITE_ORDER must be default|strong|relax");
}

inline bool urma_normal_relaxed_write_from_env() {
  auto const* value = std::getenv("UCCL_URMA_NORMAL_WRITE_ORDER");
  if (!value || !*value || !std::strcmp(value,"default") || !std::strcmp(value,"relax"))
    return true;
  if (!std::strcmp(value,"strong")) return false;
  throw std::invalid_argument("UCCL_URMA_NORMAL_WRITE_ORDER must be default|strong|relax");
}

// Diagnostic only: keep STRONG placement, vary only Normal data FADD fence.
// Barrier requests and LL retain their original per-request fence.
inline bool urma_normal_fadd_fence_from_env() {
  auto const* value=std::getenv("UCCL_URMA_NORMAL_FADD_FENCE");
  if (!value || !*value || !std::strcmp(value,"default") || !std::strcmp(value,"on")) return true;
  if (!std::strcmp(value,"off")) return false;
  throw std::invalid_argument("UCCL_URMA_NORMAL_FADD_FENCE must be default|on|off");
}

enum class UrmaNormalCompletion { All, Ordered, Paired };
// Diagnostic submission scheduling only; no placement/fence/CQE changes.
inline bool urma_normal_writes_first_from_env() {
  auto const* value=std::getenv("UCCL_URMA_NORMAL_SUBMIT_SCHEDULE");
  if (!value || !*value || !std::strcmp(value,"lane-mixed")) return false;
  if (!std::strcmp(value,"writes-first")) return true;
  throw std::invalid_argument("UCCL_URMA_NORMAL_SUBMIT_SCHEDULE must be lane-mixed|writes-first");
}
inline char const* urma_normal_submit_schedule_name(bool writes_first) {
  return writes_first ? "writes-first" : "lane-mixed";
}
inline UrmaNormalCompletion urma_normal_completion_from_env() {
  auto const* value=std::getenv("UCCL_URMA_NORMAL_COMPLETION");
  if (!value || !*value || !std::strcmp(value,"all")) return UrmaNormalCompletion::All;
  if (!std::strcmp(value,"ordered")) return UrmaNormalCompletion::Ordered;
  if (!std::strcmp(value,"paired")) return UrmaNormalCompletion::Paired;
  throw std::invalid_argument("UCCL_URMA_NORMAL_COMPLETION must be all|ordered|paired");
}
inline char const* urma_normal_completion_name(UrmaNormalCompletion mode) {
  return mode==UrmaNormalCompletion::Paired ? "paired" :
      mode==UrmaNormalCompletion::Ordered ? "ordered" : "all";
}

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
  // Explicit adjacency contract: this internal WRITE is immediately followed
  // by its original, fenced notifying FADD on the SAME ring and peer.
  bool notify_pair = false;
};
