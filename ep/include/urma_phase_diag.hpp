#pragma once
// Opt-in, process-local diagnostics. Counters are cumulative and atomic so
// Python snapshots never race a running Proxy. Timings are inclusive scopes;
// nested scopes, threads, and DMA overlap and MUST NOT be added as a timeline.
#include <array>
#include <atomic>
#include <chrono>
#include <cstdint>
#include <memory>
#include <mutex>
#include <sstream>
#include <thread>
#include <vector>

namespace uccl::urma_diag {
enum Stage { Collect, ProxyBatch, ProxyDispatch, ProxyCombine, GpuAck,
             CapacityWait, BarrierDrain, GroupPrepare, SdkPost, CqPoll,
             Write, Fadd, Quiet, Barrier, QueueDepth, EmptyPoll,
             SubmitToObservedCqe, WriteToObservedCqe, FaddToObservedCqe,
             PairCandidates, WriteCqeSuppressed, ObservedCqes, RetiredOps,
             RecoveryMarkers, PairedWriteToNotifyCqe, VerbsPostErrors,
             VerbsCompletionErrors, VerbsUnknown, VerbsReceives, VerbsMapOverflow, VerbsOther, Count };
inline char const* names[] = {"collect_ready_commands", "proxy_batch",
    "proxy_dispatch_batch", "proxy_combine_batch", "gpu_ack_update",
    "capacity_wait", "quiet_barrier_drain", "group_prepare", "sdk_post",
    "cq_poll_reclaim", "write_requests", "fadd_requests", "quiet_commands",
    "barrier_commands", "queue_depth_observation", "empty_cq_poll",
    "submit_to_observed_cqe_sampled", "write_to_observed_cqe_sampled",
    "fadd_to_observed_cqe_sampled", "paired_write_candidates",
    "write_cqe_suppressed", "observed_cqes", "retired_logical_ops",
    "pair_recovery_markers", "paired_write_to_notify_cqe_sampled", "verbs_post_errors",
    "verbs_completion_errors", "verbs_untracked_boundary_cqes", "verbs_receive_cqes", "verbs_tracking_overflow", "verbs_other_wrs"};
static_assert(sizeof(names)/sizeof(names[0])==Count,"diagnostic metric names mismatch");
struct Metric {
  std::atomic<uint64_t> calls{0}, ns{0}, items{0};
  std::array<std::atomic<uint64_t>,32> histogram{};
};
struct Record {
  size_t id;
  int rank, proxy;
  std::array<Metric,Count> metrics{};
  Record(size_t i,int r,int p):id(i),rank(r),proxy(p){}
};
inline std::atomic<bool> enabled{false};
inline std::mutex mutex;
inline std::vector<std::shared_ptr<Record>> records;
inline thread_local std::shared_ptr<Record> local;
inline thread_local int rank=-1, proxy=-1;
inline thread_local uint64_t sample_sequence[4]={};
inline void identity(int r,int p) { rank=r; proxy=p; }
inline uint64_t now() {
  return std::chrono::duration_cast<std::chrono::nanoseconds>(
      std::chrono::steady_clock::now().time_since_epoch()).count();
}
inline std::shared_ptr<Record> current() {
  if (!enabled.load(std::memory_order_relaxed)) return {};
  if (!local) {
    std::lock_guard<std::mutex> lock(mutex);
    local=std::make_shared<Record>(records.size(),rank,proxy);
    records.push_back(local);
  }
  return local;
}
inline void add(std::shared_ptr<Record> const& record,Stage stage,
                uint64_t ns=0,uint64_t items=0) {
  if (!record) return;
  auto& m=record->metrics[stage];
  m.ns.fetch_add(ns,std::memory_order_relaxed);
  m.items.fetch_add(items,std::memory_order_relaxed);
  m.calls.fetch_add(1,std::memory_order_relaxed);
}
inline void count(Stage stage,uint64_t items=1) { add(current(),stage,0,items); }
struct Scope {
  std::shared_ptr<Record> record;
  Stage stage;
  uint64_t start=0, items;
  explicit Scope(Stage s,uint64_t n=0,bool condition=true):stage(s),items(n) {
    if (condition) record=current();
    if (record) start=now();
  }
  ~Scope() { if (record) add(record,stage,now()-start,items); }
  Scope(Scope const&)=delete;
};
struct Sample {
  std::shared_ptr<Record> record;
  uint64_t start=0;
  Stage stage=SubmitToObservedCqe;
};
inline Sample sample(Stage stage=SubmitToObservedCqe) {
  unsigned index=stage==WriteToObservedCqe ? 1 : stage==FaddToObservedCqe ? 2 : stage==PairedWriteToNotifyCqe ? 3 : 0;
  if (!enabled.load(std::memory_order_relaxed) || (++sample_sequence[index] % 64)) return {};
  return {current(),now(),stage};
}
inline void complete(Sample& s) {
  if (s.record) {
    uint64_t ns=now()-s.start;
    unsigned bucket=0;
    for (uint64_t us=ns/1000; us>1 && bucket<31; us>>=1) ++bucket;
    if (s.stage!=PairedWriteToNotifyCqe) {
      add(s.record,SubmitToObservedCqe,ns,1);
      s.record->metrics[SubmitToObservedCqe].histogram[bucket].fetch_add(1,std::memory_order_relaxed);
    }
    if (s.stage!=SubmitToObservedCqe) {
      add(s.record,s.stage,ns,1);
      s.record->metrics[s.stage].histogram[bucket].fetch_add(1,std::memory_order_relaxed);
    }
  }
  s={};
}
inline std::string snapshot() {
  std::lock_guard<std::mutex> lock(mutex);
  std::ostringstream out;
  out << "{\"version\":1,\"records\":[";
  bool first=true;
  for (auto const& r:records) {
    if (!first) out << ',';
    first=false;
    out << "{\"id\":" << r->id << ",\"rank\":" << r->rank
        << ",\"proxy\":" << r->proxy << ",\"metrics\":{";
    for (size_t i=0;i<Count;++i) {
      if (i) out << ',';
      auto const& m=r->metrics[i];
      out << '"' << names[i] << "\":{\"calls\":" << m.calls.load()
          << ",\"ns\":" << m.ns.load() << ",\"items\":" << m.items.load()
          << ",\"histogram\":[";
      for (size_t j=0;j<m.histogram.size();++j) {
        if (j) out << ',';
        out << m.histogram[j].load();
      }
      out << "]}";
    }
    out << "}}";
  }
  out << "]}";
  return out.str();
}
}  // namespace uccl::urma_diag
