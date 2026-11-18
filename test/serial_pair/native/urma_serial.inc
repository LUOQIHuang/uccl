void UrmaMicroSession::serial_prepare(size_t pairs,int rank){
  auto& p=*impl_;
  uccl::serial_pair::require(p.serial && (pairs==1 || pairs==2),"not a serial URMA session");
  prepare(459264,0,0,1,rank);
  p.geometry.window=pairs;p.geometry.slots=pairs;
  urma_micro_gpu_prepare(p.main,p.geometry,rank);
}
std::string UrmaMicroSession::serial_step(size_t seq,size_t pairs){
  auto& p=*impl_;std::lock_guard<std::mutex> lock(p.mutex);
  using namespace uccl::serial_pair;
  require(p.serial && p.prepared && !p.closed && !p.armed && seq==p.expected_sequence && pairs==p.geometry.slots && p.cpus.size()==1,"invalid URMA serial state");
  verify_cpu(p.cpus[0]);uccl::gpu_p2p::set_device(p.device);
  if(!p.send){++p.expected_sequence;return "{\"version\":1,\"receiver_only\":true}";}
  auto result=measure(seq,pairs,[&](size_t slot,uint64_t wid,uint64_t aid){
    UrmaWorkRequest w;w.wr_id=wid;w.bytes=459264;
    w.local_offset=(seq%2)*p.geometry.region+slot*p.geometry.stride+p.geometry.src_pad;
    w.remote_offset=2*p.geometry.region+slot*p.geometry.stride+p.remote_dst_pad;
    UrmaWorkRequest a;a.kind=UrmaWorkRequest::Kind::Atomic;a.wr_id=aid;a.remote_offset=0;a.value=32;
    a.normal_role=NormalAtomicRole::Tail;a.fence=true;
    p.transports[0]->post_batch(0,{w,a});
  },[&](){TransportCompletion out[16];int n=p.transports[0]->poll(out,16);
    for(int i=0;i<n;++i)require(!out[i].status && out[i].retired_ops==1 && !out[i].has_secondary,"unexpected serial retirement policy");});
  ++p.expected_sequence;return result;
}
void UrmaMicroSession::serial_validate(size_t seq,size_t pairs){
  auto& p=*impl_;std::lock_guard<std::mutex> lock(p.mutex);
  uccl::serial_pair::require(p.serial && seq+1==p.expected_sequence && pairs==p.geometry.slots,"invalid serial validation");
  if(p.receive && !urma_micro_gpu_validate(p.main,p.control,p.geometry,1-p.rank,seq%2,uint64_t(seq+1)*pairs*32))
    throw std::runtime_error("serial GPU payload/counter validation failed");
}
