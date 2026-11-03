#pragma once
#include <cstddef>
#include <cstdint>

struct ProxyCtx {
  int local_rank = 0;
  int thread_idx = 0;
  static constexpr std::size_t kMaxAtomicOps = 1024;
};
struct LocalConnInfo {
  static constexpr std::size_t kMaxUrmaBlob = 2048;
  uintptr_t addr = 0;
  uint64_t len = 0;
  uintptr_t atomic_buffer_addr = 0;
  uint64_t atomic_buffer_len = 0;
  uint32_t urma_wire_version = 0;
  uint32_t urma_rjetty_len = 0;
  uint8_t urma_rjetty[kMaxUrmaBlob]{};
  uint32_t urma_main_seg_len = 0;
  uint8_t urma_main_seg[kMaxUrmaBlob]{};
  uint32_t urma_atomic_seg_len = 0;
  uint8_t urma_atomic_seg[kMaxUrmaBlob]{};
  uint32_t urma_barrier_seg_len = 0;
  uint8_t urma_barrier_seg[kMaxUrmaBlob]{};
};
using RemoteConnInfo = LocalConnInfo;
struct TransportCompletion {
  uint64_t wr_id = 0;
  uint64_t data = 0;
  bool is_write = false;
  bool is_atomic = false;
  bool is_recv_data = false;
  int status = 0;
};
class EpTransport {
 public:
  virtual ~EpTransport() = default;
  virtual void init(ProxyCtx&) = 0;
  virtual void register_main_buffer(void*, size_t, int) = 0;
  virtual void register_atomic_buffer(void*, size_t, int) = 0;
  virtual LocalConnInfo local_info() const = 0;
  virtual void connect_peer(int, RemoteConnInfo const&) = 0;
  virtual void post_write(int, uint64_t, uint64_t, uint64_t, uint32_t, bool) = 0;
  virtual void post_atomic_add(int, uint64_t, uint64_t, int64_t, bool) = 0;
  virtual int poll(TransportCompletion*, int) = 0;
  virtual void destroy() = 0;
};
