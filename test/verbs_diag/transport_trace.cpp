// Phase-scoped, read-only URMA API instrumentation. No GPU kernel rebuild.
#include <urma_api.h>
#include <atomic>
#include <cerrno>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <dlfcn.h>
#include <pthread.h>
#include <sched.h>
#include <sys/syscall.h>
#include <time.h>
#include <unistd.h>

namespace {
using Post = urma_status_t (*)(urma_jetty_t*, urma_jfs_wr_t*, urma_jfs_wr_t**);
using Poll = int (*)(urma_jfc_t*, int, urma_cr_t*);
using Register = urma_target_seg_t* (*)(urma_context_t*, urma_seg_cfg_t*);
Post real_post;
Poll real_poll;
Register real_register;
pthread_once_t once = PTHREAD_ONCE_INIT;
char real_path[4096]{};
void resolve() {
  const char* path = std::getenv("UCCL_URMA_TRACE_REAL_LIB");
  if (!path || path[0] != '/') return;
  void* lib = dlopen(path, RTLD_NOW | RTLD_LOCAL);
  if (!lib) return;
  real_post = reinterpret_cast<Post>(dlsym(lib, "urma_post_jetty_send_wr"));
  real_poll = reinterpret_cast<Poll>(dlsym(lib, "urma_poll_jfc"));
  real_register = reinterpret_cast<Register>(dlsym(lib, "urma_register_seg"));
  Dl_info info{};
  if (real_post && dladdr(reinterpret_cast<void*>(real_post), &info))
    std::snprintf(real_path, sizeof(real_path), "%s", info.dli_fname);
}
uint64_t now() {
  timespec ts{};
  clock_gettime(CLOCK_MONOTONIC, &ts);
  return uint64_t(ts.tv_sec) * 1000000000ULL + ts.tv_nsec;
}
struct Timing {
  uint64_t samples{}, total_ns{}, max_ns{}, log2_ns[64]{};
  void add(uint64_t ns) {
    ++samples; total_ns += ns;
    if (ns > max_ns) max_ns = ns;
    unsigned bin = ns ? 63U - unsigned(__builtin_clzll(ns)) : 0;
    ++log2_ns[bin];
  }
};
struct Entry { uint64_t key{}, start{}; unsigned kind{}; };
constexpr unsigned kMap = 16384, kThreads = 64;
struct Stats {
  uint64_t post_calls{}, post_errors{}, poll_calls{}, empty_polls{}, poll_errors{};
  uint64_t completed{}, completion_errors{}, attempted_wrs{}, posted_wrs{};
  uint64_t write_wrs{}, fadd_wrs{}, other_wrs{}, write_bytes{}, batch_max{};
  uint64_t complete_enabled{}, fence_enabled{}, inflight{}, peak_inflight{};
  uint64_t unknown_completions{}, map_overflow{}, context_errors{};
  uint64_t write_order[16]{}, fadd_order[16]{};
  // Exact remainder classes: 0, 64, 128, 192, other (mod 256).
  uint64_t length_mod256[5]{}, src_mod256[5]{}, dst_mod256[5]{};
  uint64_t write_attempts{}, fadd_attempts{}, other_attempts{};
  Timing post_write, post_fadd, post_other, poll, write_observed, fadd_observed;
  Entry entries[kMap]{};
};
struct alignas(128) Slot {
  std::atomic<bool> busy{false};
  unsigned epoch{};
  long tid{};
  Stats s{};
};
Slot slots[kThreads];
std::atomic<unsigned> slot_count{0}, active{0};
std::atomic<bool> too_many_threads{false};
unsigned sequence{};
uint64_t started{};
thread_local Slot* local_slot;
unsigned bucket(uint64_t x) { return x % 64 ? 4 : unsigned((x % 256) / 64); }
Slot* enter() {
  unsigned epoch = active.load();
  if (!epoch) return nullptr;
  if (!local_slot) {
    unsigned index = slot_count.fetch_add(1);
    if (index >= kThreads) { too_many_threads.store(true); return nullptr; }
    local_slot = &slots[index];
  }
  Slot* slot = local_slot;
  slot->busy.store(true);
  if (active.load() != epoch) { slot->busy.store(false); return nullptr; }
  if (slot->epoch != epoch) {
    // All fields are integer PODs. Avoid a large temporary on Proxy stacks.
    std::memset(&slot->s, 0, sizeof(slot->s));
    slot->tid = syscall(SYS_gettid);
    slot->epoch = epoch;
  }
  return slot;
}
void insert(Stats& s, uint64_t key, unsigned kind, uint64_t stamp) {
  if (key <= 1) { ++s.context_errors; return; }
  unsigned pos = unsigned((key >> 4) * 11400714819323198485ULL) & (kMap - 1);
  for (unsigned i = 0; i < kMap; ++i, pos = (pos + 1) & (kMap - 1)) {
    Entry& e = s.entries[pos];
    if (e.key == key) { ++s.context_errors; return; }
    if (e.key <= 1) {
      e = {key, stamp, kind};
      if (++s.inflight > s.peak_inflight) s.peak_inflight = s.inflight;
      return;
    }
  }
  ++s.map_overflow;
}
void complete(Stats& s, uint64_t key, uint64_t observed) {
  unsigned pos = unsigned((key >> 4) * 11400714819323198485ULL) & (kMap - 1);
  for (unsigned i = 0; i < kMap; ++i, pos = (pos + 1) & (kMap - 1)) {
    Entry& e = s.entries[pos];
    if (e.key == 0) break;
    if (key > 1 && e.key == key) {
      if (e.start) {
        if (e.kind == 0) s.write_observed.add(observed - e.start);
        if (e.kind == 1) s.fadd_observed.add(observed - e.start);
      }
      e.key = 1; --s.inflight; return;
    }
  }
  ++s.unknown_completions;
}
struct Registration { uint64_t va{}, len{}; unsigned access{}, gpu{}, ok{}; };
Registration registrations[128];
pthread_mutex_t reg_lock = PTHREAD_MUTEX_INITIALIZER;
unsigned reg_count{};
void array(FILE* f, const char* name, const uint64_t* values, unsigned n) {
  std::fprintf(f, ",\"%s\":[", name);
  for (unsigned i = 0; i < n; ++i) std::fprintf(f, "%s%llu", i ? "," : "", (unsigned long long)values[i]);
  std::fprintf(f, "]");
}
void timing(FILE* f, const char* name, const Timing& t) {
  std::fprintf(f, ",\"%s\":{\"samples\":%llu,\"total_ns\":%llu,\"max_ns\":%llu",
      name, (unsigned long long)t.samples, (unsigned long long)t.total_ns, (unsigned long long)t.max_ns);
  array(f, "log2_ns", t.log2_ns, 64); std::fprintf(f, "}");
}
} // namespace

extern "C" int uccl_trace_ready() {
  pthread_once(&once, resolve);
  return real_post && real_poll && real_register;
}
extern "C" const char* uccl_trace_real_path() { uccl_trace_ready(); return real_path; }
extern "C" int uccl_trace_begin() {
  if (!uccl_trace_ready() || active.load()) return -1;
  started = now(); active.store(++sequence); return 0;
}
extern "C" int uccl_trace_end(const char* path) {
  unsigned epoch = active.exchange(0);
  uint64_t ended = now();
  if (!epoch) return -1;
  unsigned count = slot_count.load();
  if (count > kThreads) count = kThreads;
  for (unsigned i = 0; i < count; ++i) {
    while (slots[i].busy.load()) {
      if (now() - ended > 5000000000ULL) return -2;
      sched_yield();
    }
  }
  FILE* f = std::fopen(path, "w");
  if (!f) return -3;
  std::fprintf(f, "{\"schema\":1,\"epoch\":%u,\"wall_ns\":%llu,\"thread_overflow\":%s,"
      "\"constants\":{\"relax\":%u,\"strong\":%u},\"registrations\":[",
      epoch, (unsigned long long)(ended-started), too_many_threads.load() ? "true" : "false",
      unsigned(URMA_RELAX_ORDER), unsigned(URMA_STRONG_ORDER));
  pthread_mutex_lock(&reg_lock);
  for (unsigned i = 0; i < reg_count; ++i) {
    const auto& r = registrations[i];
    std::fprintf(f, "%s{\"va\":%llu,\"len\":%llu,\"access\":%u,\"gpu\":%u,\"ok\":%u}",
        i ? "," : "", (unsigned long long)r.va, (unsigned long long)r.len, r.access, r.gpu, r.ok);
  }
  pthread_mutex_unlock(&reg_lock);
  std::fprintf(f, "],\"threads\":[");
  bool comma = false;
  for (unsigned i = 0; i < count; ++i) {
    if (slots[i].epoch != epoch) continue;
    const Stats& s = slots[i].s;
    std::fprintf(f, "%s{\"tid\":%ld", comma ? "," : "", slots[i].tid); comma = true;
#define FIELD(n) std::fprintf(f, ",\"" #n "\":%llu", (unsigned long long)s.n)
    FIELD(post_calls); FIELD(post_errors); FIELD(poll_calls); FIELD(empty_polls); FIELD(poll_errors);
    FIELD(completed); FIELD(completion_errors); FIELD(attempted_wrs); FIELD(posted_wrs);
    FIELD(write_wrs); FIELD(fadd_wrs); FIELD(other_wrs); FIELD(write_bytes); FIELD(batch_max);
    FIELD(complete_enabled); FIELD(fence_enabled); FIELD(inflight); FIELD(peak_inflight);
    FIELD(unknown_completions); FIELD(map_overflow); FIELD(context_errors);
#undef FIELD
    array(f,"write_order",s.write_order,16); array(f,"fadd_order",s.fadd_order,16);
    array(f,"length_mod256",s.length_mod256,5); array(f,"src_mod256",s.src_mod256,5); array(f,"dst_mod256",s.dst_mod256,5);
    timing(f,"post_write",s.post_write); timing(f,"post_fadd",s.post_fadd); timing(f,"post_other",s.post_other);
    timing(f,"poll",s.poll); timing(f,"write_observed",s.write_observed); timing(f,"fadd_observed",s.fadd_observed);
    std::fprintf(f, "}");
  }
  std::fprintf(f, "]}\n");
  return std::fclose(f) ? -4 : 0;
}

extern "C" urma_status_t urma_post_jetty_send_wr(urma_jetty_t* jetty, urma_jfs_wr_t* wr, urma_jfs_wr_t** bad) {
  pthread_once(&once, resolve);
  if (!real_post) { errno = ENOSYS; return URMA_EINVAL; }
  Slot* slot = enter();
  if (!slot) return real_post(jetty, wr, bad);
  Stats& s = slot->s; ++s.post_calls;
  unsigned kind = wr && wr->opcode == URMA_OPC_WRITE ? 0 : wr && wr->opcode == URMA_OPC_FADD ? 1 : 2;
  uint64_t& attempts = kind == 0 ? s.write_attempts : kind == 1 ? s.fadd_attempts : s.other_attempts;
  bool sample = (++attempts % 256) == 1;
  uint64_t stamp = sample ? now() : 0;
  auto rc = real_post(jetty, wr, bad);
  int saved = errno;
  if (sample) (kind == 0 ? s.post_write : kind == 1 ? s.post_fadd : s.post_other).add(now()-stamp);
  unsigned batch = 0;
  for (auto* p = wr; p && batch < kMap; p = p->next) {
    ++batch; ++s.attempted_wrs;
    if (rc != URMA_SUCCESS) continue; // Do not guess partial-acceptance semantics on errors.
    ++s.posted_wrs;
    s.complete_enabled += p->flag.bs.complete_enable ? 1 : 0;
    s.fence_enabled += p->flag.bs.fence ? 1 : 0;
    unsigned order = unsigned(p->flag.bs.place_order);
    unsigned op = 2;
    if (p->opcode == URMA_OPC_WRITE) {
      op = 0; ++s.write_wrs;
      if (order < 16) ++s.write_order[order];
      uint64_t bytes = 0;
      for (uint32_t j = 0; j < p->rw.src.num_sge; ++j) bytes += p->rw.src.sge[j].len;
      s.write_bytes += bytes; ++s.length_mod256[bucket(bytes)];
      if (p->rw.src.num_sge) ++s.src_mod256[bucket(p->rw.src.sge[0].addr)];
      if (p->rw.dst.num_sge) ++s.dst_mod256[bucket(p->rw.dst.sge[0].addr)];
    } else if (p->opcode == URMA_OPC_FADD) {
      op = 1; ++s.fadd_wrs;
      if (order < 16) ++s.fadd_order[order];
    } else ++s.other_wrs;
    if (p->flag.bs.complete_enable) insert(s, p->user_ctx, op, sample ? stamp : 0);
  }
  if (batch > s.batch_max) s.batch_max = batch;
  if (rc != URMA_SUCCESS) ++s.post_errors;
  slot->busy.store(false); errno = saved; return rc;
}

extern "C" int urma_poll_jfc(urma_jfc_t* jfc, int count, urma_cr_t* cr) {
  pthread_once(&once, resolve);
  if (!real_poll) { errno = ENOSYS; return -1; }
  Slot* slot = enter();
  if (!slot) return real_poll(jfc, count, cr);
  Stats& s = slot->s;
  bool sample = (++s.poll_calls % 4096) == 1;
  uint64_t before = sample ? now() : 0;
  int rc = real_poll(jfc, count, cr);
  int saved = errno;
  uint64_t observed = rc > 0 || sample ? now() : 0;
  if (sample) s.poll.add(observed-before);
  if (rc < 0) ++s.poll_errors;
  else if (!rc) ++s.empty_polls;
  else {
    s.completed += unsigned(rc);
    for (int i = 0; i < rc; ++i) {
      if (cr[i].status != URMA_CR_SUCCESS) ++s.completion_errors;
      complete(s, cr[i].user_ctx, observed);
    }
  }
  slot->busy.store(false); errno = saved; return rc;
}

extern "C" urma_target_seg_t* urma_register_seg(urma_context_t* ctx, urma_seg_cfg_t* cfg) {
  pthread_once(&once, resolve);
  if (!real_register) { errno = ENOSYS; return nullptr; }
  auto* result = real_register(ctx,cfg);
  int saved = errno;
  if (cfg) {
    pthread_mutex_lock(&reg_lock);
    if (reg_count < 128) registrations[reg_count++] = {cfg->va,cfg->len,unsigned(cfg->flag.bs.access),unsigned(cfg->is_gpu_seg),result ? 1U : 0U};
    pthread_mutex_unlock(&reg_lock);
  }
  errno = saved; return result;
}
