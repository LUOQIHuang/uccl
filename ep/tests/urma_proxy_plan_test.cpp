// Compile the actual Proxy batching method with CPU recorders. The runner
// extracts only the production TransferCmd protocol and method body; no GPU.
#include "urma_mode.hpp"
#include "urma_phase_diag.hpp"
#include <algorithm>
#include <cassert>
#include <cstdint>
#include <stdexcept>
#include <unordered_set>
#include <vector>
#define __host__
#define __device__
#include "transfer_protocol.inc"

constexpr size_t kMaxOutstandingSends=16;
constexpr int kLargeAtomicValue=33550000, kMaxSendAtomicValue=16383;
constexpr uint64_t kUrmaInternalWrId=UINT64_MAX;
struct ProxyCtx { static constexpr size_t kMaxAtomicOps=16; };
struct UrmaTransport {
  std::vector<std::vector<UrmaWorkRequest>> batches;
  void post_batch(int,std::vector<UrmaWorkRequest> const& requests) {
    batches.push_back(requests);
  }
};
struct Proxy {
  struct { bool use_normal_mode; } cfg_;
  std::vector<std::vector<UrmaWorkRequest>> urma_batches_by_rank_{3};
  UrmaTransport transport;
  size_t urma_outstanding_ops_=0;
  std::unordered_set<uint64_t> acked_wrs_;
  std::vector<uint64_t> barriers;
  size_t drains=0;
  UrmaTransport* urma_transport_for_rank(int peer) {
    return peer>0 && peer<3 ? &transport : nullptr;
  }
  void ensure_urma_capacity(size_t count) {
    assert(count<=16);
    if (urma_outstanding_ops_>16-count) { urma_outstanding_ops_=0; ++drains; }
  }
  void send_barrier(uint64_t wr) { assert(!urma_outstanding_ops_); barriers.push_back(wr); }
  void post_urma_commands(std::vector<uint64_t> const&,std::vector<TransferCmd> const&);
};
#include "proxy_plan.inc"

int main() {
  for(bool normal : {true,false}) {
    Proxy p; p.cfg_.use_normal_mode=normal;
    TransferCmd write{},atomic{},quiet{},barrier{};
    write.cmd_type=make_cmd_type(CmdType::WRITE,false,false);
    write.dst_rank=1; write.req_lptr=3; write.req_rptr=5; write.bytes=64;
    atomic.cmd_type=make_cmd_type(CmdType::ATOMIC,true,false);
    atomic.dst_rank=1; atomic.req_rptr=8; atomic.value=7;
    quiet.cmd_type=CmdType::QUIET; barrier.cmd_type=CmdType::BARRIER;
    // Inputs intentionally interleave types; verbs stages writes before atoms.
    p.post_urma_commands({(2ULL<<32)|1,(2ULL<<32)|2,3,4},{atomic,write,quiet,barrier});
    assert(p.transport.batches.size()==1);
    auto const& batch=p.transport.batches[0];
    assert(batch.size()==2 && batch[0].kind==UrmaWorkRequest::Kind::Write);
    assert(batch[0].local_offset==(normal ? 12U : 48U));
    assert(batch[0].remote_offset==(normal ? 20U : 80U));
    assert(batch[1].value==(normal ? 7 : 1));
    assert(batch[1].fence && p.acked_wrs_.count(3) && p.barriers[0]==4);
    assert(p.drains==1);
  }
  {
    Proxy p; p.cfg_.use_normal_mode=true;
    TransferCmd write{}; write.cmd_type=CmdType::WRITE; write.dst_rank=1;
    write.bytes=8; write.atomic_offset=8; write.atomic_val=3;
    p.post_urma_commands({(2ULL<<32)|9},{write});
    auto const& batch=p.transport.batches[0];
    assert(batch.size()==2 && batch[0].wr_id==kUrmaInternalWrId && batch[0].ring==2);
    UrmaMode mode{true,8};
    assert(mode.data_lane(uint64_t(batch[0].ring)<<32)==mode.data_lane(batch[1].wr_id));
    assert(batch[1].value==3);
  }
  {
    Proxy p; p.cfg_.use_normal_mode=false;
    TransferCmd write{}; write.cmd_type=CmdType::WRITE; write.dst_rank=2; write.bytes=8;
    // LL expert/token metadata shares the Normal atomic fields. It must NOT
    // accidentally generate a fused normal-mode FADD.
    write.expert_idx=8; write.atomic_val=3;
    std::vector<TransferCmd> cmds(47,write);
    std::vector<uint64_t> ids(47);
    for(size_t i=0;i<ids.size();++i) ids[i]=i;
    p.post_urma_commands(ids,cmds);
    assert(p.transport.batches.size()==3);
    assert(p.transport.batches[0].size()==16 && p.transport.batches[2].size()==15);
    for(auto const& batch:p.transport.batches)
      for(auto const& wr:batch) assert(wr.kind==UrmaWorkRequest::Kind::Write);
  }
}
