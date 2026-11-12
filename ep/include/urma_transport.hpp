#pragma once

#include "gpu_platform.hpp"
#include "transport.hpp"
#include "urma_mode.hpp"
#include "urma_phase_diag.hpp"
#ifdef USE_URMA
#include "urma_host_page_allocator.hpp"
#if __has_include(<urma_api.h>)
#include <urma_api.h>
#else
#include <ub/umdk/urma/urma_api.h>
#endif
#endif

#include <cstddef>
#include <cstdint>
#include <memory>
#include <stdexcept>
#include <vector>

#ifdef USE_URMA
struct UrmaRegistration;
#endif

// URMA-based transport for UCCL-EP.
//
// Maps the four UCCL proxy command classes onto URMA hardware primitives:
//   WRITE   -> URMA_OPC_WRITE (one-sided RDMA write)
//   ATOMIC  -> URMA_OPC_FADD  (hardware fetch-and-add, 64-bit operand)
//   BARRIER -> URMA_OPC_FADD  (FADD into a per-transport barrier word)
//   QUIET   -> drain the JFC until all outstanding WRs complete
//
// The barrier state machine (proxy.cpp) is the same as the CXI one: peers
// FADD into "barrier words" exported by each transport, and rank-0 releases
// by FADDing into per-peer release slots.
class UrmaTransport final : public EpTransport {
 public:
  UrmaTransport() = default;
  ~UrmaTransport() override;
  UrmaTransport(UrmaTransport const&) = delete;
  UrmaTransport& operator=(UrmaTransport const&) = delete;
  void init(ProxyCtx& ctx) override;
  void configure(bool normal, size_t channels);
  UrmaMode mode() const { return mode_; }
  bool normal_writes_first() const { return normal_writes_first_; }
  bool normal_fadd_fence() const { return normal_fadd_fence_; }
  size_t capacity() const;
  // Input order is retained within each lane. All WRITE/FADD operations from
  // one ring use its data lane; barrier messages use the control lane.
  void post_batch(int dst_rank, std::vector<UrmaWorkRequest> const& requests);
  void register_main_buffer(void* ptr, size_t len, int gpu_device) override;
  void register_atomic_buffer(void* ptr, size_t len, int gpu_device) override;
  LocalConnInfo local_info() const override;
  void connect_peer(int peer, RemoteConnInfo const& remote) override;
  void post_write(int dst_rank, uint64_t wr_id, uint64_t local_offset,
                  uint64_t remote_offset, uint32_t bytes,
                  bool low_latency) override;
  void post_atomic_add(int dst_rank, uint64_t wr_id,
                       uint64_t remote_atomic_offset, int64_t value,
                       bool fence) override;
  // Barrier support: FADD |value| into the remote peer's barrier word |slot|.
  // |wr_id| may be an internal sentinel (kUrmaInternalWrId) that the proxy
  // does not ack to the GPU.
  void post_barrier_atomic_add(int dst_rank, uint64_t wr_id, size_t slot,
                               uint64_t value);
  // Read a locally-owned barrier word (written by remote peers).
  uint64_t load_barrier_word(size_t slot) const;
  int poll(TransportCompletion* out, int max) override;
  void destroy() override;
  // Share GPU-allocation ownership with every registration of this pointer.
  // The returned lease keeps the allocation alive; the final lease or
  // registration releases it after unregistering the segment.
  static std::shared_ptr<void> acquire_buffer_ownership(void* ptr, bool host,
                                               int gpu_device,
                                               void* allocation_base = nullptr);

 private:
  [[noreturn]] static void unavailable();
  UrmaMode mode_;
  bool ll_relaxed_write_ = false;
  bool normal_relaxed_write_ = true;
  bool normal_writes_first_ = false;
  bool normal_fadd_fence_ = true;
  uint64_t dispatch_trial_epoch_ = 0;
  UrmaNormalCompletion normal_completion_ = UrmaNormalCompletion::All;
#ifdef USE_URMA
  ProxyCtx* ctx_ = nullptr;
  bool init_acquired_ = false;
  size_t bound_lanes_ = 0;
  bool failed_ = false;
  std::shared_ptr<urma_context_t> context_owner_;
  std::vector<std::shared_ptr<UrmaRegistration>> registrations_;
  urma_target_seg_t* register_segment(void* ptr, size_t len, bool gpu,
                                      bool atomic);
  void drain();
  urma_context_t* urma_ctx_ = nullptr;
  urma_jfce_t* jfce_ = nullptr;
  urma_jfc_t* jfc_ = nullptr;
  urma_jfr_t* jfr_ = nullptr;
  std::vector<urma_jetty_t*> jetties_;
  void* main_buffer_ = nullptr;
  void* atomic_buffer_ = nullptr;
  size_t main_buffer_len_ = 0;
  size_t atomic_buffer_len_ = 0;
  // Locally registered segments (registered here, imported by peers).
  urma_target_seg_t* main_tseg_ = nullptr;
  urma_target_seg_t* atomic_tseg_ = nullptr;
  // Scratch buffer receiving the "old value" writeback of FADD ops.
  std::vector<uint64_t,UrmaHostPageAllocator<uint64_t>> atomic_operands_;
  urma_target_seg_t* operand_tseg_ = nullptr;
  // Locally-owned barrier words; remote peers FADD into them.
  std::vector<uint64_t,UrmaHostPageAllocator<uint64_t>> barrier_words_;
  urma_target_seg_t* barrier_tseg_ = nullptr;
  LocalConnInfo local_info_{};
  // Per-peer imported objects.
  struct Peer {
    std::vector<urma_target_jetty_t*> tjetties;
    urma_target_seg_t* main_tseg = nullptr;
    urma_target_seg_t* atomic_tseg = nullptr;
    urma_target_seg_t* barrier_tseg = nullptr;
  };
  std::vector<Peer> peers_;
  // In-flight op bookkeeping (mirrors the CXI OpContext pattern).
  struct OpContext {
    uint64_t wr_id = 0;
    uint64_t generation = 0;
    uint32_t lane = 0;
    bool is_write = false;
    bool is_atomic = false;
    bool active = false;
    bool recovery = false;
    uint32_t child = UINT32_MAX;
    uint32_t anchor = UINT32_MAX;
    uccl::urma_diag::Sample diagnostic;
  };
  // Stable slots give O(1) completion lookup and O(1) FADD scratch allocation.
  // user_ctx uses 48 generation bits + 16 index bits. Stale CQEs cannot retire
  // reused slots, and sustained LL traffic does not exhaust a 32-bit epoch.
  std::vector<OpContext> op_contexts_;
  std::vector<size_t> free_contexts_;
  size_t acquire_context(UrmaWorkRequest const& request);
  void release_context(size_t index);
  size_t outstanding() const { return op_contexts_.size() - free_contexts_.size(); }
  struct PreparedWr {
    urma_jfs_wr_t wr{};
    urma_sge_t src{}, dst{};
    size_t context = 0;
    bool notify_pair = false;
    bool atomic_anchor = false;
    uint32_t ring = 0;
  };
  std::vector<PreparedWr> prepared_;
  void validate_request(Peer const& peer, UrmaWorkRequest const& request) const;
  void prepare(PreparedWr& out, Peer& peer, size_t lane,
               UrmaWorkRequest const& request);
  void submit_prepared(size_t lane, Peer& peer);
#endif
};
