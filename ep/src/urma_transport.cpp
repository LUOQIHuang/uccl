#include "urma_transport.hpp"
#include "common.hpp"
#include "gpu_p2p_runtime.hpp"
#include "urma_layout_diag.hpp"
#include "urma_dispatch_trial.hpp"
#include "transport_timeline.hpp"
#include <algorithm>
#include <chrono>
#include <cerrno>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <limits>
#include <map>
#include <mutex>
#include <string>
#include <thread>
#include <tuple>

void UrmaTransport::configure(bool normal, size_t channels) {
#ifdef USE_URMA
  if (init_acquired_) throw std::logic_error("configure URMA before init");
#endif
  UrmaMode requested{normal, normal ? channels : 1};
  requested.validate();
  ll_relaxed_write_ = urma_ll_relaxed_write_from_env();
  normal_completion_ = urma_normal_completion_from_env();
  mode_ = requested;
}
#ifndef USE_URMA
// -----------------------------------------------------------------------
// Stub build (USE_URMA not requested): every entry point throws.
// -----------------------------------------------------------------------
UrmaTransport::~UrmaTransport() {
  try {
    destroy();
  } catch (std::exception const& e) {
    // Continuing would let GPU buffers or op contexts be freed while DMA
    // may still reference them. Teardown failure is deliberately fail-stop.
    std::fprintf(stderr, "URMA teardown failed: %s\n", e.what());
    std::terminate();
  }
}
void UrmaTransport::unavailable() {
  throw std::runtime_error("UrmaTransport requires building with USE_URMA=1");
}
void UrmaTransport::init(ProxyCtx&) { unavailable(); }
size_t UrmaTransport::capacity() const { unavailable(); }
void UrmaTransport::post_batch(int, std::vector<UrmaWorkRequest> const&) { unavailable(); }
void UrmaTransport::register_main_buffer(void*, size_t, int) { unavailable(); }
void UrmaTransport::register_atomic_buffer(void*, size_t, int) {
  unavailable();
}
LocalConnInfo UrmaTransport::local_info() const { unavailable(); }
void UrmaTransport::connect_peer(int, RemoteConnInfo const&) { unavailable(); }
void UrmaTransport::post_write(int, uint64_t, uint64_t, uint64_t, uint32_t,
                               bool) {
  unavailable();
}
void UrmaTransport::post_atomic_add(int, uint64_t, uint64_t, int64_t, bool) {
  unavailable();
}
void UrmaTransport::post_barrier_atomic_add(int, uint64_t, size_t, uint64_t) {
  unavailable();
}
uint64_t UrmaTransport::load_barrier_word(size_t) const { unavailable(); }
int UrmaTransport::poll(TransportCompletion*, int) { unavailable(); }
void UrmaTransport::destroy() {}
std::shared_ptr<void> UrmaTransport::acquire_buffer_ownership(void*, bool,
                                                              int, void*) {
  unavailable();
}
#else  // USE_URMA
// Registration objects are only created/released while g_resource_mutex is
// held. Jetty/JFC remain private to each transport; identical main/control
// ranges share a pin/token/mapping on the same device context.
struct UrmaAllocation {
  void* ptr = nullptr;
  bool owned = false;
  bool host = false;
  int gpu_device = -1;
  ~UrmaAllocation() {
    if (!owned) return;
    try {
      uccl::gpu_p2p::release_memory(
          ptr,
          host ? uccl::gpu_p2p::MemoryKind::HostMapped
               : uccl::gpu_p2p::MemoryKind::Device,
          gpu_device);
    } catch (std::exception const& e) {
      std::fprintf(stderr, "URMA failed to release %s allocation: %s\n",
                   uccl::gpu_p2p::vendor_name(), e.what());
      std::terminate();
    }
  }
};
struct UrmaRegistration {
  // Destructor body unregisters before this member releases GPU storage.
  std::shared_ptr<UrmaAllocation> allocation;
  std::shared_ptr<urma_context_t> context;
  urma_target_seg_t* seg = nullptr;
  urma_token_id_t* token = nullptr;
  ~UrmaRegistration() {
    if ((seg && urma_unregister_seg(seg) != URMA_SUCCESS) ||
        (token && urma_free_token_id(token) != URMA_SUCCESS)) {
      std::fprintf(
          stderr,
          "URMA failed to release registered memory; refusing unsafe free\n");
      std::terminate();
    }
  }
};
namespace {
// urma_init()/urma_uninit() are process-global and must be paired.
std::mutex g_urma_init_mutex;
int g_urma_init_refcount = 0;
std::mutex g_resource_mutex;
using ContextKey = std::pair<std::string, uint32_t>;
std::map<ContextKey, std::weak_ptr<urma_context_t> > g_contexts;
using SegmentKey = std::tuple<urma_context_t*, uint64_t, size_t, bool, bool>;
std::map<SegmentKey, std::weak_ptr<UrmaRegistration> > g_segments;
std::map<void*, std::weak_ptr<UrmaAllocation> > g_allocations;
// Number of barrier words exported by each transport.  Slot 0 collects
// node-arrival counters, slot 1 collects rank-0 release notifications
// (identical to the CXI transport's scheme).
constexpr size_t kNumBarrierWords = 8;
void check_urma(int rc, char const* what) {
  if (rc != URMA_SUCCESS) {
    char buf[256];
    std::snprintf(buf, sizeof(buf), "%s failed: rc=%d (%s)", what, rc,
                  std::strerror(rc));
    throw std::runtime_error(buf);
  }
}
urma_transport_mode_t urma_trans_mode_from_env() {
  char const* tm = std::getenv("UCCL_URMA_TM");
  if (tm && std::string(tm) != "rc") {
    throw std::runtime_error(
        "GPU P2P URMA backend requires UCCL_URMA_TM=rc");
  }
  return URMA_TM_RC;
}
void check_range(uint64_t offset, uint64_t bytes, uint64_t length) {
  if (offset > length || bytes > length - offset)
    throw std::out_of_range("URMA operation exceeds registered segment");
}
// Address of |offset| inside a remotely imported segment.  Prefer the
// driver-assigned mapping address (MVA) and fall back to the registered VA.
inline uint64_t remote_seg_addr(urma_target_seg_t const* tseg,
                                uint64_t offset) {
  if (tseg->mva != 0) return tseg->mva + offset;
  return tseg->seg.ubva.va + offset;
}
}  // namespace
UrmaTransport::~UrmaTransport() {
  try {
    destroy();
  } catch (std::exception const& e) {
    // Continuing would let GPU buffers or op contexts be freed while DMA
    // may still reference them. Teardown failure is deliberately fail-stop.
    std::fprintf(stderr, "URMA teardown failed: %s\n", e.what());
    std::terminate();
  }
}
void UrmaTransport::unavailable() {
  throw std::runtime_error("UrmaTransport is unavailable");
}
void UrmaTransport::init(ProxyCtx& ctx) {
  if (init_acquired_) throw std::logic_error("URMA already initialized");
  mode_.validate();
  (void)urma_trans_mode_from_env();
  ctx_ = &ctx;
  uccl::urma_diag::identity(ctx.local_rank,ctx.thread_idx);
  try {
    {
      std::lock_guard<std::mutex> lock(g_urma_init_mutex);
      if (g_urma_init_refcount == 0) {
        check_urma(urma_init(nullptr), "urma_init");
      }
      ++g_urma_init_refcount;
      init_acquired_ = true;
    }
    int num_devices = 0;
    urma_device_t** dev_list = urma_get_device_list(&num_devices);
    if (!dev_list || num_devices == 0) {
      if (dev_list) urma_free_device_list(dev_list);
      throw std::runtime_error("UrmaTransport: no URMA devices found");
    }
    int dev_idx = 0;
    char const* dev_name = std::getenv("UCCL_URMA_DEV_NAME");
    if (dev_name) {
      bool found = false;
      for (int i = 0; i < num_devices; ++i) {
        if (dev_list[i] && std::string(dev_list[i]->name) == dev_name) {
          dev_idx = i;
          found = true;
          break;
        }
      }
      if (!found) {
        urma_free_device_list(dev_list);
        throw std::runtime_error(
            std::string("UrmaTransport: UCCL_URMA_DEV_NAME="
                        " device not found: ") +
            dev_name);
      }
    } else {
      // Default: skip bonding devices (their EID is a placeholder on this
      // platform); round-robin real devices by local rank.
      std::vector<int> real;
      for (int i = 0; i < num_devices; ++i) {
        if (dev_list[i] && std::string(dev_list[i]->name).find("bonding") ==
                               std::string::npos) {
          real.push_back(i);
        }
      }
      if (real.empty()) {
        urma_free_device_list(dev_list);
        throw std::runtime_error(
            "URMA P2P requires a direct UB device, not bonding");
      }
      int const prefer = ctx_->local_rank >= 0 ? ctx_->local_rank : 0;
      dev_idx = real[static_cast<size_t>(prefer) % real.size()];
    }
    urma_device_t* dev = dev_list[dev_idx];
    if (!dev || dev->type != URMA_TRANSPORT_UB ||
        std::string(dev->name).find("bonding") != std::string::npos) {
      urma_free_device_list(dev_list);
      throw std::runtime_error("URMA P2P requires a direct UB device");
    }
    std::fprintf(stderr, "[URMA] rank=%d thread=%d using device %d/%d: %s\n",
                 ctx_->local_rank, ctx_->thread_idx, dev_idx, num_devices,
                 dev ? dev->name : "");
    // Any EID of the device works for context creation; use the first.
    uint32_t eid_cnt = 0;
    urma_eid_info_t* eid_list = urma_get_eid_list(dev, &eid_cnt);
    if (!eid_list || eid_cnt == 0) {
      if (eid_list) urma_free_eid_list(eid_list);
      urma_free_device_list(dev_list);
      throw std::runtime_error("UrmaTransport: device has no EIDs");
    }
    {
      std::lock_guard<std::mutex> lock(g_resource_mutex);
      ContextKey key{dev->name, eid_list[0].eid_index};
      context_owner_ = g_contexts[key].lock();
      if (!context_owner_) {
        auto* raw = urma_create_context(dev, eid_list[0].eid_index);
        if (raw) {
          context_owner_ =
              std::shared_ptr<urma_context_t>(raw, [](urma_context_t* c) {
                if (urma_delete_context(c) != URMA_SUCCESS) std::terminate();
              });
          g_contexts[key] = context_owner_;
        }
      }
      urma_ctx_ = context_owner_.get();
    }
    urma_free_eid_list(eid_list);
    urma_free_device_list(dev_list);
    if (!urma_ctx_) {
      throw std::runtime_error("urma_create_context failed");
    }
    urma_jfce_t* jfce = urma_create_jfce(urma_ctx_);
    if (!jfce) {
      throw std::runtime_error("urma_create_jfce failed");
    }
    jfce_ = jfce;
    urma_jfc_cfg_t jfc_cfg = {};
    jfc_cfg.depth = kMaxOutstandingSends * 2;
    jfc_cfg.jfce = jfce;
    jfc_ = urma_create_jfc(urma_ctx_, &jfc_cfg);
    if (!jfc_) {
      throw std::runtime_error("urma_create_jfc failed");
    }
    // RC jetty creation requires a JFR (shared) even for one-sided usage.
    urma_jfr_cfg_t jfr_cfg = {};
    jfr_cfg.depth = 1;
    jfr_cfg.trans_mode = urma_trans_mode_from_env();
    jfr_cfg.max_sge = 1;
    jfr_cfg.jfc = jfc_;
    jfr_cfg.token_value.token = 0;
    urma_jfr_t* jfr = urma_create_jfr(urma_ctx_, &jfr_cfg);
    if (!jfr) {
      throw std::runtime_error("urma_create_jfr failed");
    }
    jfr_ = jfr;
    urma_jetty_cfg_t jetty_cfg = {};
    jetty_cfg.flag.bs.share_jfr = 1; /* URMA_SHARE_JFR */
    jetty_cfg.jfs_cfg.depth = kMaxOutstandingSends;
    jetty_cfg.jfs_cfg.trans_mode = urma_trans_mode_from_env();
    jetty_cfg.jfs_cfg.max_sge = 1;
    jetty_cfg.jfs_cfg.rnr_retry = URMA_TYPICAL_RNR_RETRY;
    jetty_cfg.jfs_cfg.err_timeout = URMA_TYPICAL_ERR_TIMEOUT;
    jetty_cfg.jfs_cfg.jfc = jfc_;
    jetty_cfg.shared.jfr = jfr;
    jetty_cfg.shared.jfc = jfc_;
    jetties_.reserve(mode_.endpoint_count());
    for (size_t lane = 0; lane < mode_.endpoint_count(); ++lane) {
      auto* jetty = urma_create_jetty(urma_ctx_, &jetty_cfg);
      if (!jetty) throw std::runtime_error("urma_create_jetty failed");
      jetties_.push_back(jetty);
    }
    // Local scratch target for FADD old-value writeback.
    free_contexts_.reserve(capacity());
    op_contexts_.assign(capacity(), {});
    for (size_t i = capacity(); i > 0; --i) free_contexts_.push_back(i-1);
    prepared_.reserve(capacity());
    atomic_operands_.assign(capacity(), 0);
    operand_tseg_ = register_segment(atomic_operands_.data(),
                                     atomic_operands_.size() * sizeof(uint64_t),
                                     false, false);
    barrier_words_.assign(kNumBarrierWords, 0);
    barrier_tseg_ =
        register_segment(barrier_words_.data(),
                         barrier_words_.size() * sizeof(uint64_t), false, true);
    std::fprintf(stderr,
        "[URMA] mode=%s channels=%zu endpoints=%zu batch=linked capacity=%zu wire=%u write_order=%s atomic_fence=per_request normal_completion=%s\n",
        mode_.normal ? "normal" : "low_latency", mode_.channels,
        jetties_.size(), capacity(), UrmaMode::kWireVersion,
        mode_.normal || ll_relaxed_write_ ? "relax" : "strong",
        urma_normal_completion_name(normal_completion_));
  } catch (...) {
    destroy();
    throw;
  }
}
std::shared_ptr<void> UrmaTransport::acquire_buffer_ownership(void* ptr,
                                                              bool host,
                                                              int gpu_device,
                                                              void* allocation_base) {
  if (!ptr || (!host && gpu_device < 0))
    throw std::invalid_argument("invalid URMA allocation ownership");
  std::lock_guard<std::mutex> lock(g_resource_mutex);
  auto allocation = g_allocations[ptr].lock();
  if (!allocation) {
    allocation = std::make_shared<UrmaAllocation>();
    allocation->ptr = ptr;
    g_allocations[ptr] = allocation;
  }
  if (allocation->owned &&
      (allocation->host != host || allocation->gpu_device != gpu_device ||
       allocation->ptr != (allocation_base ? allocation_base : ptr)))
    throw std::logic_error("conflicting URMA allocation ownership");
  allocation->owned = true;
  // Registrations use the aligned view as their key; the allocator must only
  // receive its original base when the final registration/owner releases it.
  allocation->ptr = allocation_base ? allocation_base : ptr;
  allocation->host = host;
  allocation->gpu_device = gpu_device;
  return allocation;
}

urma_target_seg_t* UrmaTransport::register_segment(void* ptr, size_t len,
                                                   bool gpu, bool atomic) {
  if (!ptr || !len || len - 1 > UINT64_MAX - reinterpret_cast<uint64_t>(ptr))
    throw std::invalid_argument("invalid URMA segment range");
  std::lock_guard<std::mutex> lock(g_resource_mutex);
  SegmentKey key{urma_ctx_, reinterpret_cast<uint64_t>(ptr), len, gpu, atomic};
  if (auto owner = g_segments[key].lock()) {
    std::fprintf(stderr, "[URMA_REG] rank=%d thread=%d stage=cache_hit kind=%s va=0x%llx len=%zu atomic=%d\n",
        ctx_->local_rank, ctx_->thread_idx, gpu ? "gpu" : "host",
        static_cast<unsigned long long>(reinterpret_cast<uint64_t>(ptr)), len, atomic);
    registrations_.push_back(owner);
    return owner->seg;
  }
  // Do not let a second, partially overlapping GPU mapping replace a live
  // MATT entry. Exact ranges above share ownership; differing ranges fail.
  for (auto it = g_segments.begin(); it != g_segments.end();) {
    auto owner = it->second.lock();
    if (!owner) {
      it = g_segments.erase(it);
      continue;
    }
    auto const& k = it->first;
    if (gpu && std::get<3>(k) && std::get<0>(k) == urma_ctx_) {
      uint64_t va = reinterpret_cast<uint64_t>(ptr), other = std::get<1>(k);
      uint64_t const alignment = uccl::gpu_p2p::registration_alignment();
      uint64_t const page_mask = ~(alignment - 1);
      uint64_t end_page = (va + len - 1) & page_mask;
      uint64_t other_end_page = (other + std::get<2>(k) - 1) & page_mask;
      if ((va & page_mask) <= other_end_page && (other & page_mask) <= end_page)
        throw std::invalid_argument(
            "overlapping URMA GPU registrations require identical "
            "range/permissions");
    }
    ++it;
  }
  auto allocation = g_allocations[ptr].lock();
  if (!allocation) {
    allocation = std::make_shared<UrmaAllocation>();
    allocation->ptr = ptr;
    g_allocations[ptr] = allocation;
  }
  auto owner = std::make_shared<UrmaRegistration>();
  owner->allocation = allocation;
  owner->context = context_owner_;
  auto* token = urma_alloc_token_id(urma_ctx_);
  if (!token) throw std::runtime_error("urma_alloc_token_id failed");
  urma_seg_cfg_t cfg{};
  cfg.va = reinterpret_cast<uint64_t>(ptr);
  cfg.len = len;
  cfg.token_id = token;
  cfg.flag.bs.token_id_valid = URMA_TOKEN_ID_VALID;
  cfg.flag.bs.token_policy = URMA_TOKEN_NONE;
  cfg.flag.bs.cacheable = URMA_NON_CACHEABLE;
  cfg.flag.bs.non_pin = 0;
  cfg.flag.bs.access = URMA_ACCESS_READ | URMA_ACCESS_WRITE;
  if (atomic) cfg.flag.bs.access |= URMA_ACCESS_ATOMIC;
  cfg.is_gpu_seg = gpu ? 1 : 0;
  std::fprintf(stderr,
      "[URMA_REG] rank=%d thread=%d stage=begin kind=%s va=0x%llx len=%zu atomic=%d cfg_size=%zu gpu_flag_offset=%zu access=%u\n",
      ctx_->local_rank, ctx_->thread_idx, gpu ? "gpu" : "host",
      static_cast<unsigned long long>(cfg.va), len, atomic, sizeof(cfg),
      offsetof(urma_seg_cfg_t, is_gpu_seg), static_cast<unsigned>(cfg.flag.bs.access));
  errno = 0;
  auto* seg = urma_register_seg(urma_ctx_, &cfg);
  int const registration_errno = errno;  // Capture BEFORE token cleanup/logging.
  if (!seg) {
    std::string const details = " rank=" + std::to_string(ctx_->local_rank) +
        " thread=" + std::to_string(ctx_->thread_idx) +
        " atomic=" + std::to_string(atomic) + " len=" + std::to_string(len) +
        " errno=" + std::to_string(registration_errno) +
        " (" + (registration_errno ? std::string(std::strerror(registration_errno)) :
                  std::string("provider did not set errno")) + ")";
    std::fprintf(stderr, "[URMA_REG] stage=failed%s\n", details.c_str());
    check_urma(urma_free_token_id(token), "urma_free_token_id");
    throw std::runtime_error(
        (gpu ? std::string("URMA ") + uccl::gpu_p2p::vendor_name() +
                  " P2P segment registration failed (no host fallback)"
            : std::string("URMA host segment registration failed")) + details);
  }
  std::fprintf(stderr, "[URMA_REG] rank=%d thread=%d stage=success kind=%s va=0x%llx len=%zu atomic=%d\n",
      ctx_->local_rank, ctx_->thread_idx, gpu ? "gpu" : "host",
      static_cast<unsigned long long>(cfg.va), len, atomic);
  owner->seg = seg;
  owner->token = token;
  registrations_.push_back(owner);
  g_segments[key] = owner;
  return seg;
}
void UrmaTransport::register_main_buffer(void* ptr, size_t len, int device) {
  if (!urma_ctx_ || main_tseg_)
    throw std::logic_error("invalid main registration state");
  uccl::gpu_p2p::prepare_main_buffer(ptr, len, device);
  main_tseg_ = register_segment(ptr, len, true, false);
  main_buffer_ = ptr;
  main_buffer_len_ = len;
  local_info_.addr = reinterpret_cast<uintptr_t>(ptr);
  local_info_.len = len;
}
void UrmaTransport::register_atomic_buffer(void* ptr, size_t len, int device) {
  if (!urma_ctx_ || atomic_tseg_)
    throw std::logic_error("invalid control registration state");
  if (uccl::gpu_p2p::atomic_buffer_is_gpu_memory() &&
      reinterpret_cast<uintptr_t>(ptr) % uccl::gpu_p2p::registration_alignment())
    throw std::invalid_argument("URMA GPU atomic buffer is not registration-page aligned");
  uccl::gpu_p2p::prepare_atomic_buffer(ptr, len, device);
  atomic_tseg_ = register_segment(
      ptr, len, uccl::gpu_p2p::atomic_buffer_is_gpu_memory(), true);
  atomic_buffer_ = ptr;
  atomic_buffer_len_ = len;
  local_info_.atomic_buffer_addr = reinterpret_cast<uintptr_t>(ptr);
  local_info_.atomic_buffer_len = len;
}
LocalConnInfo UrmaTransport::local_info() const {
  if (jetties_.empty() || !main_tseg_ || !atomic_tseg_ || !barrier_tseg_)
    throw std::logic_error("URMA local_info before registration");
  LocalConnInfo info = local_info_;
  info.urma_wire_version = UrmaMode::kWireVersion;
  info.urma_normal_mode = mode_.normal;
  info.urma_channels = mode_.channels;
  info.urma_jetty_count = jetties_.size();
  static_assert(sizeof(urma_rjetty_t) * (UrmaMode::kMaxChannels + 1) <=
                LocalConnInfo::kMaxUrmaJettyBlob);
  static_assert(sizeof(urma_seg_t) <= LocalConnInfo::kMaxUrmaBlob);
  for (size_t lane = 0; lane < jetties_.size(); ++lane) {
    urma_rjetty_t rjetty{};
    rjetty.jetty_id = jetties_[lane]->jetty_id;
    rjetty.trans_mode = URMA_TM_RC;
    rjetty.type = URMA_JETTY;
    rjetty.tp_type = URMA_RTP;
    std::memcpy(info.urma_rjetty + lane * sizeof(rjetty), &rjetty, sizeof(rjetty));
  }
  info.urma_rjetty_len = sizeof(urma_rjetty_t);
  std::memcpy(info.urma_main_seg, &main_tseg_->seg, sizeof(urma_seg_t));
  std::memcpy(info.urma_atomic_seg, &atomic_tseg_->seg, sizeof(urma_seg_t));
  std::memcpy(info.urma_barrier_seg, &barrier_tseg_->seg, sizeof(urma_seg_t));
  info.urma_main_seg_len = info.urma_atomic_seg_len =
      info.urma_barrier_seg_len = sizeof(urma_seg_t);
  return info;
}
void UrmaTransport::connect_peer(int peer, RemoteConnInfo const& remote) {
  if (jetties_.empty() || peer < 0) throw std::invalid_argument("invalid URMA peer");
  if (bound_lanes_)
    throw std::logic_error("each RC transport binds exactly one peer");
  if (remote.urma_wire_version != UrmaMode::kWireVersion ||
      remote.urma_normal_mode != uint32_t(mode_.normal) ||
      remote.urma_channels != mode_.channels ||
      remote.urma_jetty_count != jetties_.size() ||
      remote.urma_rjetty_len != sizeof(urma_rjetty_t) ||
      remote.urma_main_seg_len != sizeof(urma_seg_t) ||
      remote.urma_atomic_seg_len != sizeof(urma_seg_t) ||
      remote.urma_barrier_seg_len != sizeof(urma_seg_t))
    throw std::invalid_argument("URMA descriptor ABI/version mismatch");
  // Copy into aligned native objects; the byte arrays need not be aligned.
  std::vector<urma_rjetty_t> rjetties(jetties_.size());
  urma_seg_t segs[3]{};
  std::memcpy(rjetties.data(), remote.urma_rjetty,
              rjetties.size() * sizeof(urma_rjetty_t));
  std::memcpy(&segs[0], remote.urma_main_seg, sizeof(urma_seg_t));
  std::memcpy(&segs[1], remote.urma_atomic_seg, sizeof(urma_seg_t));
  std::memcpy(&segs[2], remote.urma_barrier_seg, sizeof(urma_seg_t));
  for (auto const& rjetty : rjetties) {
    if (rjetty.trans_mode != URMA_TM_RC || rjetty.type != URMA_JETTY ||
        rjetty.tp_type != URMA_RTP || rjetty.flag.bs.has_drv_ext)
      throw std::invalid_argument("unsupported remote URMA jetty");
  }
  for (int i = 0; i < 3; ++i) {
    uint32_t access = URMA_ACCESS_READ | URMA_ACCESS_WRITE;
    if (i != 0) access |= URMA_ACCESS_ATOMIC;
    if (!segs[i].len || !segs[i].ubva.va ||
        segs[i].len > UINT64_MAX - segs[i].ubva.va ||
        (segs[i].attr.bs.access & access) != access ||
        segs[i].attr.bs.token_policy != URMA_TOKEN_NONE)
      throw std::invalid_argument("invalid remote URMA segment");
  }
  if (segs[2].len < kNumBarrierWords * sizeof(uint64_t))
    throw std::invalid_argument("remote barrier segment is too short");
  peers_.resize(static_cast<size_t>(peer) + 1);
  Peer& p = peers_[peer];
  urma_token_t token{};
  try {
    p.tjetties.reserve(rjetties.size());
    for (auto& rjetty : rjetties) {
      auto* target = urma_import_jetty(urma_ctx_, &rjetty, &token);
      if (!target) throw std::runtime_error("urma_import_jetty failed");
      p.tjetties.push_back(target);
    }
    urma_target_seg_t** targets[] = {&p.main_tseg, &p.atomic_tseg,
                                     &p.barrier_tseg};
    for (int i = 0; i < 3; ++i) {
      urma_import_seg_flag_t flag{};
      flag.bs.mapping = URMA_SEG_NOMAP;  // matches the tested P2P perftest
      flag.bs.cacheable = URMA_NON_CACHEABLE;
      flag.bs.access = URMA_ACCESS_READ | URMA_ACCESS_WRITE;
      if (i) flag.bs.access |= URMA_ACCESS_ATOMIC;
      *targets[i] = urma_import_seg(urma_ctx_, &segs[i], &token, 0, flag);
      if (!*targets[i]) throw std::runtime_error("urma_import_seg failed");
    }
    for (size_t lane = 0; lane < jetties_.size(); ++lane) {
      check_urma(urma_bind_jetty(jetties_[lane], p.tjetties[lane]), "urma_bind_jetty");
      ++bound_lanes_;
    }
  } catch (...) {
    while (bound_lanes_) {
      --bound_lanes_;
      check_urma(urma_unbind_jetty(jetties_[bound_lanes_]), "urma_unbind_jetty");
    }
    if (p.barrier_tseg)
      check_urma(urma_unimport_seg(p.barrier_tseg), "unimport barrier");
    if (p.atomic_tseg)
      check_urma(urma_unimport_seg(p.atomic_tseg), "unimport control");
    if (p.main_tseg)
      check_urma(urma_unimport_seg(p.main_tseg), "unimport main");
    for (auto* target : p.tjetties)
      check_urma(urma_unimport_jetty(target), "unimport jetty");
    p = {};
    throw;
  }
}
size_t UrmaTransport::capacity() const {
  static_assert(kMaxOutstandingSends <= 65536 || ProxyCtx::kMaxAtomicOps <= 65536,
                "URMA completion token has a 16-bit slot index");
  return std::min<size_t>(kMaxOutstandingSends, ProxyCtx::kMaxAtomicOps);
}
size_t UrmaTransport::acquire_context(UrmaWorkRequest const& request) {
  if (free_contexts_.empty()) throw std::runtime_error("URMA context capacity exhausted");
  size_t index = free_contexts_.back();
  auto& op = op_contexts_[index];
  if (op.generation == (UINT64_MAX >> 16))
    throw std::runtime_error("URMA completion generation exhausted");
  free_contexts_.pop_back();
  ++op.generation;
  op.active = true;
  op.wr_id = request.wr_id;
  op.is_write = request.kind == UrmaWorkRequest::Kind::Write;
  op.is_atomic = !op.is_write;
  op.child = op.anchor = UINT32_MAX;
  op.recovery = false;
  return index;
}
void UrmaTransport::release_context(size_t index) {
  auto& op = op_contexts_[index];
  if (!op.active) throw std::logic_error("double release of URMA context");
  if (op.anchor != UINT32_MAX) {
    auto& anchor = op_contexts_.at(op.anchor);
    if (anchor.active && anchor.child == index) anchor.child = UINT32_MAX;
  }
  if (op.child != UINT32_MAX) {
    auto& child = op_contexts_.at(op.child);
    if (child.active && child.anchor == index) child.anchor = UINT32_MAX;
  }
  op.child = op.anchor = UINT32_MAX;
  op.active = false;
  op.diagnostic={};
  free_contexts_.push_back(index);
}
void UrmaTransport::validate_request(Peer const& peer,
                                     UrmaWorkRequest const& request) const {
  if (request.kind != UrmaWorkRequest::Kind::Write &&
      request.kind != UrmaWorkRequest::Kind::Atomic &&
      request.kind != UrmaWorkRequest::Kind::Barrier)
    throw std::invalid_argument("invalid URMA operation kind");
  bool const write = request.kind == UrmaWorkRequest::Kind::Write;
  auto* remote = write ? peer.main_tseg :
      request.kind == UrmaWorkRequest::Kind::Atomic ? peer.atomic_tseg : peer.barrier_tseg;
  if (!remote || !main_tseg_) throw std::runtime_error("URMA unregistered segment");
  uint64_t bytes = write ? request.bytes : sizeof(uint64_t);
  if (!bytes) throw std::invalid_argument("zero-length URMA write");
  check_range(request.remote_offset, bytes, remote->seg.len);
  if (write) check_range(request.local_offset, bytes, main_buffer_len_);
  if (!write && remote_seg_addr(remote,request.remote_offset) % alignof(uint64_t))
    throw std::invalid_argument("unaligned URMA atomic");
}
void UrmaTransport::prepare(PreparedWr& out, Peer& peer, size_t lane,
                             UrmaWorkRequest const& request) {
  bool const write = request.kind == UrmaWorkRequest::Kind::Write;
  uccl::urma_diag::count(write ? uccl::urma_diag::Write : uccl::urma_diag::Fadd,
                         write ? request.bytes : 1);
  auto* remote = write ? peer.main_tseg :
      request.kind == UrmaWorkRequest::Kind::Atomic ? peer.atomic_tseg : peer.barrier_tseg;
  uint64_t bytes = write ? request.bytes : sizeof(uint64_t);
  out.context = acquire_context(request);
  op_contexts_[out.context].lane=static_cast<uint32_t>(lane);
  if(uccl::transport_timeline::active.load(std::memory_order_relaxed)){
    uccl::transport_timeline::record("remote_offset",request.wr_id,lane,request.remote_offset);
    uccl::transport_timeline::record(write?"payload_bytes":"atomic_value",request.wr_id,lane,write?request.bytes:static_cast<uint64_t>(request.value));
  }
  out.notify_pair = request.notify_pair;
  out.atomic_anchor = request.kind==UrmaWorkRequest::Kind::Atomic && request.fence;
  out.ring = request.ring==UINT32_MAX ? static_cast<uint32_t>(request.wr_id>>32) : request.ring;
  auto const& op = op_contexts_[out.context];
  out.dst.addr = remote_seg_addr(remote,request.remote_offset);
  out.dst.len = bytes;
  out.dst.tseg = remote;
  out.wr.user_ctx = (op.generation << 16) | out.context;
  out.wr.tjetty = peer.tjetties[lane];
  out.wr.flag.bs.complete_enable = 1; // default; eligible pairs may share the FADD CQE
  if (write) {
    if(mode_.normal)uccl::dispatch_trial::payload(ctx_->thread_idx,dispatch_trial_epoch_,request.bytes);
    out.src.addr = reinterpret_cast<uint64_t>(main_buffer_) + request.local_offset;
    out.src.len = bytes;
    out.src.tseg = main_tseg_;
    if(mode_.normal)uccl::urma_layout::record(request.bytes,out.src.addr,out.dst.addr,lane);
    out.wr.opcode = URMA_OPC_WRITE;
    // Normal uses the tested relaxed data path. Notifying FADDs retain a
    // strong fence on the SAME lane. LL defaults to conservative ordering;
    // only the explicit diagnostic A/B can select relaxed LL WRITEs.
    out.wr.flag.bs.place_order = mode_.normal || ll_relaxed_write_ ? URMA_RELAX_ORDER : URMA_STRONG_ORDER;
    out.wr.rw.src.sge = &out.src;
    out.wr.rw.src.num_sge = 1;
    out.wr.rw.dst.sge = &out.dst;
    out.wr.rw.dst.num_sge = 1;
  } else {
    atomic_operands_[out.context] = 0;
    out.src.addr = reinterpret_cast<uint64_t>(&atomic_operands_[out.context]);
    out.src.len = sizeof(uint64_t);
    out.src.tseg = operand_tseg_;
    out.wr.opcode = URMA_OPC_FADD;
    out.wr.flag.bs.place_order = URMA_STRONG_ORDER;
    out.wr.flag.bs.fence = request.fence ? URMA_FENCE_ENABLE : URMA_FENCE_DISABLE;
    out.wr.faa.src = &out.src;
    out.wr.faa.dst = &out.dst;
    out.wr.faa.operand = static_cast<uint64_t>(request.value);
  }
}
void UrmaTransport::submit_prepared(size_t lane, Peer& peer) {
  if (prepared_.empty()) return;
  for (size_t i=0; i<prepared_.size(); ++i) {
    // Non-EFA Normal kernels publish WRITE and ATOMIC separately. Discover
    // adjacent same-ring requests already present in this SAME submission;
    // never wait for an atomic, reorder work, or change the GPU command stream.
    if (mode_.normal && i+1<prepared_.size() &&
        op_contexts_[prepared_[i].context].is_write && prepared_[i+1].atomic_anchor &&
        prepared_[i].ring==prepared_[i+1].ring)
      prepared_[i].notify_pair=true;
    if (!prepared_[i].notify_pair) continue;
    auto& write=prepared_[i];
    auto& anchor=prepared_.at(i+1);
    uccl::urma_diag::count(uccl::urma_diag::PairCandidates);
    if (normal_completion_ == UrmaNormalCompletion::All) continue;
    // Placement order is not local completion order. Explicit comp_order
    // ensures the FADD cannot complete locally before the preceding WRITE.
    // Preserve STRONG placement and the original FADD fence independently.
    anchor.wr.flag.bs.comp_order=1;
    if (normal_completion_ == UrmaNormalCompletion::Paired) {
      write.wr.flag.bs.complete_enable=0;
      op_contexts_[anchor.context].child=static_cast<uint32_t>(write.context);
      op_contexts_[write.context].anchor=static_cast<uint32_t>(anchor.context);
      uccl::urma_diag::count(uccl::urma_diag::WriteCqeSuppressed);
    }
  }
  for (size_t i = 0; i < prepared_.size(); ++i)
    prepared_[i].wr.next = i+1 < prepared_.size() ? &prepared_[i+1].wr : nullptr;
  urma_jfs_wr_t* bad = nullptr;
  if (uccl::urma_diag::enabled.load(std::memory_order_relaxed))
    for (auto const& wr:prepared_)
      op_contexts_[wr.context].diagnostic=uccl::urma_diag::sample(
          op_contexts_[wr.context].anchor != UINT32_MAX ? uccl::urma_diag::PairedWriteToNotifyCqe :
          op_contexts_[wr.context].is_write ? uccl::urma_diag::WriteToObservedCqe :
                                            uccl::urma_diag::FaddToObservedCqe);
  uccl::urma_diag::Scope diagnostic(uccl::urma_diag::SdkPost,prepared_.size());
  if(mode_.normal && uccl::dispatch_trial::active.load() && uccl::dispatch_trial::trace.load()) {
    uint64_t bytes=0,writes=0,fadds=0;
    for(auto const& w:prepared_)if(op_contexts_[w.context].is_write){++writes;bytes+=w.src.len;}else ++fadds;
    uccl::dispatch_trial::submit(ctx_->thread_idx,lane,bytes,writes,fadds);
  }
  if(uccl::transport_timeline::active.load(std::memory_order_relaxed))
    for(auto const& w:prepared_)uccl::transport_timeline::record("post_begin",op_contexts_[w.context].wr_id,lane,w.wr.opcode);
  int rc = urma_post_jetty_send_wr(jetties_[lane], &prepared_[0].wr, &bad);
  if(uccl::transport_timeline::active.load(std::memory_order_relaxed))
    for(auto const& w:prepared_)uccl::transport_timeline::record("post_end",op_contexts_[w.context].wr_id,lane,rc);
  if (rc == URMA_SUCCESS) return;
  // The API specifies bad_wr as the first rejected request. Keep accepted
  // contexts/scratch alive for DMA; release only the rejected suffix.
  size_t first_bad = prepared_.size();
  for (size_t i = 0; i < prepared_.size(); ++i)
    if (bad == &prepared_[i].wr) { first_bad = i; break; }
  if (first_bad == prepared_.size()) {
    failed_ = true; // unknown acceptance: retain everything, teardown is fail-stop
  } else {
    // If only the silent WRITE of a pair was accepted, its rejected FADD
    // cannot retire it. Submit a fenced +0 into an existing HOST barrier
    // word on the SAME data lane, with ordered completion. Do not publish
    // the rejected GPU notification. All failures remain fail-stop.
    auto& rejected=prepared_[first_bad];
    bool const orphan=op_contexts_[rejected.context].child!=UINT32_MAX;
    for (size_t i = first_bad+(orphan ? 1 : 0); i < prepared_.size(); ++i)
      release_context(prepared_[i].context);
    if (orphan) {
      auto& op=op_contexts_[rejected.context];
      op.wr_id=UINT64_MAX;
      op.recovery=true;
      rejected.dst.addr=remote_seg_addr(peer.barrier_tseg,0);
      rejected.dst.tseg=peer.barrier_tseg;
      rejected.wr.faa.operand=0;
      rejected.wr.flag.bs.comp_order=1;
      rejected.wr.flag.bs.fence=URMA_FENCE_ENABLE;
      rejected.wr.next=nullptr;
      urma_jfs_wr_t* recovery_bad=nullptr;
      uccl::urma_diag::count(uccl::urma_diag::RecoveryMarkers);
      // If this also fails/has ambiguous acceptance, retain both slots and
      // registrations. Teardown will fail closed instead of freeing DMA data.
      int recovery_rc=urma_post_jetty_send_wr(jetties_[lane],&rejected.wr,&recovery_bad);
      std::fprintf(stderr,"[URMA_RECOVERY] original_rc=%d rejected_index=%zu recovery_rc=%d contexts_retained_until_completion=1\n",
                   rc,first_bad,recovery_rc);
    }
    if (first_bad) failed_ = true;
  }
  check_urma(rc,"urma_post_jetty_send_wr(batch)");
}
void UrmaTransport::post_batch(int dst_rank,
                               std::vector<UrmaWorkRequest> const& requests) {
  if (requests.empty()) return;
  if (failed_) throw std::runtime_error("URMA transport has failed");
  if (dst_rank < 0 || size_t(dst_rank) >= peers_.size() ||
      peers_[dst_rank].tjetties.size() != jetties_.size())
    throw std::runtime_error("URMA destination not connected");
  if (requests.size() > free_contexts_.size())
    throw std::runtime_error("URMA batch exceeds available context capacity");
  auto& peer = peers_[dst_rank];
  // Reject malformed batches before any lane has posted a prefix.
  for (size_t i=0; i<requests.size(); ++i) {
    auto const& request=requests[i];
    validate_request(peer,request);
    if (!request.notify_pair) continue;
    if (!mode_.normal || request.kind!=UrmaWorkRequest::Kind::Write ||
        request.wr_id!=UINT64_MAX || request.ring==UINT32_MAX || i+1==requests.size())
      throw std::invalid_argument("invalid URMA notifying WRITE pair");
    auto const& anchor=requests[i+1];
    if (anchor.kind!=UrmaWorkRequest::Kind::Atomic || !anchor.fence ||
        anchor.wr_id==UINT64_MAX || (anchor.wr_id>>32)!=request.ring)
      throw std::invalid_argument("URMA WRITE pair requires adjacent fenced FADD on same ring");
  }
  for (size_t lane = 0; lane < jetties_.size(); ++lane) {
    prepared_.clear();
    try {
      uccl::urma_diag::Scope diagnostic(uccl::urma_diag::GroupPrepare,requests.size());
      for (auto const& request : requests) {
        size_t selected = request.kind == UrmaWorkRequest::Kind::Barrier ?
            0 : mode_.data_lane(request.ring == UINT32_MAX ? request.wr_id :
                                (uint64_t(request.ring) << 32));
        if (selected != lane) continue;
        prepared_.emplace_back();
        try {
          prepare(prepared_.back(),peer,lane,request);
        } catch (...) {
          prepared_.pop_back();
          throw;
        }
      }
    } catch (...) {
      for (auto const& wr : prepared_) release_context(wr.context);
      throw;
    }
    try {
      submit_prepared(lane,peer);
    } catch (...) {
      if (outstanding()) failed_=true;
      throw;
    }
  }
}
void UrmaTransport::post_write(int dst_rank, uint64_t wr_id,
                               uint64_t local_offset, uint64_t remote_offset,
                               uint32_t bytes, bool low_latency) {
  if (low_latency == mode_.normal)
    throw std::invalid_argument("URMA WRITE mode does not match connection mode");
  UrmaWorkRequest request;
  request.wr_id = wr_id;
  request.local_offset = local_offset;
  request.remote_offset = remote_offset;
  request.bytes = bytes;
  post_batch(dst_rank,{request});
}
void UrmaTransport::post_atomic_add(int dst_rank, uint64_t wr_id,
                                    uint64_t offset, int64_t value, bool fence) {
  UrmaWorkRequest request;
  request.kind = UrmaWorkRequest::Kind::Atomic;
  request.wr_id = wr_id;
  request.remote_offset = offset;
  request.value = value;
  request.fence = fence;
  post_batch(dst_rank,{request});
}
void UrmaTransport::post_barrier_atomic_add(int dst_rank, uint64_t wr_id,
                                            size_t slot, uint64_t value) {
  if (slot >= kNumBarrierWords) throw std::out_of_range("invalid barrier slot");
  UrmaWorkRequest request;
  request.kind = UrmaWorkRequest::Kind::Barrier;
  request.wr_id = wr_id;
  request.remote_offset = slot * sizeof(uint64_t);
  request.value = static_cast<int64_t>(value);
  post_batch(dst_rank,{request});
}
uint64_t UrmaTransport::load_barrier_word(size_t slot) const {
  if (slot >= barrier_words_.size()) throw std::out_of_range("invalid barrier slot");
  return __atomic_load_n(&barrier_words_[slot],__ATOMIC_ACQUIRE);
}
int UrmaTransport::poll(TransportCompletion* out, int max) {
  if (!jfc_ || max <= 0) return 0;
  int total = 0;
  // SDK calls are limited to 16 CRs; drain several full batches without
  // forcing a trip around the Proxy loop for every 16 completions.
  while (total < max) {
    int wanted = std::min(16,max-total);
    urma_cr_t crs[16]{};
    int n = urma_poll_jfc(jfc_,wanted,crs);
    if (n < 0) throw std::runtime_error("urma_poll_jfc failed");
    for (int i = 0; i < n; ++i) {
      uint64_t token = crs[i].user_ctx;
      size_t index = token & 0xffffULL;
      uint64_t generation = token >> 16;
      if (index >= op_contexts_.size() || !op_contexts_[index].active ||
          op_contexts_[index].generation != generation) {
        failed_ = true;
        throw std::runtime_error("unexpected/stale URMA completion context");
      }
      auto const& op = op_contexts_[index];
      auto& completion = out[total+i];
      completion = {};
      completion.wr_id = op.wr_id;
      completion.is_write = op.is_write;
      completion.is_atomic = op.is_atomic;
      completion.data = crs[i].imm_data;
      completion.status = crs[i].status == URMA_CR_SUCCESS ? 0 : int(crs[i].status);
      if (completion.status) {
        failed_ = true;
        std::fprintf(stderr,"[URMA_COMPLETION_ERROR] rank=%d proxy=%d status=%d token=%llu wr_id=%llu write=%d atomic=%d\n",
            ctx_->local_rank,ctx_->thread_idx,completion.status,
            static_cast<unsigned long long>(token),static_cast<unsigned long long>(op.wr_id),op.is_write,op.is_atomic);
      }
      uccl::transport_timeline::record("cqe",op.wr_id,op.lane,completion.status);
      uccl::urma_diag::count(uccl::urma_diag::ObservedCqes);
      uccl::urma_diag::complete(op_contexts_[index].diagnostic);
      if (op.child != UINT32_MAX) {
        auto child=op.child;
        if (!op_contexts_[child].active || op_contexts_[child].anchor!=index) {
          failed_=true;
          throw std::runtime_error("invalid URMA paired completion ownership");
        }
        uccl::urma_diag::complete(op_contexts_[child].diagnostic);
        if (!op.recovery && op_contexts_[child].wr_id!=UINT64_MAX) {
          completion.has_secondary=true;
          completion.secondary_wr_id=op_contexts_[child].wr_id;
        }
        release_context(child);
        completion.retired_ops=2;
      }
      uccl::urma_diag::count(uccl::urma_diag::RetiredOps,completion.retired_ops);
      release_context(index);
    }
    total += n;
    if (n < wanted) break;
  }
  return total;
}
void UrmaTransport::drain() {
  auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(30);
  TransportCompletion out[16];
  while (outstanding()) {
    poll(out, 16);
    if (std::chrono::steady_clock::now() >= deadline)
      throw std::runtime_error("URMA teardown timed out with in-flight DMA");
    std::this_thread::yield();
  }
}
void UrmaTransport::destroy() {
  drain();
  while (bound_lanes_) {
    --bound_lanes_;
    check_urma(urma_unbind_jetty(jetties_[bound_lanes_]), "urma_unbind_jetty");
  }
  for (auto& p : peers_) {
    if (p.main_tseg) {
      check_urma(urma_unimport_seg(p.main_tseg), "unimport main");
      p.main_tseg = nullptr;
    }
    if (p.atomic_tseg) {
      check_urma(urma_unimport_seg(p.atomic_tseg), "unimport control");
      p.atomic_tseg = nullptr;
    }
    if (p.barrier_tseg) {
      check_urma(urma_unimport_seg(p.barrier_tseg), "unimport barrier");
      p.barrier_tseg = nullptr;
    }
    for (auto* target : p.tjetties)
      check_urma(urma_unimport_jetty(target), "unimport jetty");
    p.tjetties.clear();
  }
  peers_.clear();
  for (auto* jetty : jetties_)
    check_urma(urma_delete_jetty(jetty), "delete jetty");
  jetties_.clear();
  if (jfr_) {
    check_urma(urma_delete_jfr(jfr_), "delete jfr");
    jfr_ = nullptr;
  }
  if (jfc_) {
    check_urma(urma_delete_jfc(jfc_), "delete jfc");
    jfc_ = nullptr;
  }
  if (jfce_) {
    check_urma(urma_delete_jfce(jfce_), "delete jfce");
    jfce_ = nullptr;
  }
  {
    std::lock_guard<std::mutex> lock(g_resource_mutex);
    registrations_.clear();
    barrier_tseg_ = operand_tseg_ = atomic_tseg_ = main_tseg_ = nullptr;
    for (auto it = g_allocations.begin(); it != g_allocations.end();)
      if (it->second.expired())
        it = g_allocations.erase(it);
      else
        ++it;
    context_owner_.reset();
    urma_ctx_ = nullptr;
    for (auto it = g_contexts.begin(); it != g_contexts.end();)
      if (it->second.expired())
        it = g_contexts.erase(it);
      else
        ++it;
    for (auto it = g_segments.begin(); it != g_segments.end();)
      if (it->second.expired())
        it = g_segments.erase(it);
      else
        ++it;
  }
  if (init_acquired_) {
    std::lock_guard<std::mutex> lock(g_urma_init_mutex);
    if (--g_urma_init_refcount == 0) check_urma(urma_uninit(), "urma_uninit");
    init_acquired_ = false;
  }
  op_contexts_.clear();
  free_contexts_.clear();
  prepared_.clear();
  atomic_operands_.clear();
  barrier_words_.clear();
  failed_ = false;
}
#endif  // USE_URMA
