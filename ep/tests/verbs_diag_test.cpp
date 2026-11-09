#include "verbs_stage_diag.hpp"
#include <cassert>
#include <deque>
#include <iostream>
std::deque<ibv_wc> completions;
int calls=0;
int post(ibv_qp* qp,ibv_send_wr* wr,ibv_send_wr**) {
  ++calls;for(auto* w=wr;w;w=w->next)if(w->send_flags&IBV_SEND_SIGNALED){ibv_wc c{};c.wr_id=w->wr_id;c.qp_num=qp->qp_num;c.opcode=w->opcode==IBV_WR_ATOMIC_FETCH_AND_ADD?IBV_WC_FETCH_ADD:IBV_WC_RDMA_WRITE;c.status=IBV_WC_SUCCESS;completions.push_back(c);}return 0;
}
int poll(ibv_cq*,int n,ibv_wc* out) {int count=0;while(!completions.empty() && count<n){out[count++]=completions.front();completions.pop_front();}return count;}
int main(){
  namespace v=uccl::verbs_diag;namespace d=uccl::urma_diag;
  ibv_context context{};context.ops.post_send=post;context.ops.poll_cq=poll;
  ibv_qp qp{};qp.context=&context;qp.qp_num=9;ibv_cq cq{};cq.context=&context;
  ibv_sge sge{};sge.addr=0x20000;sge.length=8192;
  ibv_send_wr w{},a{},*bad=nullptr;w.opcode=IBV_WR_RDMA_WRITE;w.wr_id=1;w.sg_list=&sge;w.num_sge=1;w.send_flags=IBV_SEND_SIGNALED;w.wr.rdma.remote_addr=0x10000;w.next=&a;
  a.opcode=IBV_WR_ATOMIC_FETCH_AND_ADD;a.wr_id=2;a.send_flags=IBV_SEND_SIGNALED;
  ibv_wc out[4];v::post(&qp,&w,&bad);assert(v::poll(&cq,4,out)==2 && d::records.empty());
  d::identity(0,2);uccl::urma_layout::begin();v::begin();uccl::verbs_timeline::begin();
  for(int i=0;i<128;++i){assert(v::post(&qp,&w,&bad)==0);assert(v::poll(&cq,4,out)==2);}
  assert(v::pending.empty());v::end();auto timeline=uccl::verbs_timeline::end();
  assert(timeline.find("post_begin")!=std::string::npos && timeline.find("post_end")!=std::string::npos && timeline.find("cqe")!=std::string::npos && timeline.find("\"dropped\":0")!=std::string::npos);
  auto r=d::records.at(0);
  assert(r->metrics[d::Write].calls==128 && r->metrics[d::Write].items==128*8192);
  assert(r->metrics[d::Fadd].calls==128 && r->metrics[d::ObservedCqes].calls==256);
  assert(r->metrics[d::WriteToObservedCqe].calls>0 && r->metrics[d::FaddToObservedCqe].calls>0);
  assert(uccl::urma_layout::end().find("\"count\":128")!=std::string::npos);
  v::begin();ibv_wc late{};late.qp_num=9;late.wr_id=999;late.opcode=IBV_WC_RDMA_WRITE;late.status=IBV_WC_WR_FLUSH_ERR;completions.push_back(late);
  assert(v::poll(&cq,4,out)==1 && out[0].status==IBV_WC_WR_FLUSH_ERR);
  assert(r->metrics[d::VerbsUnknown].calls==1 && r->metrics[d::VerbsCompletionErrors].calls==1);v::end();
  std::cout<<"Source-level verbs post/poll tracking tests passed (simulated provider)\n";
}
