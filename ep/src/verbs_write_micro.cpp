#include "verbs_write_micro.hpp"
#if defined(USE_DMABUF) && !defined(EFA)
#include "urma_micro.hpp"
#include "gpu_p2p_runtime.hpp"
#include <infiniband/verbs.h>
#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstring>
#include <cstdio>
#include <exception>
#include <stdexcept>
#include <sstream>
#include <thread>
#include <sched.h>
#include <pthread.h>
ibv_mr* reg_mr_gpu_dmabuf(ibv_pd*,void*,size_t,uint64_t,int);
namespace {
uint64_t tick(){return std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now().time_since_epoch()).count();}
void ok(bool value,char const* message){if(!value)throw std::runtime_error(message);}
struct Descriptor {uint64_t addr=0,len=0;uint32_t rkey=0,qpn[8]{};uint16_t lid=0;uint8_t gid[16]{};uint32_t mtu=0;};
}
struct VerbsWriteSession::Impl {
  struct LaneSet {ibv_pd* pd=nullptr;ibv_mr* mr=nullptr;ibv_cq* cq=nullptr;std::vector<ibv_qp*> qp;Descriptor local{},remote{};size_t inflight=0;};
  ibv_context* context=nullptr;ibv_pd* shared_pd=nullptr;ibv_mr* shared_mr=nullptr;std::vector<LaneSet> sets;
  void* main=nullptr;void* control=nullptr;size_t threads,max_bytes,total;int rank=0,gid;
  UrmaMicroGeometry g{};std::vector<int> cpus;bool send=true,receive=true,connected=false,prepared=false,closed=false;size_t sequence=0;
  Impl(size_t t,size_t b,int gi):sets(t),threads(t),max_bytes(b),gid(gi) {
    g.stride=(b+8191)/4096*4096;g.region=(t*8*4+1)*g.stride;total=3*g.region;
  }
  void cleanup() {
    if(closed)return;
    for(auto& s:sets){ok(!s.inflight,"verbs micro teardown retains outstanding DMA");
      for(auto* q:s.qp)ok(ibv_destroy_qp(q)==0,"destroy QP failed");
      s.qp.clear();
      if(s.cq){ok(ibv_destroy_cq(s.cq)==0,"destroy CQ failed");s.cq=nullptr;}
    }
    if(shared_mr){ok(ibv_dereg_mr(shared_mr)==0,"deregister DMA-BUF failed");shared_mr=nullptr;}
    if(shared_pd){ok(ibv_dealloc_pd(shared_pd)==0,"deallocate PD failed");shared_pd=nullptr;}
    if(context){ok(ibv_close_device(context)==0,"close verbs device failed");context=nullptr;}
    if(main){uccl::gpu_p2p::release_memory(main,uccl::gpu_p2p::MemoryKind::Device,0);main=nullptr;}
    if(control){uccl::gpu_p2p::release_memory(control,uccl::gpu_p2p::MemoryKind::Device,0);control=nullptr;}
    closed=true;
  }
  ~Impl(){try{cleanup();}catch(std::exception const& e){std::fprintf(stderr,"VERBS_MICRO_TEARDOWN_ERROR %s\n",e.what());std::terminate();}}
};
VerbsWriteSession::VerbsWriteSession(std::string const& hca,int gid,size_t threads,size_t bytes) {
  ok((threads==1 || threads==4) && bytes>0 && bytes<=2*1024*1024 && gid>=0,"invalid verbs micro configuration");
  auto p=std::make_unique<Impl>(threads,bytes,gid);int count=0;auto** list=ibv_get_device_list(&count);ok(list,"verbs device enumeration failed");
  for(int i=0;i<count;++i)if(hca==ibv_get_device_name(list[i])){p->context=ibv_open_device(list[i]);break;}
  ibv_free_device_list(list);ok(p->context,"selected verbs HCA unavailable");
  ibv_port_attr port{};ok(ibv_query_port(p->context,1,&port)==0 && port.state==IBV_PORT_ACTIVE,"verbs port 1 is not active");
  ok(gid<port.gid_tbl_len,"GID index outside table");ibv_gid local_gid{};ok(ibv_query_gid(p->context,1,gid,&local_gid)==0,"query GID failed");
  p->main=uccl::gpu_p2p::allocate_device(p->total,0);p->control=uccl::gpu_p2p::allocate_device(4096,0);
  uccl::gpu_p2p::prepare_main_buffer(p->main,p->total,0);
  p->shared_pd=ibv_alloc_pd(p->context);ok(p->shared_pd,"allocate PD failed");
  p->shared_mr=reg_mr_gpu_dmabuf(p->shared_pd,p->main,p->total,reinterpret_cast<uintptr_t>(p->main),IBV_ACCESS_LOCAL_WRITE|IBV_ACCESS_REMOTE_WRITE);
  ok(p->shared_mr,"strict DMA-BUF micro registration failed; no fallback");
  for(auto& s:p->sets){
    s.pd=p->shared_pd;s.mr=p->shared_mr;
    s.cq=ibv_create_cq(p->context,512,nullptr,nullptr,0);ok(s.cq,"create CQ failed");
    s.local.addr=reinterpret_cast<uintptr_t>(p->main);s.local.len=p->total;s.local.rkey=s.mr->rkey;s.local.lid=port.lid;
    s.local.mtu=port.active_mtu;std::memcpy(s.local.gid,&local_gid,16);
    for(size_t lane=0;lane<8;++lane){ibv_qp_init_attr init{};init.send_cq=s.cq;init.recv_cq=s.cq;init.qp_type=IBV_QPT_RC;
      init.cap.max_send_wr=256;init.cap.max_recv_wr=1;init.cap.max_send_sge=1;init.cap.max_recv_sge=1;
      auto* q=ibv_create_qp(s.pd,&init);ok(q,"create RC QP failed");s.qp.push_back(q);s.local.qpn[lane]=q->qp_num;
      ibv_qp_attr a{};a.qp_state=IBV_QPS_INIT;a.port_num=1;a.pkey_index=0;a.qp_access_flags=IBV_ACCESS_LOCAL_WRITE|IBV_ACCESS_REMOTE_WRITE;
      ok(ibv_modify_qp(q,&a,IBV_QP_STATE|IBV_QP_PORT|IBV_QP_PKEY_INDEX|IBV_QP_ACCESS_FLAGS)==0,"QP INIT failed");
    }
  }
  impl_=std::move(p);
}
VerbsWriteSession::~VerbsWriteSession()=default;
std::string VerbsWriteSession::descriptor(size_t i) const {auto const& d=impl_->sets.at(i).local;return std::string(reinterpret_cast<char const*>(&d),sizeof(d));}
std::string VerbsWriteSession::metadata() const {
  auto const& p=*impl_;return "{\"gid_index\":"+std::to_string(p.gid)+",\"mtu_enum\":"+std::to_string(p.sets[0].local.mtu)+",\"port\":1,\"lanes_per_thread\":8,\"sl\":3,\"traffic_class\":104,\"registration\":\"dmabuf\",\"shared_pd_mr\":true,\"psn\":0}";
}
void VerbsWriteSession::connect(std::vector<std::string> const& descriptors) {
  auto& p=*impl_;ok(!p.closed && !p.connected && descriptors.size()==p.threads,"invalid peer descriptors");
  for(size_t t=0;t<p.threads;++t){auto& s=p.sets[t];ok(descriptors[t].size()==sizeof(Descriptor),"peer descriptor ABI mismatch");
    std::memcpy(&s.remote,descriptors[t].data(),sizeof(Descriptor));ok(s.remote.len==p.total,"peer buffer size mismatch");
    for(size_t lane=0;lane<8;++lane){ibv_qp_attr a{};a.qp_state=IBV_QPS_RTR;a.path_mtu=static_cast<ibv_mtu>(std::min(s.local.mtu,s.remote.mtu));
      a.dest_qp_num=s.remote.qpn[lane];a.rq_psn=0;a.max_dest_rd_atomic=1;a.min_rnr_timer=12;
      a.ah_attr.is_global=1;a.ah_attr.port_num=1;a.ah_attr.sl=3;a.ah_attr.dlid=s.remote.lid;
      std::memcpy(&a.ah_attr.grh.dgid,s.remote.gid,16);a.ah_attr.grh.sgid_index=p.gid;a.ah_attr.grh.hop_limit=255;a.ah_attr.grh.traffic_class=104;
      ok(ibv_modify_qp(s.qp[lane],&a,IBV_QP_STATE|IBV_QP_AV|IBV_QP_PATH_MTU|IBV_QP_DEST_QPN|IBV_QP_RQ_PSN|IBV_QP_MAX_DEST_RD_ATOMIC|IBV_QP_MIN_RNR_TIMER)==0,"QP RTR failed");
      a={};a.qp_state=IBV_QPS_RTS;a.timeout=14;a.retry_cnt=7;a.rnr_retry=7;a.sq_psn=0;a.max_rd_atomic=1;
      ok(ibv_modify_qp(s.qp[lane],&a,IBV_QP_STATE|IBV_QP_TIMEOUT|IBV_QP_RETRY_CNT|IBV_QP_RNR_RETRY|IBV_QP_SQ_PSN|IBV_QP_MAX_QP_RD_ATOMIC)==0,"QP RTS failed");
    }
  }
  p.connected=true;
}
void VerbsWriteSession::prepare(size_t bytes,size_t src,size_t dst,int rank) {
  auto& p=*impl_;ok(!p.closed && p.connected && bytes && bytes<=p.max_bytes && src<4096 && dst<4096 && src%4==0 && dst%4==0 && (rank==0 || rank==1),"invalid verbs micro geometry");
  auto base=reinterpret_cast<uintptr_t>(p.main);p.g.bytes=bytes;p.g.src_pad=(src+4096-base%4096)%4096;p.g.dst_pad=(dst+4096-base%4096)%4096;
  p.g.threads=p.threads;p.g.lanes=8;p.g.window=4;p.g.slots=p.threads*32;p.rank=rank;p.sequence=0;
  uccl::gpu_p2p::zero_memory(p.control,4096,uccl::gpu_p2p::MemoryKind::Device,0);
  urma_micro_gpu_prepare(p.main,p.g,rank);p.prepared=true;
}
void VerbsWriteSession::experiment(bool send,bool receive,std::vector<int> const& cpus) {
  auto& p=*impl_;ok(cpus.size()==p.threads,"one CPU per worker required");for(int c:cpus)ok(c>=0 && c<CPU_SETSIZE,"CPU outside mask");p.send=send;p.receive=receive;p.cpus=cpus;
}
std::string VerbsWriteSession::step(size_t sequence) {
  auto& p=*impl_;ok(!p.closed && p.prepared && sequence==p.sequence && p.cpus.size()==p.threads,"micro step state mismatch");
  struct Result {uint64_t start=0,end=0,cqes=0,build=0,post=0,poll=0;std::exception_ptr error;};std::vector<Result> result(p.threads);
  std::atomic<size_t> ready{0};std::atomic<bool> go{false},cancel{false};std::vector<std::thread> workers;
  auto work=[&](size_t t){auto& r=result[t];try{uccl::gpu_p2p::set_device(0);cpu_set_t mask;CPU_ZERO(&mask);CPU_SET(p.cpus[t],&mask);
      ok(pthread_setaffinity_np(pthread_self(),sizeof(mask),&mask)==0,"micro pin failed");cpu_set_t actual;CPU_ZERO(&actual);
      ok(pthread_getaffinity_np(pthread_self(),sizeof(actual),&actual)==0 && CPU_COUNT(&actual)==1 && CPU_ISSET(p.cpus[t],&actual),"micro affinity verification failed");
    }catch(...){r.error=std::current_exception();cancel=true;}
    ++ready;while(!go.load())std::this_thread::yield();if(cancel)return;
    try{auto& s=p.sets[t];auto const& g=p.g;r.start=tick();auto deadline=r.start+15000000000ULL;
      if(p.send)for(size_t sent=0;sent<128/p.threads;sent+=32){
        uint64_t mark=tick();std::array<ibv_sge,32> sg{};std::array<ibv_send_wr,32> wr{};
        for(size_t lane=0;lane<8;++lane)for(size_t j=0;j<4;++j){size_t i=lane*4+j,slot=t*32+lane*4+((sent/8+j)%4);
          sg[i].addr=reinterpret_cast<uintptr_t>(p.main)+(sequence%2)*g.region+slot*g.stride+g.src_pad;sg[i].length=g.bytes;sg[i].lkey=s.mr->lkey;
          wr[i].wr_id=sent+i;wr[i].sg_list=&sg[i];wr[i].num_sge=1;wr[i].opcode=IBV_WR_RDMA_WRITE;wr[i].send_flags=IBV_SEND_SIGNALED;
          wr[i].wr.rdma.remote_addr=s.remote.addr+2*g.region+slot*g.stride+(g.dst_pad+reinterpret_cast<uintptr_t>(p.main)%4096+4096-s.remote.addr%4096)%4096;
          wr[i].wr.rdma.rkey=s.remote.rkey;wr[i].next=j==3?nullptr:&wr[i+1];
          ok(sg[i].addr%4096==(reinterpret_cast<uintptr_t>(p.main)+g.src_pad)%4096 && wr[i].wr.rdma.remote_addr%4096==(reinterpret_cast<uintptr_t>(p.main)+g.dst_pad)%4096,"actual micro alignment mismatch");
        }
        r.build+=tick()-mark;mark=tick();
        for(size_t lane=0;lane<8;++lane){ibv_send_wr* bad=nullptr;s.inflight+=4;ok(ibv_post_send(s.qp[lane],&wr[lane*4],&bad)==0,"verbs micro post failure (retaining DMA)");}
        r.post+=tick()-mark;mark=tick();size_t done=0;
        while(done<32){ibv_wc wc[64];int n=ibv_poll_cq(s.cq,64,wc);ok(n>=0,"verbs micro poll failed");
          for(int i=0;i<n;++i){if(wc[i].status!=IBV_WC_SUCCESS){std::fprintf(stderr,"VERBS_MICRO_CQE_ERROR status=%d vendor=%u qp=%u wr_id=%llu\n",wc[i].status,wc[i].vendor_err,wc[i].qp_num,(unsigned long long)wc[i].wr_id);throw std::runtime_error("verbs micro completion error");}ok(s.inflight>0,"unexpected micro CQE");--s.inflight;}
          done+=n;r.cqes+=n;ok(tick()<deadline,"verbs micro completion timeout");}
        ok(done==32,"micro CQE count mismatch");r.poll+=tick()-mark;
      }
      r.end=tick();
    }catch(...){r.error=std::current_exception();}
  };
  try{for(size_t i=0;i<p.threads;++i)workers.emplace_back(work,i);}catch(...){cancel=true;go=true;for(auto& w:workers)w.join();throw;}
  while(ready.load()!=p.threads)std::this_thread::yield();
  go=true;for(auto& w:workers)w.join();
  for(auto const& r:result)if(r.error)std::rethrow_exception(r.error);
  ++p.sequence;
  uint64_t start=UINT64_MAX,end=0,cqes=0,build=0,post=0,poll=0;
  for(auto const& r:result){start=std::min(start,r.start);end=std::max(end,r.end);cqes+=r.cqes;build+=r.build;post+=r.post;poll+=r.poll;}
  std::ostringstream out;out<<"{\"span_ns\":"<<end-start<<",\"write_bytes\":"<<(p.send?128*p.g.bytes:0)<<",\"cqes\":"<<cqes<<",\"retired\":"<<cqes<<",\"build_ns_sum\":"<<build<<",\"post_ns_sum\":"<<post<<",\"poll_ns_sum\":"<<poll<<'}';return out.str();
}
void VerbsWriteSession::validate(size_t sequence) {auto& p=*impl_;ok(p.sequence==sequence+1,"invalid validation sequence");if(p.receive)ok(urma_micro_gpu_validate(p.main,p.control,p.g,1-p.rank,sequence%2,0),"received GPU payload mismatch");}
void VerbsWriteSession::close(){impl_->cleanup();}
#endif
