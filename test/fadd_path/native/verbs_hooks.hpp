#pragma once
// Included only in the isolated diagnostic source tree.
#include "fadd_path_probe.hpp"
namespace uccl::fadd_path {
inline int verbs_post(ibv_qp* qp,ibv_send_wr* wr,ibv_send_wr** bad,int proxy){
  auto stamp=post_begin();
  for(auto* w=wr;w;w=w->next){
    bool write=w->opcode==IBV_WR_RDMA_WRITE;
    bool atomic=w->opcode==IBV_WR_ATOMIC_FETCH_AND_ADD;
    uint64_t bytes=0;if(write)for(int i=0;i<w->num_sge;++i)bytes+=w->sg_list[i].length;
    // Production verbs reuses scratch[t] across batches/QPs. Do not read it
    // as a per-WR return marker or alter its ownership/SGEs for this probe.
    issued(stamp,reinterpret_cast<uintptr_t>(qp->send_cq),qp->qp_num,w->wr_id,write,atomic,bytes,
           atomic ? w->wr.atomic.compare_add : 0,nullptr,(w->send_flags&IBV_SEND_FENCE)!=0,-1,proxy);
  }
  int rc=ibv_post_send(qp,wr,bad);post_end(stamp,rc);return rc;
}
inline int verbs_poll(ibv_cq* cq,int max,ibv_wc* wc){
  auto key=reinterpret_cast<uintptr_t>(cq);auto stamp=poll_begin(key);
  int n=ibv_poll_cq(cq,max,wc);auto t=poll_end(stamp,key,n);
  for(int i=0;i<n;++i)if(!(wc[i].opcode&IBV_WC_RECV))completed(stamp,key,wc[i].wr_id,t,wc[i].status);
  return n;
}
}
