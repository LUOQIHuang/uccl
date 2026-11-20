#pragma once
// Read-only layout for provider Build ID a94dc33688722b1604ae5dc2d8f06b783b8e474a.
// Offsets follow udma_u_poll_jfc at 0xc0f0..0xc148 in the uploaded binary.
#include <atomic>
#include <algorithm>
#include <array>
#include <cstdint>
#include <cstring>
#include <cstdlib>
#include <dlfcn.h>
#include <link.h>
#include <mutex>
#include <memory>
#include <sstream>
#include <stdexcept>
#include <thread>
#include <vector>
#ifdef USE_URMA
#include "urma_api.h"
#include "urma_provider.h"

namespace uccl::cq_ready {
constexpr unsigned lookahead=16, limit=4096;
inline bool enabled(){static const bool on=[](){const char* p=std::getenv("FADD_PATH_CQ_READY");return p && !std::strcmp(p,"1");}();return on;}
struct BuildCheck {uintptr_t base;bool matched=false;};
inline int check_note(dl_phdr_info* info,size_t,void* argument){
  auto& check=*static_cast<BuildCheck*>(argument);
  if(info->dlpi_addr!=check.base)return 0;
  constexpr unsigned char expected[]={0xa9,0x4d,0xc3,0x36,0x88,0x72,0x2b,0x16,0x04,0xae,0x5d,0xc2,0xd8,0xf0,0x6b,0x78,0x3b,0x8e,0x47,0x4a};
  for(unsigned i=0;i<info->dlpi_phnum;++i){
    const auto& ph=info->dlpi_phdr[i];if(ph.p_type!=PT_NOTE)continue;
    auto p=reinterpret_cast<const unsigned char*>(info->dlpi_addr+ph.p_vaddr);
    size_t left=ph.p_memsz;
    while(left>=sizeof(ElfW(Nhdr))){
      ElfW(Nhdr) note{};std::memcpy(&note,p,sizeof(note));
      size_t name=(size_t(note.n_namesz)+3)&~size_t(3),desc=(size_t(note.n_descsz)+3)&~size_t(3);
      if(name>left-sizeof(note) || desc>left-sizeof(note)-name)break;
      if(note.n_type==NT_GNU_BUILD_ID && note.n_namesz==4 && note.n_descsz==sizeof(expected) &&
         !std::memcmp(p+sizeof(note),"GNU",4) && !std::memcmp(p+sizeof(note)+name,expected,sizeof(expected)))check.matched=true;
      size_t used=sizeof(note)+name+desc;p+=used;left-=used;
    }
  }
  return 1;
}
template<class T> inline T load(uintptr_t p){return __atomic_load_n(reinterpret_cast<const T*>(p),__ATOMIC_ACQUIRE);}
struct Layout {uintptr_t jfc=0,base=0;uint32_t shift=0,depth=0,mask=0,log_depth=0;};
inline std::mutex registry_mutex;
inline std::vector<Layout> layouts;
inline void bind(urma_jfc_t* jfc){
  if(!enabled())return;
  static_assert(sizeof(urma_jfc_t)==248,"CQ readiness requires the recorded private SDK ABI");
  Dl_info info{};
  if(!jfc || !jfc->urma_ctx || !jfc->urma_ctx->ops ||
     !dladdr(reinterpret_cast<void*>(jfc->urma_ctx->ops->poll_jfc),&info) || !info.dli_fbase)
    throw std::runtime_error("CQ_READY_PROVIDER_UNRESOLVED");
  BuildCheck check{reinterpret_cast<uintptr_t>(info.dli_fbase)};
  dl_iterate_phdr(check_note,&check);
  if(!check.matched)throw std::runtime_error("CQ_READY_PROVIDER_BUILD_ID_MISMATCH");
  Layout x;x.jfc=reinterpret_cast<uintptr_t>(jfc);
  x.base=load<uint64_t>(x.jfc+264);x.shift=load<uint32_t>(x.jfc+328);
  x.depth=load<uint32_t>(x.jfc+332);x.mask=load<uint32_t>(x.jfc+336);x.log_depth=load<uint32_t>(x.jfc+432);
  if(!x.base || x.base%4 || x.shift<6 || x.shift>8 || x.log_depth<4 || x.log_depth>20 ||
     x.depth!=(1u<<x.log_depth) || x.mask!=x.depth-1 || x.depth!=jfc->jfc_cfg.depth)
    throw std::runtime_error("CQ_READY_LAYOUT_REJECTED");
  std::lock_guard<std::mutex> lock(registry_mutex);layouts.push_back(x);
}
inline uint32_t ready_mask(const Layout& layout, uint32_t consumer) {
  uint32_t bits = 0;
  for (unsigned offset = 0; offset < lookahead; ++offset) {
    uint32_t index = consumer + offset;
    uintptr_t address = layout.base + (uintptr_t(index & layout.mask) << layout.shift);
    uint32_t word = load<uint32_t>(address);
    uint32_t owner = (word >> 2) & 1;
    uint32_t generation = (index >> layout.log_depth) & 1;
    if (owner != generation) bits |= 1u << offset;
  }
  return bits;
}
struct Completion {uint64_t wr_id,token;uint32_t position;};
struct Poll {uint64_t seq=0,begin=0,end=0,sdk_return=0;uint32_t ci=0,after_ci=0,ready=0;int count=0;std::vector<Completion> completed;};
struct Stream {std::atomic<bool> busy{false};uint64_t frame=0,dropped=0;Layout layout;int proxy=-1;std::vector<Poll> polls;Stream(){polls.reserve(limit);}};
inline std::vector<std::unique_ptr<Stream>> streams;
inline thread_local Stream* local=nullptr;
inline void before(urma_jfc_t* jfc, uint64_t frame, uint64_t seq, int proxy,
                   uint64_t (*clock)()) {
  if (!enabled() || !frame) return;
  if (!local) {
    std::lock_guard<std::mutex> lock(registry_mutex);
    auto it = std::find_if(layouts.begin(), layouts.end(), [&](const Layout& x) {
      return x.jfc == reinterpret_cast<uintptr_t>(jfc);
    });
    if (it == layouts.end()) throw std::runtime_error("CQ_READY_UNBOUND_QUEUE");
    auto next = std::make_unique<Stream>();
    local = next.get();
    local->layout = *it;
    local->proxy = proxy;
    streams.push_back(std::move(next));
  }
  local->busy.store(true, std::memory_order_release);
  if (local->frame != frame) {
    local->frame = frame;
    local->dropped = 0;
    local->polls.clear();
  }
  if (local->polls.size() == limit) {
    ++local->dropped;
    local->busy.store(false, std::memory_order_release);
    return;
  }
  Poll poll;
  poll.seq = seq;
  poll.begin = clock();
  poll.ci = load<uint32_t>(local->layout.jfc + 300);
  poll.ready = ready_mask(local->layout, poll.ci);
  poll.end = clock();
  local->polls.push_back(std::move(poll));
}
inline void after(uint64_t frame, int count, uint64_t returned) {
  if (!enabled() || !frame || !local || !local->busy.load()) return;
  auto& poll = local->polls.back();
  poll.count = count;
  poll.sdk_return = returned;
  poll.after_ci = load<uint32_t>(local->layout.jfc + 300);
  if (count < 0 || poll.after_ci < poll.ci ||
      poll.after_ci - poll.ci != static_cast<unsigned>(count))
    throw std::runtime_error("CQ_READY_COMPLETION_POSITION_MISMATCH");
  poll.completed.reserve(count);
  // Retain ownership through the final completion identity, so dump() cannot
  // observe a positive count before all corresponding request IDs are stored.
  if (!count) local->busy.store(false, std::memory_order_release);
}
inline void completed(uint64_t frame, uint64_t wr_id, uint64_t token, unsigned index) {
  if (!enabled() || !frame || !local || local->dropped) return;
  auto& poll = local->polls.back();
  poll.completed.push_back({wr_id, token, poll.ci + index});
  if (index + 1 == static_cast<unsigned>(poll.count))
    local->busy.store(false, std::memory_order_release);
}
inline std::string dump(uint64_t frame){
  std::lock_guard<std::mutex> lock(registry_mutex);std::ostringstream out;out<<"[";bool comma=false;
  for(const auto& owner:streams){auto s=owner.get();
    while(s->busy.load(std::memory_order_acquire))std::this_thread::yield();
    if(s->frame!=frame)continue;
    if(comma)out<<",";
    comma=true;
    out<<"{\"proxy\":"<<s->proxy<<",\"dropped\":"<<s->dropped<<",\"depth\":"<<s->layout.depth
       <<",\"stride\":"<<(1u<<s->layout.shift)<<",\"lookahead\":"<<lookahead<<",\"polls\":[";
    bool pc=false;
    for(const auto& p:s->polls){if(pc)out<<",";pc=true;
      out<<"{\"seq\":"<<p.seq<<",\"begin\":"<<p.begin<<",\"end\":"<<p.end<<",\"ci\":"<<p.ci
         <<",\"ready_mask\":"<<p.ready<<",\"after_ci\":"<<p.after_ci<<",\"count\":"<<p.count<<",\"sdk_return\":"<<p.sdk_return<<",\"completed\":[";
      bool cc=false;for(const auto& c:p.completed){if(cc)out<<",";cc=true;out<<"{\"wr_id\":"<<c.wr_id<<",\"token\":"<<c.token<<",\"position\":"<<c.position<<"}";}
      out<<"]}";
    }
    out<<"]}";
  }
  out<<"]";return out.str();
}
}
#else
namespace uccl::cq_ready {inline std::string dump(uint64_t){return "[]";}}
#endif
