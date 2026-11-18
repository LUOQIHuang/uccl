void UrmaMicroSession::load_prepare(size_t limit,int rank,size_t count){
  auto& p=*impl_;
  uccl::serial_pair::require(p.serial && uccl::serial_pair::valid_load_shape(p.serial_lanes,limit,count),"invalid URMA load configuration");
  uccl::serial_pair::require(p.transports[0]->capacity()>=2*limit,"URMA capacity below requested load");
  prepare(459264,0,0,1,rank);
  p.geometry.window=count/p.serial_lanes;p.geometry.slots=count;p.load_limit=limit;
  urma_micro_gpu_prepare(p.main,p.geometry,rank);
}
std::string UrmaMicroSession::load_step(size_t seq,size_t limit,size_t count){
  auto& p=*impl_;std::lock_guard<std::mutex> lock(p.mutex);
  using namespace uccl::serial_pair;
  require(p.serial && p.prepared && !p.closed && !p.armed && seq==p.expected_sequence && p.geometry.slots==count && p.load_limit==limit && p.cpus.size()==1,"invalid URMA load state");
  verify_cpu(p.cpus[0]);uccl::gpu_p2p::set_device(p.device);
  if(!p.send){++p.expected_sequence;return "{\"version\":1,\"receiver_only\":true}";}
  auto result=load_measure(seq,p.serial_lanes,limit,count,[&](size_t first,size_t n,std::vector<Pair> const& pairs){
    std::vector<UrmaWorkRequest> batch;batch.reserve(n*2);
    // Match production staging: all ordinary WRITEs first, then standalone
    // notifying FADDs. UrmaTransport groups this list by data lane.
    for(size_t i=first;i<first+n;++i){UrmaWorkRequest w;w.wr_id=pairs[i].wid;w.bytes=459264;
      size_t slot=(i%p.serial_lanes)*p.geometry.window+i/p.serial_lanes;
      w.local_offset=(seq%2)*p.geometry.region+slot*p.geometry.stride+p.geometry.src_pad;
      w.remote_offset=2*p.geometry.region+slot*p.geometry.stride+p.remote_dst_pad;batch.push_back(w);}
    for(size_t i=first;i<first+n;++i){UrmaWorkRequest a;a.kind=UrmaWorkRequest::Kind::Atomic;a.wr_id=pairs[i].aid;
      a.remote_offset=(i%p.serial_lanes)*8;a.value=32;a.normal_role=NormalAtomicRole::Tail;a.fence=true;batch.push_back(a);}
    p.transports[0]->post_batch(0,batch);
  },[&](){TransportCompletion out[16];int n=p.transports[0]->poll(out,16);
    for(int i=0;i<n;++i)require(!out[i].status && out[i].retired_ops==1 && !out[i].has_secondary,"unexpected load completion policy");});
  ++p.expected_sequence;return result;
}
void UrmaMicroSession::load_validate(size_t seq){
  auto& p=*impl_;std::lock_guard<std::mutex> lock(p.mutex);
  uccl::serial_pair::require(p.serial && !p.closed && seq+1==p.expected_sequence && (p.geometry.slots==4 || p.geometry.slots==12),"invalid load validation");
  if(p.receive && !urma_micro_gpu_validate(p.main,p.control,p.geometry,1-p.rank,seq%2,uint64_t(seq+1)*p.geometry.window*32))
    throw std::runtime_error("load GPU payload/counter validation failed");
}
