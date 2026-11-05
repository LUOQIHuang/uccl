// Hardware-independent integration tests: real UMDK types + production
// transport.
#include "urma_transport.hpp"
#include "urma_registration_probe.hpp"
#include "gpu_aligned_allocation.hpp"
#include <cassert>
#include <algorithm>
#include <cstring>
#include <cerrno>
#include <deque>
#include <iostream>
#include <map>
#include <set>
#include <stdexcept>
#include <cuda.h>
#include <cuda_runtime_api.h>
#include <unistd.h>

namespace {
int resources = 0, init_count = 0, gpu_regs = 0, posts = 0;
bool fail_context = false, fail_post = false, fail_register = false;
bool fail_gpu_register = false;
bool fail_completion = false;
int selected_device = -1;
int fail_after = -1;
int fail_jetty_after = -1, fail_bind_after = -1;
std::vector<urma_jetty_t*> posted_jetties;
std::vector<size_t> posted_sizes;
int poll_calls = 0;
struct Posted {
  urma_jetty_t* jetty;
  uint64_t token;
  bool write;
  unsigned order, fence;
  unsigned complete, comp_order;
};
std::vector<Posted> posted_ops;
std::set<void const*> gpu_ptrs, host_ptrs, sync_ptrs;
std::map<urma_jfc_t*, std::deque<urma_cr_t>> completions;
urma_device_t device{};
template <class T>
T* create() {
  ++resources;
  return new T{};
}
template <class T>
int release(T* x) {
  if (x) {
    --resources;
    delete x;
  }
  return 0;
}
template <class F>
void rejects(F f) {
  bool threw = false;
  try {
    f();
  } catch (std::exception const&) {
    threw = true;
  }
  assert(threw);
}
}  // namespace
cudaError_t cudaSetDevice(int d) {
  selected_device = d;
  return 0;
}
cudaError_t cudaPointerGetAttributes(cudaPointerAttributes* a, void const* p) {
  if (gpu_ptrs.count(p)) {
    *a = {cudaMemoryTypeDevice, 2, const_cast<void*>(p), nullptr};
    return 0;
  }
  if (host_ptrs.count(p)) {
    *a = {cudaMemoryTypeHost, -1, const_cast<void*>(p), const_cast<void*>(p)};
    return 0;
  }
  return 1;
}
int gpu_frees = 0, host_frees = 0;
cudaError_t cudaMalloc(void** ptr, std::size_t) {
  static std::uint64_t storage[16];
  *ptr = storage;
  gpu_ptrs.insert(*ptr);
  return 0;
}
cudaError_t cudaHostAlloc(void** ptr, std::size_t, unsigned int) {
  static std::uint64_t storage[16];
  *ptr = storage;
  host_ptrs.insert(*ptr);
  return 0;
}
cudaError_t cudaMemset(void* ptr, int value, std::size_t bytes) {
  std::memset(ptr, value, bytes);
  return 0;
}
cudaError_t cudaMemcpy(void* dst, void const* src, std::size_t bytes, int) {
  std::memcpy(dst, src, bytes);
  return 0;
}
void* last_gpu_free = nullptr;
cudaError_t cudaFree(void* ptr) {
  last_gpu_free = ptr;
  ++gpu_frees;
  assert(gpu_regs == 0);
  return 0;
}
cudaError_t cudaFreeHost(void*) {
  ++host_frees;
  return 0;
}
char const* cudaGetErrorString(cudaError_t) { return "test CUDA error"; }
extern "C" {
CUresult cuPointerSetAttribute(void const* flag, CUpointer_attribute attr,
                               CUdeviceptr ptr) {
  assert(attr == CU_POINTER_ATTRIBUTE_SYNC_MEMOPS &&
         *static_cast<unsigned const*>(flag) == 1);
  sync_ptrs.insert(reinterpret_cast<void*>(ptr));
  return CUDA_SUCCESS;
}
urma_status_t urma_init(urma_init_attr_t*) {
  ++init_count;
  return 0;
}
urma_status_t urma_uninit() {
  --init_count;
  return 0;
}
urma_device_t** urma_get_device_list(int* n) {
  static urma_device_t* list[] = {&device};
  std::strcpy(device.name, "udma_test");
  device.type = URMA_TRANSPORT_UB;
  *n = 1;
  return list;
}
void urma_free_device_list(urma_device_t**) {}
urma_eid_info_t* urma_get_eid_list(urma_device_t*, uint32_t* n) {
  static urma_eid_info_t e{};
  *n = 1;
  return &e;
}
void urma_free_eid_list(urma_eid_info_t*) {}
urma_context_t* urma_create_context(urma_device_t* d, uint32_t) {
  if (fail_context) return nullptr;
  auto* c = create<urma_context_t>();
  c->dev = d;
  return c;
}
urma_status_t urma_delete_context(urma_context_t* x) { return release(x); }
urma_jfce_t* urma_create_jfce(urma_context_t*) { return create<urma_jfce_t>(); }
urma_status_t urma_delete_jfce(urma_jfce_t* x) { return release(x); }
urma_jfc_t* urma_create_jfc(urma_context_t*, urma_jfc_cfg_t*) {
  return create<urma_jfc_t>();
}
urma_status_t urma_delete_jfc(urma_jfc_t* x) {
  completions.erase(x);
  return release(x);
}
urma_jfr_t* urma_create_jfr(urma_context_t*, urma_jfr_cfg_t*) {
  return create<urma_jfr_t>();
}
urma_status_t urma_delete_jfr(urma_jfr_t* x) { return release(x); }
urma_jetty_t* urma_create_jetty(urma_context_t*, urma_jetty_cfg_t* cfg) {
  if(fail_jetty_after==0) { fail_jetty_after=-1; return nullptr; }
  if(fail_jetty_after>0) --fail_jetty_after;
  auto* x = create<urma_jetty_t>();
  x->jetty_cfg = *cfg;
  static uint32_t next_id = 42;
  x->jetty_id.id = next_id++;
  return x;
}
urma_status_t urma_delete_jetty(urma_jetty_t* x) { return release(x); }
urma_token_id_t* urma_alloc_token_id(urma_context_t* c) {
  auto* t = create<urma_token_id_t>();
  t->urma_ctx = c;
  t->token_id = 123;
  return t;
}
urma_status_t urma_free_token_id(urma_token_id_t* t) {
  errno = EINVAL;  // Cleanup must not overwrite the reported registration errno.
  return release(t);
}
urma_target_seg_t* urma_register_seg(urma_context_t* c, urma_seg_cfg_t* cfg) {
  if (fail_register) return nullptr;
  if (fail_gpu_register && cfg->is_gpu_seg) { errno = ENOSPC; return nullptr; }
  if (!cfg->token_id || !cfg->flag.bs.token_id_valid) return nullptr;
  bool gpu = gpu_ptrs.count(reinterpret_cast<void*>(cfg->va));
  if(!gpu) assert(cfg->va % uint64_t(sysconf(_SC_PAGESIZE))==0);
  if (gpu != bool(cfg->is_gpu_seg) || cfg->flag.bs.non_pin) return nullptr;
#ifndef USE_METAX
  if (gpu && !sync_ptrs.count(reinterpret_cast<void*>(cfg->va))) return nullptr;
#endif
  if (gpu) ++gpu_regs;
  auto* s = create<urma_target_seg_t>();
  s->urma_ctx = c;
  s->token_id = cfg->token_id;
  s->seg.ubva.va = cfg->va;
  s->seg.len = cfg->len;
  s->seg.token_id = cfg->token_id->token_id;
  s->seg.attr.bs.access = cfg->flag.bs.access;
  return s;
}
urma_status_t urma_unregister_seg(urma_target_seg_t* s) {
  if (gpu_ptrs.count(reinterpret_cast<void*>(s->seg.ubva.va))) --gpu_regs;
  return release(s);
}
urma_target_seg_t* urma_import_seg(urma_context_t*, urma_seg_t* seg,
                                   urma_token_t*, uint64_t,
                                   urma_import_seg_flag_t flag) {
  assert(flag.bs.mapping == URMA_SEG_NOMAP);
  auto* x = create<urma_target_seg_t>();
  x->seg = *seg;
  return x;
}
urma_status_t urma_unimport_seg(urma_target_seg_t* s) { return release(s); }
urma_target_jetty_t* urma_import_jetty(urma_context_t*, urma_rjetty_t* r,
                                       urma_token_t*) {
  assert(r->trans_mode == URMA_TM_RC && r->type == URMA_JETTY &&
         r->tp_type == URMA_RTP);
  auto* t = create<urma_target_jetty_t>();
  t->id = r->jetty_id;
  return t;
}
urma_status_t urma_unimport_jetty(urma_target_jetty_t* t) { return release(t); }
urma_status_t urma_bind_jetty(urma_jetty_t* j, urma_target_jetty_t* t) {
  if(fail_bind_after==0) { fail_bind_after=-1; return URMA_EINVAL; }
  if(fail_bind_after>0) --fail_bind_after;
  assert(!j->remote_jetty);
  j->remote_jetty = t;
  return 0;
}
urma_status_t urma_unbind_jetty(urma_jetty_t* j) {
  j->remote_jetty = nullptr;
  return 0;
}
urma_status_t urma_post_jetty_send_wr(urma_jetty_t* j, urma_jfs_wr_t* w,
                                      urma_jfs_wr_t** bad) {
  if (fail_post) { *bad = w; return URMA_EINVAL; }
  ++posts;
  posted_jetties.push_back(j);
  size_t batch_size = 0;
  for (; w; w = w->next) {
  if (fail_after == 0) { *bad = w; fail_after = -1; return URMA_EINVAL; }
  if (fail_after > 0) --fail_after;
  ++batch_size;
  posted_ops.push_back({j,w->user_ctx,w->opcode==URMA_OPC_WRITE,
                       unsigned(w->flag.bs.place_order),unsigned(w->flag.bs.fence),
                       unsigned(w->flag.bs.complete_enable),unsigned(w->flag.bs.comp_order)});
  if (w->opcode == URMA_OPC_WRITE) {
    std::memcpy(reinterpret_cast<void*>(w->rw.dst.sge->addr),
                reinterpret_cast<void*>(w->rw.src.sge->addr),
                w->rw.src.sge->len);
  } else {
    assert(w->opcode == URMA_OPC_FADD);
    auto* dst = reinterpret_cast<uint64_t*>(w->faa.dst->addr);
    *reinterpret_cast<uint64_t*>(w->faa.src->addr) = *dst;
    *dst += w->faa.operand;
  }
  urma_cr_t c{};
  c.status = fail_completion ? URMA_CR_LOC_LEN_ERR : URMA_CR_SUCCESS;
  c.user_ctx = w->user_ctx;
  if (w->flag.bs.complete_enable || fail_completion)
    completions[j->jetty_cfg.jfs_cfg.jfc].push_back(c);
  }
  posted_sizes.push_back(batch_size);
  return 0;
}
int urma_poll_jfc(urma_jfc_t* j, int max, urma_cr_t* out) {
  ++poll_calls;
  assert(max <= 16);
  int n = 0;
  auto& q = completions[j];
  while (n < max && !q.empty()) {
    out[n++] = q.front();
    q.pop_front();
  }
  return n;
}
}
int main() {
  unsetenv("UCCL_URMA_NORMAL_COMPLETION");
  ProxyCtx ctx;
  // Failure before a context exists must release global initialization
  // ownership.
  fail_context = true;
  {
    UrmaTransport t;
    rejects([&] { t.init(ctx); });
  }
  fail_context = false;
  assert(init_count == 0 && resources == 0);
  fail_jetty_after=2;
  {
    UrmaTransport t; t.configure(true,8);
    rejects([&] { t.init(ctx); });
  }
  assert(init_count==0 && resources==0);
  alignas(65536) uint64_t a[32]{}, b[32]{};
  // Also cover ARM64 hosts built with 64KiB base pages.
  alignas(65536) uint64_t ac[8]{}, bc[8]{};
#ifdef USE_METAX
  gpu_ptrs = {a, b, ac, bc};
  host_ptrs.clear();
#else
  gpu_ptrs = {a, b};
  host_ptrs = {ac, bc};
#endif
  {
    UrmaTransport left, right;
    left.init(ctx);
    right.init(ctx);
#ifndef USE_METAX
    rejects([&] { left.register_main_buffer(ac, sizeof(ac), 2); });
    rejects([&] { left.register_main_buffer(a, sizeof(a), 0); });
#endif
    left.register_main_buffer(a, sizeof(a), 2);
    right.register_main_buffer(b, sizeof(b), 2);
#ifdef USE_METAX
    assert(sync_ptrs.empty());
#endif
#ifdef USE_METAX
    assert(gpu_regs == 2);
#else
    assert(selected_device == 2 && gpu_regs == 2);
#endif
    {
      UrmaTransport duplicate;
      duplicate.init(ctx);
      duplicate.register_main_buffer(a, sizeof(a), 2);
      assert(gpu_regs == 2);  // same device/context/range must pin exactly once
      duplicate.destroy();
      assert(gpu_regs == 2);  // first registration is still live
    }
    {
      UrmaTransport overlapping;
      overlapping.init(ctx);
      gpu_ptrs.insert(a + 1);
      rejects([&] { overlapping.register_main_buffer(a + 1, 8, 2); });
      gpu_ptrs.erase(a + 1);
    }
    left.register_atomic_buffer(ac, sizeof(ac), 2);
    right.register_atomic_buffer(bc, sizeof(bc), 2);
    auto ri = right.local_info(), li = left.local_info();
    auto broken = ri;
    broken.urma_main_seg_len = 1;
    rejects([&] { left.connect_peer(1, broken); });
    broken = ri;
    ++broken.urma_wire_version;
    rejects([&] { left.connect_peer(1, broken); });
    left.connect_peer(1, ri);
    right.connect_peer(0, li);
    a[0] = 0x1122334455667788ULL;
    left.post_write(1, 7, 0, 8, 8, false);
    left.post_atomic_add(1, 8, 0, 3, true);
    assert(b[1] == a[0] && bc[0] == 3);
    TransportCompletion cs[8];
    assert(left.poll(cs, 8) == 2);
    assert(cs[0].wr_id == 7 && cs[1].wr_id == 8);
    int n = posts;
    rejects([&] { left.post_write(1, 9, UINT64_MAX, 0, 8, false); });
    rejects([&] { left.post_write(1, 9, 0, sizeof(b), 8, false); });
    rejects([&] { left.post_atomic_add(1, 9, 1, 1, true); });
    assert(posts == n);
    fail_post = true;
    for (int i = 0; i < 32; ++i)
      rejects([&] { left.post_atomic_add(1, 10, 0, 1, true); });
    fail_post = false;
    left.post_atomic_add(1, 11, 0, -1, true);
    assert(left.poll(cs, 8) == 1 && bc[0] == 2);
    left.post_barrier_atomic_add(1, 12, 0, 1);
    assert(left.poll(cs, 8) == 1 && right.load_barrier_word(0) == 1);
    // destroy drains outstanding work and is idempotent.
    left.post_write(1, 13, 0, 16, 8, false);
    left.destroy();
    left.destroy();
    fail_completion = true;
    right.post_atomic_add(0, 14, 0, 1, true);
    assert(right.poll(cs, 8) == 1 && cs[0].status != 0);
    rejects([&] { right.post_atomic_add(0, 15, 0, 1, true); });
    fail_completion = false;
    right.destroy();
  }
  assert(resources == 0 && init_count == 0 && gpu_regs == 0);
  // Mode-specific endpoint routing and linked submission; same-ring WRITE
  // and ATOMIC must stay together, including internal/sentinel WRITE IDs.
  for (bool normal : {true,false}) {
    UrmaTransport left,right;
    left.configure(normal,normal ? 8 : 1);
    right.configure(normal,normal ? 8 : 1);
    left.init(ctx); right.init(ctx);
    rejects([&] { left.configure(normal,1); });
    left.register_main_buffer(a,sizeof(a),2); right.register_main_buffer(b,sizeof(b),2);
    left.register_atomic_buffer(ac,sizeof(ac),2); right.register_atomic_buffer(bc,sizeof(bc),2);
    auto li=left.local_info(), ri=right.local_info();
    assert(li.urma_channels==(normal ? 8U : 1U));
    assert(li.urma_jetty_count==(normal ? 9U : 1U));
    auto bad=ri; bad.urma_normal_mode=!normal;
    rejects([&] { left.connect_peer(1,bad); });
    bad=ri; ++bad.urma_jetty_count;
    rejects([&] { left.connect_peer(1,bad); });
    if(normal) {
      int before=resources; fail_bind_after=1;
      rejects([&] { left.connect_peer(1,ri); });
      assert(resources==before);
    }
    left.connect_peer(1,ri); right.connect_peer(0,li);
    std::memset(b,0,sizeof(b)); std::memset(bc,0,sizeof(bc));
    std::vector<UrmaWorkRequest> batch;
    for (unsigned ring=0;ring<3;++ring) {
      UrmaWorkRequest w;
      w.wr_id=(uint64_t(ring)<<32)|20; w.bytes=8;
      w.remote_offset=16+8*ring;
      if (ring==2) { w.wr_id=UINT64_MAX; w.ring=2; }
      batch.push_back(w);
    }
    for (unsigned ring=0;ring<3;++ring) {
      UrmaWorkRequest w;
      w.kind=UrmaWorkRequest::Kind::Atomic;
      w.wr_id=(uint64_t(ring)<<32)|21; w.remote_offset=8*ring; w.value=5;
      batch.push_back(w);
    }
    int before=posts;
    size_t seen=posted_ops.size();
    left.post_batch(1,batch);
    assert(posts-before==(normal ? 3 : 1));
    for (size_t i=seen;i<posted_ops.size();++i) {
      auto const& op=posted_ops[i];
      assert(op.order==unsigned(op.write && normal ? URMA_RELAX_ORDER : URMA_STRONG_ORDER));
      if (!op.write) assert(op.fence==URMA_FENCE_ENABLE);
    }
    if (normal) {
      assert(posted_ops[seen].jetty==posted_ops[seen+1].jetty);
      assert(posted_ops[seen+2].jetty==posted_ops[seen+3].jetty);
      assert(posted_ops[seen].jetty!=posted_ops[seen+2].jetty);
      assert(posted_ops[seen+4].jetty!=posted_ops[seen].jetty);
    }
    assert(b[2]==a[0] && b[3]==a[0] && b[4]==a[0]);
    assert(bc[0]==5 && bc[1]==5 && bc[2]==5);
    TransportCompletion out[64];
    assert(left.poll(out,64)==6);
    assert(std::count_if(out,out+6,[](TransportCompletion const& c){return c.wr_id==UINT64_MAX;})==1);
    auto* data_jetty=posted_jetties.back();
    left.post_barrier_atomic_add(1,30,0,1);
    assert((posted_jetties.back()!=data_jetty)==normal);
    assert(left.poll(out,64)==1 && right.load_barrier_word(0)==1);
    rejects([&] { left.post_write(1,31,0,0,8,normal); });
    batch.assign(left.capacity(),{});
    for (size_t i=0;i<batch.size();++i) {
      batch[i].wr_id=(uint64_t(2)<<32)|i; batch[i].bytes=8;
    }
    left.post_batch(1,batch);
    rejects([&] { left.post_write(1,32,0,0,8,!normal); });
    auto* jetty=posted_jetties.back();
    auto& cq=completions[jetty->jetty_cfg.jfs_cfg.jfc];
    std::reverse(cq.begin(),cq.end());
    int polls_before=poll_calls;
    assert(left.poll(out,64)==int(left.capacity()));
    assert(poll_calls-polls_before>=2);
    // Successful pool reuse gets a different generation token.
    uint64_t previous=posted_ops[posted_ops.size()-batch.size()].token;
    left.post_write(1,33,0,0,8,!normal);
    assert((posted_ops.back().token & 0xffffULL)==(previous & 0xffffULL));
    assert(posted_ops.back().token!=previous);
    assert(left.poll(out,64)==1);
    urma_cr_t stale{}; stale.user_ctx=posted_ops.back().token;
    stale.status=URMA_CR_SUCCESS;
    cq.push_back(stale);
    rejects([&] { left.poll(out,64); });
    left.destroy(); right.destroy();
    assert(resources==0 && init_count==0 && gpu_regs==0);
  }
  {
    // Partial post failure: accepted prefix stays live, rejected suffix is
    // released, and the transport refuses further submissions.
    UrmaTransport left,right;
    left.init(ctx); right.init(ctx);
    left.register_main_buffer(a,sizeof(a),2); right.register_main_buffer(b,sizeof(b),2);
    left.register_atomic_buffer(ac,sizeof(ac),2); right.register_atomic_buffer(bc,sizeof(bc),2);
    left.connect_peer(1,right.local_info()); right.connect_peer(0,left.local_info());
    std::vector<UrmaWorkRequest> batch(3);
    for (size_t i=0;i<batch.size();++i) { batch[i].wr_id=40+i; batch[i].bytes=8; }
    fail_after=1;
    rejects([&] { left.post_batch(1,batch); });
    TransportCompletion out[16];
    assert(left.poll(out,16)==1 && out[0].wr_id==40);
    rejects([&] { left.post_write(1,44,0,0,8,false); });
    left.destroy(); right.destroy();
    assert(resources==0 && init_count==0 && gpu_regs==0);
  }
  assert((UrmaMode{true,1}.atomic_value(7,true)==7));
  assert((UrmaMode{false,1}.atomic_value(7,true)==1));
  // An owning proxy may stop before another proxy sharing its allocation.
  {
    auto main_owner = UrmaTransport::acquire_buffer_ownership(a, false, 2);
    auto control_owner = UrmaTransport::acquire_buffer_ownership(
        ac,
#ifdef USE_METAX
        false,
#else
        true,
#endif
        2);
    UrmaTransport first, last;
    first.init(ctx);
    last.init(ctx);
    first.register_main_buffer(a, sizeof(a), 2);
    last.register_main_buffer(a, sizeof(a), 2);
    first.register_atomic_buffer(ac, sizeof(ac), 2);
    last.register_atomic_buffer(ac, sizeof(ac), 2);
    first.destroy();
    main_owner.reset();
    control_owner.reset();
#ifdef USE_METAX
    assert(gpu_frees == 0 && host_frees == 0 && gpu_regs == 2);
#else
    assert(gpu_frees == 0 && host_frees == 0 && gpu_regs == 1);
#endif
    last.destroy();
#ifdef USE_METAX
    assert(gpu_frees == 0 && host_frees == 0 && gpu_regs == 0);
#else
    assert(gpu_frees == 1 && host_frees == 1 && gpu_regs == 0);
#endif
  }
  // DLPack owns the same allocation; proxy teardown must not free it twice.
  {
    auto tensor_owner = UrmaTransport::acquire_buffer_ownership(a, false, 2);
    auto proxy_owner = UrmaTransport::acquire_buffer_ownership(a, false, 2);
    UrmaTransport t;
    t.init(ctx);
    t.register_main_buffer(a, sizeof(a), 2);
    t.destroy();
    proxy_owner.reset();
#ifndef USE_METAX
    assert(gpu_frees == 1);
#endif
    tensor_owner.reset();
#ifndef USE_METAX
    assert(gpu_frees == 2);
#endif
  }
  fail_register = true;
  {
    UrmaTransport t;
    rejects([&] { t.init(ctx); });
  }
  fail_register = false;
  assert(resources == 0 && init_count == 0);
  {
    // Fine-grained allocations may start at 0x600 within a registration page.
    // Keep the raw base alive until the final shared registration is released.
    alignas(65536) unsigned char storage[98304]{};
    void* raw = storage + 0x600;
    void* view = uccl::gpu_p2p::aligned_allocation_view(raw, 4096);
    assert(reinterpret_cast<uintptr_t>(view) % 4096 == 0);
    assert(static_cast<unsigned char*>(view) + 81960 <=
           static_cast<unsigned char*>(raw) + uccl::gpu_p2p::aligned_allocation_size(81960,4096));
    gpu_ptrs.insert(view);
    auto owner = UrmaTransport::acquire_buffer_ownership(view,false,2,raw);
    rejects([&] { UrmaTransport::acquire_buffer_ownership(view,false,2,view); });
    UrmaTransport first, last;
    first.init(ctx); last.init(ctx);
#ifdef USE_METAX
    rejects([&] { first.register_atomic_buffer(raw,81960,2); });
    first.register_atomic_buffer(view,81960,2);
    last.register_atomic_buffer(view,81960,2);
#else
    first.register_main_buffer(view,81960,2);
    last.register_main_buffer(view,81960,2);
#endif
    owner.reset();
    first.destroy();
    assert(gpu_regs==1);
    last.destroy();
#ifndef USE_METAX
    assert(last_gpu_free==raw);
#endif
    gpu_ptrs.erase(view);
    assert(resources==0 && init_count==0 && gpu_regs==0);
  }
  probe_urma_registration(a, sizeof(a), ac, sizeof(ac), 2, true, 8, 4);
  assert(resources == 0 && init_count == 0 && gpu_regs == 0);
  probe_urma_registration(a, sizeof(a), ac, sizeof(ac), 2, false, 1, 1);
  assert(resources == 0 && init_count == 0 && gpu_regs == 0);
  {
    // Failed GPU registrations must preserve errno through cleanup and must
    // not publish a cache entry; a later valid registration must really retry.
    UrmaTransport t;
    t.init(ctx);
    fail_gpu_register = true;
    try {
      t.register_main_buffer(a, sizeof(a), 2);
      assert(false);
    } catch (std::runtime_error const& error) {
      std::string message = error.what();
      assert(message.find("errno=" + std::to_string(ENOSPC)) != std::string::npos);
      assert(message.find("atomic=0") != std::string::npos);
    }
    fail_gpu_register = false;
    assert(gpu_regs == 0);
    t.register_main_buffer(a, sizeof(a), 2);
    assert(gpu_regs == 1);
    t.destroy();
    assert(resources == 0 && init_count == 0 && gpu_regs == 0);
  }
  // Paired CQE suppression: exercise actual SDK flags and context accounting,
  // including a rejected FADD after an already-accepted silent WRITE.
  for (auto const* policy : {"all","ordered","paired"}) {
    for (bool inline_notify:{true,false}) {
    for (int failure : {-1,0,1,2,3}) {
      // 3 injects error CQEs, including one for the unsignaled WRITE.
      setenv("UCCL_URMA_NORMAL_COMPLETION",policy,1);
      UrmaTransport left,right;
      left.configure(true,8); right.configure(true,8);
      unsetenv("UCCL_URMA_NORMAL_COMPLETION");
      left.init(ctx); right.init(ctx);
      left.register_main_buffer(a,sizeof(a),2); right.register_main_buffer(b,sizeof(b),2);
      left.register_atomic_buffer(ac,sizeof(ac),2); right.register_atomic_buffer(bc,sizeof(bc),2);
      left.connect_peer(1,right.local_info()); right.connect_peer(0,left.local_info());
      UrmaWorkRequest write,atomic,extra;
      write.wr_id=inline_notify ? UINT64_MAX : (2ULL<<32)|98;
      write.ring=2; write.bytes=8; write.notify_pair=inline_notify;
      atomic.kind=UrmaWorkRequest::Kind::Atomic; atomic.wr_id=(2ULL<<32)|99; atomic.value=3;
      extra.wr_id=(3ULL<<32)|100; extra.bytes=8;
      int64_t old=static_cast<int64_t>(bc[0]);
      if (failure>=0 && failure<=2) fail_after=failure;
      fail_completion=failure==3;
      size_t seen=posted_ops.size();
      if (failure>=0 && failure<=2) rejects([&]{left.post_batch(1,{write,atomic,extra});});
      else left.post_batch(1,{write,atomic,extra});
      fail_completion=false;
      TransportCompletion out[16];
      if (failure==-1 && std::string(policy)=="paired") {
        auto& live=completions[posted_ops[seen].jetty->jetty_cfg.jfs_cfg.jfc];
        auto saved=live; live.clear();
        assert(left.poll(out,16)==0);
        std::vector<UrmaWorkRequest> too_many(left.capacity()-2,extra);
        rejects([&]{left.post_batch(1,too_many);}); // all THREE logical slots live
        live=saved;
      }
      int n=left.poll(out,16);
      unsigned retired=0; for(int i=0;i<n;++i) retired+=out[i].retired_ops;
      assert(retired==unsigned(failure==0 ? 0 : failure==1 && std::string(policy)!="paired" ? 1 : failure==1 || failure==2 ? 2 : 3));
      if (failure==1 && std::string(policy)=="paired") {
        assert(n==1 && out[0].wr_id==UINT64_MAX && out[0].retired_ops==2);
        assert(!out[0].has_secondary);
        assert(bc[0]==uint64_t(old)); // recovery +0 never publishes original notify
        assert(right.load_barrier_word(0)==0);
      }
      if (failure==-1) {
        assert(posted_ops[seen].complete==unsigned(std::string(policy)!="paired"));
        assert(posted_ops[seen+1].complete==1);
        assert(posted_ops[seen+1].comp_order==unsigned(std::string(policy)!="all"));
        assert(posted_ops[seen+1].fence==URMA_FENCE_ENABLE);
        assert(n==(std::string(policy)=="paired" ? 2 : 3));
        if (std::string(policy)=="paired") {
          assert(out[0].has_secondary==!inline_notify);
          if (!inline_notify) assert(out[0].secondary_wr_id==write.wr_id);
        }
        assert(bc[0]==uint64_t(old+3));
        // Pool is fully reusable after the anchor retires both slots.
        std::vector<UrmaWorkRequest> full(left.capacity(),extra);
        left.post_batch(1,full); assert(left.poll(out,16)==16);
        // Never pair across submissions or across different rings sharing
        // a lane, and never use an unfenced atomic as completion anchor.
        auto standalone=write;standalone.notify_pair=false;
        standalone.wr_id=(2ULL<<32)|98;
        left.post_batch(1,{standalone});assert(left.poll(out,16)==1 && out[0].retired_ops==1);
        auto unfenced=atomic;unfenced.fence=false;
        left.post_batch(1,{standalone,unfenced});
        assert(left.poll(out,16)==2 && out[0].retired_ops==1 && out[1].retired_ops==1);
        auto other_ring=atomic;other_ring.wr_id=(10ULL<<32)|99;
        left.post_batch(1,{standalone,other_ring});
        assert(left.poll(out,16)==2 && out[0].retired_ops==1 && out[1].retired_ops==1);
      }
      if (failure==3) {
        assert(n==3);
        for(int i=0;i<n;++i) assert(out[i].status!=0 && out[i].retired_ops==1);
      }
      if (inline_notify) rejects([&]{left.post_batch(1,{write});});
      auto wrong=atomic;wrong.wr_id=(10ULL<<32)|99; // same lane, DIFFERENT ring
      if (inline_notify) rejects([&]{left.post_batch(1,{write,wrong});});
      left.destroy();right.destroy();
      assert(resources==0 && init_count==0 && gpu_regs==0);
    }
    }
  }
  unsetenv("UCCL_URMA_NORMAL_COMPLETION");
  // Exercise opt-in diagnostics through the real submission and completion
  // code in both modes; existing tests above exercise the disabled path.
  // A/B changes only LL WRITE ordering. Verify the actual submitted SDK WRs,
  // not just the parser; configuration remains frozen if the env changes.
  for (auto const* order : {"strong","relax"}) for (bool normal : {true,false}) {
    setenv("UCCL_URMA_LL_WRITE_ORDER",order,1);
    UrmaTransport left,right;
    left.configure(normal,normal ? 8 : 1); right.configure(normal,normal ? 8 : 1);
    unsetenv("UCCL_URMA_LL_WRITE_ORDER");
    left.init(ctx); right.init(ctx);
    left.register_main_buffer(a,sizeof(a),2); right.register_main_buffer(b,sizeof(b),2);
    left.register_atomic_buffer(ac,sizeof(ac),2); right.register_atomic_buffer(bc,sizeof(bc),2);
    left.connect_peer(1,right.local_info()); right.connect_peer(0,left.local_info());
    UrmaWorkRequest write,atomic;
    write.wr_id=100; write.bytes=8;
    atomic.kind=UrmaWorkRequest::Kind::Atomic; atomic.wr_id=101; atomic.value=1;
    size_t seen=posted_ops.size();
    left.post_batch(1,{write,atomic});
    assert(posted_ops.size()==seen+2);
    assert(posted_ops[seen].order==unsigned(normal || std::string(order)=="relax" ? URMA_RELAX_ORDER : URMA_STRONG_ORDER));
    assert(posted_ops[seen+1].order==URMA_STRONG_ORDER);
    assert(posted_ops[seen+1].fence==URMA_FENCE_ENABLE);
    assert(posted_ops[seen].jetty==posted_ops[seen+1].jetty);
    TransportCompletion out[4]; assert(left.poll(out,4)==2);
    left.post_barrier_atomic_add(1,102,0,1);
    assert(posted_ops.back().order==URMA_STRONG_ORDER && posted_ops.back().fence==URMA_FENCE_ENABLE);
    assert(left.poll(out,4)==1);
    left.destroy(); right.destroy();
    assert(resources==0 && init_count==0 && gpu_regs==0);
  }
  setenv("UCCL_URMA_LL_WRITE_ORDER","invalid",1);
  { UrmaTransport t; rejects([&] {t.configure(false,1);}); }
  unsetenv("UCCL_URMA_LL_WRITE_ORDER");
  for (bool normal:{true,false}) {
    UrmaTransport left,right;
    left.configure(normal,normal ? 8 : 1); right.configure(normal,normal ? 8 : 1);
    left.init(ctx); right.init(ctx);
    left.register_main_buffer(a,sizeof(a),2); right.register_main_buffer(b,sizeof(b),2);
    left.register_atomic_buffer(ac,sizeof(ac),2); right.register_atomic_buffer(bc,sizeof(bc),2);
    left.connect_peer(1,right.local_info()); right.connect_peer(0,left.local_info());
    uccl::urma_diag::enabled=true;
    for (int i=0;i<64;++i) {
      UrmaWorkRequest write,atomic;
      write.wr_id=2*i; write.bytes=8;
      atomic.kind=UrmaWorkRequest::Kind::Atomic; atomic.wr_id=2*i+1; atomic.value=1;
      left.post_batch(1,{write,atomic});
      TransportCompletion out[4]; assert(left.poll(out,4)==2);
    }
    uccl::urma_diag::enabled=false;
    left.destroy(); right.destroy();
    assert(resources==0 && init_count==0 && gpu_regs==0);
  }
  auto const& diagnostic=uccl::urma_diag::records.at(0)->metrics;
  assert(diagnostic[uccl::urma_diag::Write].calls==128);
  assert(diagnostic[uccl::urma_diag::Fadd].calls==128);
  assert(diagnostic[uccl::urma_diag::SdkPost].calls==128);
  assert(diagnostic[uccl::urma_diag::WriteToObservedCqe].calls==2);
  assert(diagnostic[uccl::urma_diag::FaddToObservedCqe].calls==2);
  std::cout
      << "URMA transport tests passed (simulated hardware; SDK types selected by runner)\n";
}
