#pragma once

#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <functional>
#include <string>
#include <thread>
#include <time.h>
#include <unistd.h>
#include <vector>

namespace uccl_stage_trace {

enum class Event : uint32_t {
  kPoll = 1,
  kDecoded = 2,
  kPost = 3,
  kCompletion = 4,
  kMarkerPost = 5,
  kMarkerReceived = 6,
  kNativePost = 7,
};

struct Record {
  uint64_t ns;
  uint64_t wr_id;
  uint64_t local_offset;
  uint64_t remote_offset;
  uint32_t bytes;
  uint32_t event;
  uint32_t operation;
  uint32_t peer_or_status;
};

class Recorder {
 public:
  Recorder() {
    auto const* path = std::getenv("UCCL_CPU_TRACE_DIR");
    if (path && *path) {
      directory_ = path;
      records_.reserve(kCapacity);
    }
  }

  ~Recorder() {
    if (directory_.empty() || records_.empty()) return;
    char filename[4096];
    char const* rank_text = std::getenv("RANK");
    long const rank = rank_text ? std::strtol(rank_text, nullptr, 10) : -1;
    auto const tid = std::hash<std::thread::id>{}(std::this_thread::get_id());
    int const length = std::snprintf(filename, sizeof(filename),
                                     "%s/cpu_rank%ld_pid%ld_thread%zu.csv",
                                     directory_.c_str(), rank,
                                     static_cast<long>(getpid()), tid);
    if (length < 0 || static_cast<size_t>(length) >= sizeof(filename)) return;
    FILE* file = std::fopen(filename, "wx");
    if (!file) {
      std::fprintf(stderr, "CPU_TRACE_OPEN_FAILED %s\n", filename);
      return;
    }
    std::fprintf(file, "# dropped=%zu\n", dropped_);
    std::fprintf(file, "ns,wr_id,local_offset,remote_offset,bytes,event,operation,peer_or_status\n");
    for (auto const& r : records_) {
      std::fprintf(file, "%llu,%llu,%llu,%llu,%u,%u,%u,%u\n",
                   static_cast<unsigned long long>(r.ns),
                   static_cast<unsigned long long>(r.wr_id),
                   static_cast<unsigned long long>(r.local_offset),
                   static_cast<unsigned long long>(r.remote_offset), r.bytes,
                   r.event, r.operation, r.peer_or_status);
    }
    if (std::fclose(file) != 0)
      std::fprintf(stderr, "CPU_TRACE_CLOSE_FAILED %s\n", filename);
  }

  uint64_t now() const {
    if (directory_.empty()) return 0;
    timespec time{};
    if (clock_gettime(CLOCK_MONOTONIC_RAW, &time) != 0) return 0;
    return static_cast<uint64_t>(time.tv_sec) * UINT64_C(1000000000) +
           static_cast<uint64_t>(time.tv_nsec);
  }

  void add(uint64_t ns, Event event, uint64_t wr_id, uint32_t bytes,
           uint32_t operation, uint32_t peer_or_status,
           uint64_t local_offset = 0, uint64_t remote_offset = 0) {
    if (!ns) return;
    if (records_.size() == kCapacity) {
      ++dropped_;
      return;
    }
    records_.push_back({ns, wr_id, local_offset, remote_offset, bytes,
                        static_cast<uint32_t>(event), operation, peer_or_status});
  }

 private:
  static constexpr size_t kCapacity = 262144;
  std::string directory_;
  std::vector<Record> records_;
  size_t dropped_ = 0;
};

inline thread_local Recorder recorder;
inline uint64_t now() { return recorder.now(); }
inline void add(uint64_t ns, Event event, uint64_t wr_id, uint32_t bytes,
                uint32_t operation, uint32_t peer_or_status,
                uint64_t local_offset = 0, uint64_t remote_offset = 0) {
  recorder.add(ns, event, wr_id, bytes, operation, peer_or_status, local_offset,
               remote_offset);
}

}  // namespace uccl_stage_trace
