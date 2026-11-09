#pragma once
// Source-level hooks: ibv_post_send/ibv_poll_cq can be inline provider calls.
#include <infiniband/verbs.h>
#include "urma_phase_diag.hpp"
#include "urma_layout_diag.hpp"
#include "verbs_timeline.hpp"
#include <map>
namespace uccl::verbs_diag {
inline std::atomic<uint64_t> epoch{0};
struct Pending {uccl::urma_diag::Sample sample;};
inline thread_local uint64_t local_epoch=0;
inline thread_local std::map<std::pair<uint32_t,uint64_t>,Pending> pending;
inline void refresh(){auto e=epoch.load();if(e!=local_epoch){pending.clear();local_epoch=e;}}
inline int post(ibv_qp* qp,ibv_send_wr* wr,ibv_send_wr** bad) {
  namespace d=uccl::urma_diag;
  if(!d::enabled.load(std::memory_order_relaxed))return ibv_post_send(qp,wr,bad);
  refresh();uint64_t count=0;
  for(auto* w=wr;w;w=w->next) {
    ++count;bool write=w->opcode==IBV_WR_RDMA_WRITE || w->opcode==IBV_WR_RDMA_WRITE_WITH_IMM;
    bool atomic=w->opcode==IBV_WR_ATOMIC_FETCH_AND_ADD || w->opcode==IBV_WR_ATOMIC_CMP_AND_SWP;
    if(write){uint64_t bytes=0;for(int i=0;i<w->num_sge;++i)bytes+=w->sg_list[i].length;
      d::count(d::Write,bytes);
      if(w->num_sge==1)uccl::urma_layout::record(w->sg_list[0].length,w->sg_list[0].addr,w->wr.rdma.remote_addr,qp->qp_num);
    }else d::count(atomic?d::Fadd:d::VerbsOther);
    if(w->send_flags&IBV_SEND_SIGNALED) {
      if(pending.size()>=32768){d::count(d::VerbsMapOverflow);continue;}
      auto key=std::make_pair(qp->qp_num,w->wr_id);
      if(pending.count(key))d::count(d::VerbsMapOverflow);
      pending[key]={d::sample(write?d::WriteToObservedCqe:atomic?d::FaddToObservedCqe:d::SubmitToObservedCqe)};
    }
  }
  int rc;
  for(auto* w=wr;w;w=w->next)verbs_timeline::record("post_begin",w->wr_id,qp->qp_num,w->opcode);
  {d::Scope timing(d::SdkPost,count);rc=ibv_post_send(qp,wr,bad);}
  for(auto* w=wr;w;w=w->next)verbs_timeline::record("post_end",w->wr_id,qp->qp_num,rc);
  if(rc)d::count(d::VerbsPostErrors);
  return rc;
}
inline int poll(ibv_cq* cq,int max,ibv_wc* wc) {
  namespace d=uccl::urma_diag;
  int n=ibv_poll_cq(cq,max,wc);
  if(!d::enabled.load(std::memory_order_relaxed))return n;
  refresh();if(n<0){d::count(d::VerbsCompletionErrors);return n;}
  if(!n)d::count(d::EmptyPoll);
  for(int i=0;i<n;++i){
    if(wc[i].status!=IBV_WC_SUCCESS)d::count(d::VerbsCompletionErrors);
    if(wc[i].opcode & IBV_WC_RECV){d::count(d::VerbsReceives);continue;}
    d::count(d::ObservedCqes);
    verbs_timeline::record("cqe",wc[i].wr_id,wc[i].qp_num,wc[i].status);
    auto it=pending.find({wc[i].qp_num,wc[i].wr_id});
    if(it==pending.end())d::count(d::VerbsUnknown);
    else {d::complete(it->second.sample);pending.erase(it);d::count(d::RetiredOps,1);}
  }
  return n;
}
inline void begin(){uccl::urma_diag::enabled=false;++epoch;uccl::urma_diag::enabled=true;}
inline void end(){uccl::urma_diag::enabled=false;}
}
