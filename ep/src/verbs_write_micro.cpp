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
ibv_mr* register_control_buffer_mr(ibv_pd*,void*,size_t,int);
namespace uccl::verbs_micro_detail {
uint64_t tick(){return std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now().time_since_epoch()).count();}
void ok(bool value,char const* message){if(!value)throw std::runtime_error(message);}
struct Descriptor {uint64_t addr=0,len=0,control=0;uint32_t control_rkey=0,rkey=0,qpn[8]{};uint16_t lid=0;uint8_t gid[16]{};uint32_t mtu=0;};
}
using namespace uccl::verbs_micro_detail;
struct VerbsWriteSession::Impl {
  struct LaneSet {ibv_pd* pd=nullptr;ibv_mr* mr=nullptr;ibv_cq* cq=nullptr;std::vector<ibv_qp*> qp;Descriptor local{},remote{};size_t inflight=0;};
  ibv_context* context=nullptr;ibv_pd* shared_pd=nullptr;ibv_mr* shared_mr=nullptr;ibv_mr* control_mr=nullptr;ibv_mr* returns_mr=nullptr;
  alignas(64) std::array<uint64_t,32> returns{};std::vector<LaneSet> sets;
  void* main=nullptr;void* control=nullptr;size_t threads,max_bytes,total;int rank=0,gid;
  UrmaMicroGeometry g{};std::vector<int> cpus;bool send=true,receive=true,connected=false,prepared=false,closed=false;size_t sequence=0;bool notifications=false,armed=false;int operation=0;void* observer=nullptr;
  Impl(size_t t,size_t b,int gi):sets(t),threads(t),max_bytes(b),gid(gi) {
    g.stride=(b+8191)/4096*4096;g.region=(t*8*4+1)*g.stride;total=3*g.region;
  }
  void cleanup() {
    if(closed)return;
    if(observer){urma_micro_observer_finish(observer);observer=nullptr;}
    for(auto& s:sets){ok(!s.inflight,"verbs micro teardown retains outstanding DMA");
      for(auto* q:s.qp)ok(ibv_destroy_qp(q)==0,"destroy QP failed");
      s.qp.clear();
      if(s.cq){ok(ibv_destroy_cq(s.cq)==0,"destroy CQ failed");s.cq=nullptr;}
    }
    if(returns_mr){ok(ibv_dereg_mr(returns_mr)==0,"deregister atomic return buffer failed");returns_mr=nullptr;}
    if(control_mr){ok(ibv_dereg_mr(control_mr)==0,"deregister control buffer failed");control_mr=nullptr;}
    if(shared_mr){ok(ibv_dereg_mr(shared_mr)==0,"deregister DMA-BUF failed");shared_mr=nullptr;}
    if(shared_pd){ok(ibv_dealloc_pd(shared_pd)==0,"deallocate PD failed");shared_pd=nullptr;}
    if(context){ok(ibv_close_device(context)==0,"close verbs device failed");context=nullptr;}
    if(main){uccl::gpu_p2p::release_memory(main,uccl::gpu_p2p::MemoryKind::Device,0);main=nullptr;}
    if(control){uccl::gpu_p2p::release_memory(control,uccl::gpu_p2p::MemoryKind::Device,0);control=nullptr;}
    closed=true;
  }
  ~Impl(){try{cleanup();}catch(std::exception const& e){std::fprintf(stderr,"VERBS_MICRO_TEARDOWN_ERROR %s\n",e.what());std::terminate();}}
};
VerbsWriteSession::VerbsWriteSession(std::string const& hca,int gid,size_t threads,size_t bytes,bool notifications) {
  ok((threads==1 || threads==4) && bytes>0 && bytes<=2*1024*1024 && gid>=0,"invalid verbs micro configuration");
  auto p=std::make_unique<Impl>(threads,bytes,gid);p->notifications=notifications;
  if(notifications)ok(threads==4 && uccl::gpu_p2p::atomic_buffer_is_gpu_memory(),"notification campaign requires four threads and a GPU atomic buffer");
  int count=0;auto** list=ibv_get_device_list(&count);ok(list,"verbs device enumeration failed");
  for(int i=0;i<count;++i)if(hca==ibv_get_device_name(list[i])){p->context=ibv_open_device(list[i]);break;}
  ibv_free_device_list(list);ok(p->context,"selected verbs HCA unavailable");
  ibv_port_attr port{};ok(ibv_query_port(p->context,1,&port)==0 && port.state==IBV_PORT_ACTIVE,"verbs port 1 is not active");
  ok(gid<port.gid_tbl_len,"GID index outside table");ibv_gid local_gid{};ok(ibv_query_gid(p->context,1,gid,&local_gid)==0,"query GID failed");
  p->main=uccl::gpu_p2p::allocate_device(p->total,0);p->control=notifications?uccl::gpu_p2p::allocate_atomic_buffer(4096,0):uccl::gpu_p2p::allocate_device(4096,0);
  uccl::gpu_p2p::prepare_main_buffer(p->main,p->total,0);
  p->shared_pd=ibv_alloc_pd(p->context);ok(p->shared_pd,"allocate PD failed");
  p->shared_mr=reg_mr_gpu_dmabuf(p->shared_pd,p->main,p->total,reinterpret_cast<uintptr_t>(p->main),IBV_ACCESS_LOCAL_WRITE|IBV_ACCESS_REMOTE_WRITE);
  ok(p->shared_mr,"strict DMA-BUF micro registration failed; no fallback");
  if(notifications){
    ibv_device_attr caps{};ok(ibv_query_device(p->context,&caps)==0 && caps.atomic_cap!=IBV_ATOMIC_NONE,"native atomic capability unavailable");
    uccl::gpu_p2p::prepare_atomic_buffer(p->control,4096,0);
    p->control_mr=register_control_buffer_mr(p->shared_pd,p->control,4096,IBV_ACCESS_LOCAL_WRITE|IBV_ACCESS_REMOTE_WRITE|IBV_ACCESS_REMOTE_READ|IBV_ACCESS_REMOTE_ATOMIC);
    ok(p->control_mr,"GPU control registration failed; no host fallback");
    p->returns_mr=ibv_reg_mr(p->shared_pd,p->returns.data(),sizeof(p->returns),IBV_ACCESS_LOCAL_WRITE);
    ok(p->returns_mr,"atomic return registration failed");
  }
  for(auto& s:p->sets){
    s.pd=p->shared_pd;s.mr=p->shared_mr;
    s.cq=ibv_create_cq(p->context,512,nullptr,nullptr,0);ok(s.cq,"create CQ failed");
    s.local.addr=reinterpret_cast<uintptr_t>(p->main);s.local.len=p->total;s.local.rkey=s.mr->rkey;s.local.lid=port.lid;
    if(notifications){s.local.control=reinterpret_cast<uintptr_t>(p->control);s.local.control_rkey=p->control_mr->rkey;}
    s.local.mtu=port.active_mtu;std::memcpy(s.local.gid,&local_gid,16);
    for(size_t lane=0;lane<8;++lane){ibv_qp_init_attr init{};init.send_cq=s.cq;init.recv_cq=s.cq;init.qp_type=IBV_QPT_RC;
      init.cap.max_send_wr=256;init.cap.max_recv_wr=1;init.cap.max_send_sge=1;init.cap.max_recv_sge=1;
      auto* q=ibv_create_qp(s.pd,&init);ok(q,"create RC QP failed");s.qp.push_back(q);s.local.qpn[lane]=q->qp_num;
      ibv_qp_attr a{};a.qp_state=IBV_QPS_INIT;a.port_num=1;a.pkey_index=0;a.qp_access_flags=IBV_ACCESS_LOCAL_WRITE|IBV_ACCESS_REMOTE_WRITE|(notifications?IBV_ACCESS_REMOTE_ATOMIC:0);
      ok(ibv_modify_qp(q,&a,IBV_QP_STATE|IBV_QP_PORT|IBV_QP_PKEY_INDEX|IBV_QP_ACCESS_FLAGS)==0,"QP INIT failed");
    }
  }
  impl_=std::move(p);
}
VerbsWriteSession::~VerbsWriteSession()=default;
std::string VerbsWriteSession::descriptor(size_t i) const {auto const& d=impl_->sets.at(i).local;return std::string(reinterpret_cast<char const*>(&d),sizeof(d));}
std::string VerbsWriteSession::metadata() const {
  auto const& p=*impl_;return "{\"gid_index\":"+std::to_string(p.gid)+",\"mtu_enum\":"+std::to_string(p.sets[0].local.mtu)+",\"port\":1,\"lanes_per_thread\":8,\"sl\":3,\"traffic_class\":104,\"registration\":\"dmabuf\",\"shared_pd_mr\":true,\"psn\":0,\"notifications\":"+std::string(p.notifications?"true":"false")+",\"control_memory\":\"gpu\",\"atomic_return_memory\":\"host\",\"fadd_send_fence\":false}";
}
void VerbsWriteSession::connect(std::vector<std::string> const& descriptors) {
  auto& p=*impl_;ok(!p.closed && !p.connected && descriptors.size()==p.threads,"invalid peer descriptors");
  for(size_t t=0;t<p.threads;++t){auto& s=p.sets[t];ok(descriptors[t].size()==sizeof(Descriptor),"peer descriptor ABI mismatch");
    std::memcpy(&s.remote,descriptors[t].data(),sizeof(Descriptor));ok(s.remote.len==p.total,"peer buffer size mismatch");
    if(p.notifications)ok(s.remote.control && s.remote.control_rkey && s.remote.control%8==0,"invalid remote control descriptor");
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
  p.g.threads=p.threads;p.g.lanes=8;p.g.window=p.notifications?1:4;p.g.slots=p.threads*8*p.g.window;p.rank=rank;p.sequence=0;
  uccl::gpu_p2p::zero_memory(p.control,4096,uccl::gpu_p2p::MemoryKind::Device,0);
  urma_micro_gpu_prepare(p.main,p.g,rank);p.prepared=true;
}
void VerbsWriteSession::experiment(bool send,bool receive,std::vector<int> const& cpus) {
  auto& p=*impl_;ok(cpus.size()==p.threads,"one CPU per worker required");for(int c:cpus)ok(c>=0 && c<CPU_SETSIZE,"CPU outside mask");p.send=send;p.receive=receive;p.cpus=cpus;
}
void VerbsWriteSession::arm(size_t sequence,int operation,bool visible) {
  auto& p=*impl_;ok(p.notifications && p.prepared && !p.armed && !p.closed && sequence==p.sequence && operation>=0 && operation<=2 && (!visible || operation==2),"invalid notification arm");
  p.operation=operation;p.armed=true;
  if(visible)p.observer=urma_micro_observer_start(p.main,p.control,p.g,1-p.rank,sequence%2,sequence+1,false);
}
std::string VerbsWriteSession::step(size_t sequence) {
  auto& p=*impl_;ok(!p.closed && p.prepared && sequence==p.sequence && p.cpus.size()==p.threads,"micro step state mismatch");
  ok(!p.notifications || (p.armed && p.send && p.receive),"notifications require armed bidirectional mode");
  struct Result {uint64_t start=0,end=0,cq_end=0,cqes=0,build=0,post=0,poll=0,observe=0;std::exception_ptr error;};std::vector<Result> result(p.threads);
  std::atomic<size_t> ready{0};std::atomic<bool> go{false},cancel{false};std::vector<std::thread> workers;
  auto work=[&](size_t t){auto& r=result[t];try{uccl::gpu_p2p::set_device(0);cpu_set_t mask;CPU_ZERO(&mask);CPU_SET(p.cpus[t],&mask);
      ok(pthread_setaffinity_np(pthread_self(),sizeof(mask),&mask)==0,"micro pin failed");cpu_set_t actual;CPU_ZERO(&actual);
      ok(pthread_getaffinity_np(pthread_self(),sizeof(actual),&actual)==0 && CPU_COUNT(&actual)==1 && CPU_ISSET(p.cpus[t],&actual),"micro affinity verification failed");
    }catch(...){r.error=std::current_exception();cancel=true;}
    ++ready;while(!go.load())std::this_thread::yield();if(cancel)return;
    try{auto& s=p.sets[t];auto const& g=p.g;r.start=tick();auto deadline=r.start+15000000000ULL;
      if(p.notifications){
        uint64_t mark=tick();std::array<ibv_sge,16> sg{};std::array<ibv_send_wr,16> wr{};
        bool writes=p.operation!=1,atomics=p.operation!=0;size_t expected=(writes?8:0)+(atomics?8:0);
        for(size_t lane=0;lane<8;++lane){size_t slot=t*8+lane;
          sg[lane].addr=reinterpret_cast<uintptr_t>(p.main)+(sequence%2)*g.region+slot*g.stride+g.src_pad;sg[lane].length=g.bytes;sg[lane].lkey=s.mr->lkey;
          wr[lane].wr_id=lane;wr[lane].sg_list=&sg[lane];wr[lane].num_sge=1;wr[lane].opcode=IBV_WR_RDMA_WRITE;wr[lane].send_flags=IBV_SEND_SIGNALED;
          wr[lane].wr.rdma.remote_addr=s.remote.addr+2*g.region+slot*g.stride+(g.dst_pad+reinterpret_cast<uintptr_t>(p.main)%4096+4096-s.remote.addr%4096)%4096;wr[lane].wr.rdma.rkey=s.remote.rkey;
          ok(sg[lane].addr%4096==(reinterpret_cast<uintptr_t>(p.main)+g.src_pad)%4096 && wr[lane].wr.rdma.remote_addr%4096==(reinterpret_cast<uintptr_t>(p.main)+g.dst_pad)%4096,"notification alignment mismatch");
          sg[lane+8].addr=reinterpret_cast<uintptr_t>(&p.returns[slot]);sg[lane+8].length=8;sg[lane+8].lkey=p.returns_mr->lkey;
          wr[lane+8].wr_id=lane+8;wr[lane+8].sg_list=&sg[lane+8];wr[lane+8].num_sge=1;wr[lane+8].opcode=IBV_WR_ATOMIC_FETCH_AND_ADD;wr[lane+8].send_flags=IBV_SEND_SIGNALED;
          wr[lane+8].wr.atomic.remote_addr=s.remote.control+slot*8;wr[lane+8].wr.atomic.rkey=s.remote.control_rkey;wr[lane+8].wr.atomic.compare_add=1;
        }
        r.build+=tick()-mark;mark=tick();
        // Production native Normal submits WRITE groups before atomic groups, on the same data QP.
        for(int kind=0;kind<2;++kind)if(kind==0?writes:atomics)for(size_t lane=0;lane<8;++lane){ibv_send_wr* bad=nullptr;++s.inflight;ok(ibv_post_send(s.qp[lane],&wr[lane+kind*8],&bad)==0,"notification post failed (retaining DMA)");}
        r.post+=tick()-mark;mark=tick();std::array<bool,16> seen{};
        while(r.cqes<expected){ibv_wc wc[32];int n=ibv_poll_cq(s.cq,32,wc);ok(n>=0,"notification poll failed");
          for(int i=0;i<n;++i){auto id=wc[i].wr_id;
            if(wc[i].status!=IBV_WC_SUCCESS){std::fprintf(stderr,"VERBS_NOTIFY_CQE_ERROR status=%d vendor=%u qp=%u wr_id=%llu\n",wc[i].status,wc[i].vendor_err,wc[i].qp_num,(unsigned long long)id);throw std::runtime_error("notification completion error");}
            ok(id<16 && !seen[id] && (id<8?writes:atomics) && wc[i].qp_num==s.qp[id%8]->qp_num && wc[i].opcode==(id<8?IBV_WC_RDMA_WRITE:IBV_WC_FETCH_ADD),"notification unexpected/duplicate CQE");seen[id]=true;ok(s.inflight>0,"notification inflight underflow");--s.inflight;++r.cqes;
          }
          ok(tick()<deadline,"notification CQ timeout");
        }
        ok(r.cqes==expected,"notification CQ count mismatch");r.poll+=tick()-mark;r.cq_end=tick();mark=tick();
        if(p.observer)while(!urma_micro_observer_done(p.observer,t*8,8)){ok(tick()<deadline,"GPU notification observer timeout");std::this_thread::yield();}
        r.observe=tick()-mark;
      }else if(p.send)for(size_t sent=0;sent<128/p.threads;sent+=32){
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
  ++p.sequence;p.armed=false;
  if(p.observer){urma_micro_observer_finish(p.observer);p.observer=nullptr;}
  uint64_t start=UINT64_MAX,end=0,cq_end=0,cqes=0,build=0,post=0,poll=0,observe=0;
  for(auto const& r:result){start=std::min(start,r.start);end=std::max(end,r.end);cq_end=std::max(cq_end,r.cq_end);observe+=r.observe;cqes+=r.cqes;build+=r.build;post+=r.post;poll+=r.poll;}
  std::ostringstream out;out<<"{\"span_ns\":"<<end-start<<",\"write_bytes\":"<<(p.send?(p.notifications?(p.operation==1?0:32):128)*p.g.bytes:0)<<",\"cqes\":"<<cqes<<",\"retired\":"<<cqes<<",\"build_ns_sum\":"<<build<<",\"post_ns_sum\":"<<post<<",\"poll_ns_sum\":"<<poll<<",\"observe_ns_sum\":"<<observe<<",\"cq_span_ns\":"<<(p.notifications?cq_end-start:end-start)<<'}';return out.str();
}
void VerbsWriteSession::validate(size_t sequence) {
  auto& p=*impl_;ok(p.sequence==sequence+1,"invalid validation sequence");
  uint64_t count=p.notifications && p.operation!=0?sequence+1:0;
  if(p.receive && (!p.notifications || p.operation!=1))ok(urma_micro_gpu_validate(p.main,p.control,p.g,1-p.rank,sequence%2,count),"received GPU payload/counter mismatch");
  if(p.receive && p.notifications){std::array<uint64_t,32> flags{};uccl::gpu_p2p::copy_device_to_host(flags.data(),p.control,sizeof(flags),0);for(auto f:flags)ok(f==count,"GPU atomic counter mismatch");}
}
void VerbsWriteSession::close(){impl_->cleanup();}
#endif
