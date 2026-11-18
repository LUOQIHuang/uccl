void VerbsWriteSession::serial_prepare(size_t pairs,int rank){
  auto& p=*impl_;ok(p.serial && (pairs==1 || pairs==2),"not a serial verbs session");
  prepare(459264,0,0,rank);p.g.window=pairs;p.g.slots=pairs;
  urma_micro_gpu_prepare(p.main,p.g,rank);
}
std::string VerbsWriteSession::serial_step(size_t seq,size_t pairs){
  auto& p=*impl_;using namespace uccl::serial_pair;
  require(p.serial && p.prepared && !p.closed && seq==p.sequence && pairs==p.g.slots && p.cpus.size()==1,"invalid verbs serial state");
  verify_cpu(p.cpus[0]);uccl::gpu_p2p::set_device(0);
  if(!p.send){++p.sequence;return "{\"version\":1,\"receiver_only\":true}";}
  auto& s=p.sets[0];
  auto result=measure(seq,pairs,[&](size_t slot,uint64_t wid,uint64_t aid){
    ibv_sge sg[2]{};ibv_send_wr wr[2]{};
    sg[0].addr=reinterpret_cast<uintptr_t>(p.main)+(seq%2)*p.g.region+slot*p.g.stride+p.g.src_pad;
    sg[0].length=459264;sg[0].lkey=s.mr->lkey;
    wr[0].wr_id=wid;wr[0].opcode=IBV_WR_RDMA_WRITE;wr[0].send_flags=IBV_SEND_SIGNALED;wr[0].sg_list=&sg[0];wr[0].num_sge=1;
    wr[0].wr.rdma.remote_addr=s.remote.addr+2*p.g.region+slot*p.g.stride+(p.g.dst_pad+reinterpret_cast<uintptr_t>(p.main)%4096+4096-s.remote.addr%4096)%4096;
    wr[0].wr.rdma.rkey=s.remote.rkey;
    sg[1].addr=reinterpret_cast<uintptr_t>(&p.returns[0]);sg[1].length=8;sg[1].lkey=p.returns_mr->lkey;
    wr[1].wr_id=aid;wr[1].opcode=IBV_WR_ATOMIC_FETCH_AND_ADD;wr[1].send_flags=IBV_SEND_SIGNALED;wr[1].sg_list=&sg[1];wr[1].num_sge=1;
    wr[1].wr.atomic.remote_addr=s.remote.control;wr[1].wr.atomic.rkey=s.remote.control_rkey;wr[1].wr.atomic.compare_add=32;
    for(size_t i=0;i<2;++i){ibv_send_wr* bad=nullptr;auto t=post_tick();posted(wr[i].wr_id,i==0,i==1,t);
      ++s.inflight;int rc=ibv_post_send(s.qp[0],&wr[i],&bad);post_end(t,rc);}
  },[&](){ibv_wc wc[16]{};auto q=poll_begin();int n=ibv_poll_cq(s.cq,16,wc);auto t=poll_return(q);
    require(n>=0,"serial verbs poll failed");
    for(int i=0;i<n;++i){require(s.inflight>0,"serial inflight underflow");--s.inflight;
      completed(wc[i].wr_id,t,q,wc[i].status==IBV_WC_SUCCESS?0:int(wc[i].status));}
  });
  ++p.sequence;return result;
}
void VerbsWriteSession::serial_validate(size_t seq,size_t pairs){
  auto& p=*impl_;ok(p.serial && seq+1==p.sequence && pairs==p.g.slots,"invalid serial validation");
  if(p.receive)ok(urma_micro_gpu_validate(p.main,p.control,p.g,1-p.rank,seq%2,uint64_t(seq+1)*pairs*32),"serial GPU payload/counter validation failed");
}
