#include <cuda_runtime_api.h>
#include <atomic>
#include <cerrno>
#include <cstdint>
#include <cstring>
#include <sched.h>
#include <thread>
#include <time.h>
#include <unistd.h>

extern "C" int uccl_stage_normal_clock_set(void*);
extern "C" int uccl_stage_ll_clock_set(void*);
namespace {
struct ClockStats {
  uint64_t updates;
  uint64_t max_gap_ns;
  uint64_t gaps_over_1us;
  uint64_t gaps_over_10us;
  int scheduler_error;
  int reserved;
};
std::atomic<bool> running{false};
std::thread updater;
uint64_t* host_clock = nullptr;
void* device_clock = nullptr;
ClockStats stats{};
uint64_t now_raw() {
  timespec ts{};
  if (clock_gettime(CLOCK_MONOTONIC_RAW, &ts) != 0) return 0;
  return uint64_t(ts.tv_sec) * 1000000000ULL + uint64_t(ts.tv_nsec);
}
void update_clock() {
  sched_param param{};
  if (sched_setscheduler(0, SCHED_IDLE, &param) != 0) stats.scheduler_error = errno;
  uint64_t previous = now_raw();
  while (running.load(std::memory_order_relaxed)) {
    uint64_t t = now_raw();
    __atomic_store_n(host_clock, t, __ATOMIC_RELEASE);
    uint64_t gap = t >= previous ? t - previous : 0;
    if (gap > stats.max_gap_ns) stats.max_gap_ns = gap;
    stats.gaps_over_1us += gap > 1000;
    stats.gaps_over_10us += gap > 10000;
    ++stats.updates;
    previous = t;
  }
}
}
extern "C" __attribute__((visibility("default"))) int uccl_stage_host_clock_start() {
  if (running.load() || host_clock) return -1;
  std::memset(&stats, 0, sizeof(stats));
  cudaError_t rc = cudaHostAlloc(reinterpret_cast<void**>(&host_clock), 4096, cudaHostAllocMapped);
  if (rc != cudaSuccess) return static_cast<int>(rc);
  rc = cudaHostGetDevicePointer(&device_clock, host_clock, 0);
  if (rc != cudaSuccess) { cudaFreeHost(host_clock); host_clock = nullptr; return static_cast<int>(rc); }
  __atomic_store_n(host_clock, now_raw(), __ATOMIC_RELEASE);
  int result = uccl_stage_normal_clock_set(device_clock);
  if (!result) result = uccl_stage_ll_clock_set(device_clock);
  if (result) {
    uccl_stage_normal_clock_set(nullptr); uccl_stage_ll_clock_set(nullptr);
    cudaFreeHost(host_clock); host_clock = nullptr; device_clock = nullptr;
    return result;
  }
  running.store(true);
  try { updater = std::thread(update_clock); }
  catch (...) {
    running.store(false);
    uccl_stage_normal_clock_set(nullptr); uccl_stage_ll_clock_set(nullptr);
    cudaFreeHost(host_clock); host_clock = nullptr; device_clock = nullptr;
    return -2;
  }
  return 0;
}
extern "C" __attribute__((visibility("default"))) int uccl_stage_host_clock_stop(void* output) {
  if (!running.load() || !host_clock || !output) return -1;
  running.store(false);
  if (updater.joinable()) updater.join();
  std::memcpy(output, &stats, sizeof(stats));
  int result = uccl_stage_normal_clock_set(nullptr);
  int rc_ll = uccl_stage_ll_clock_set(nullptr);
  cudaError_t rc_free = cudaFreeHost(host_clock);
  host_clock = nullptr; device_clock = nullptr;
  if (result) return result;
  if (rc_ll) return rc_ll;
  return static_cast<int>(rc_free);
}
