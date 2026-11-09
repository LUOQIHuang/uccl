#include "urma_micro.hpp"
#ifdef USE_URMA
#include "common.hpp"
#include "gpu_p2p_runtime.hpp"
#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstring>
#include <exception>
#include <mutex>
#include <sstream>
#include <thread>
#include <pthread.h>
#include <sched.h>

std::pair<void*,std::shared_ptr<void>> allocate_urma_atomic_control(int);
namespace {
uint64_t micro_now() {
  return std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now().time_since_epoch()).count();
}
size_t aligned(size_t n){return (n+4095)/4096*4096;}
}
struct UrmaMicroSession::Impl {
  int device,rank=0;
  size_t threads,max_bytes,stride,region,total;
  void* main=nullptr;void* control=nullptr;void* observer=nullptr;
  std::shared_ptr<void> main_owner,control_owner;
  std::vector<ProxyCtx> contexts;
  std::vector<std::unique_ptr<UrmaTransport>> transports;
  UrmaMicroGeometry geometry;
  bool connected=false,prepared=false,armed=false,visible=false,with_fadd=false,closed=false;
  bool send=true,receive=true,scan=true;
  std::vector<int> cpus;
  size_t expected_sequence=0,armed_messages=0;
  uint64_t expected_counter=0;
  int notification_operation=-1;
  uintptr_t remote_main=0;
  size_t remote_dst_pad=0;
  std::mutex mutex;
  Impl(int d,size_t t,size_t m):device(d),threads(t),max_bytes(m),stride(aligned(m+4096)),
      region(aligned((t*8*4+1)*stride)),total(3*region),contexts(t){}
};
UrmaMicroSession::UrmaMicroSession(int device,size_t threads,size_t max_bytes) {
  if ((threads!=1 && threads!=4) || !max_bytes || max_bytes>2*1024*1024 || device<0)
    throw std::invalid_argument("invalid URMA micro allocation configuration");
  if(urma_normal_completion_from_env()!=UrmaNormalCompletion::All)
    throw std::invalid_argument("URMA micro requires normal_completion=all");
  auto p=std::make_unique<Impl>(device,threads,max_bytes);
  p->main=uccl::gpu_p2p::allocate_device(p->total,device);
  p->main_owner=UrmaTransport::acquire_buffer_ownership(p->main,false,device);
  auto control=allocate_urma_atomic_control(device);
  p->control=control.first;p->control_owner=std::move(control.second);
  for(size_t i=0;i<threads;++i) {
    auto& ctx=p->contexts[i];ctx.local_rank=device;ctx.thread_idx=int(i);
    auto transport=std::make_unique<UrmaTransport>();transport->configure(true,8);transport->init(ctx);
    transport->register_main_buffer(p->main,p->total,device);
    transport->register_atomic_buffer(p->control,kAtomicBufferSize,device);
    p->transports.push_back(std::move(transport));
  }
  impl_=std::move(p);
}
UrmaMicroSession::~UrmaMicroSession() {
  try{close();}catch(std::exception const& e){std::fprintf(stderr,"URMA micro teardown failed: %s\n",e.what());std::terminate();}
}
LocalConnInfo UrmaMicroSession::descriptor(size_t i) const {
  return impl_->transports.at(i)->local_info();
}
std::string UrmaMicroSession::metadata() const {
  return "{\"channels\":8,\"normal_completion\":\"all\",\"write_order\":\"relax\",\"atomic_order\":\"strong\",\"atomic_fence\":\"per_request\",\"control_memory\":\""+std::string(uccl::gpu_p2p::atomic_buffer_is_gpu_memory()?"gpu":"host_mapped")+"\",\"atomic_return_memory\":\"host\",\"production_transport\":true}";
}
void UrmaMicroSession::connect(std::vector<RemoteConnInfo> const& peer) {
  auto& p=*impl_;std::lock_guard<std::mutex> lock(p.mutex);
  if(p.closed || p.connected || peer.size()!=p.threads)throw std::invalid_argument("invalid micro peer descriptors");
  for(auto const& r:peer)if(r.addr!=peer[0].addr || r.len!=p.total)throw std::invalid_argument("micro peer buffer geometry mismatch");
  p.remote_main=peer[0].addr;
  for(size_t i=0;i<p.threads;++i)p.transports[i]->connect_peer(0,peer[i]);
  p.connected=true;
}
void UrmaMicroSession::prepare(size_t bytes,size_t src_mod,size_t dst_mod,size_t window,int rank) {
  auto& p=*impl_;std::lock_guard<std::mutex> lock(p.mutex);
  if(p.closed || !p.connected || p.armed || !bytes || bytes>p.max_bytes || src_mod>=4096 || dst_mod>=4096 ||
      src_mod%4 || dst_mod%4 || (window!=1 && window!=4) || (rank!=0 && rank!=1))
    throw std::invalid_argument("invalid micro geometry");
  if(p.observer){urma_micro_observer_finish(p.observer);p.observer=nullptr;}
  auto base=reinterpret_cast<uintptr_t>(p.main);
  p.geometry={bytes,p.stride,p.region,(src_mod+4096-base%4096)%4096,
      (dst_mod+4096-base%4096)%4096,uint32_t(p.threads),8,uint32_t(window),uint32_t(p.threads*8*window)};
  p.rank=rank;p.expected_sequence=0;p.expected_counter=0;p.notification_operation=-1;
  p.remote_dst_pad=(dst_mod+4096-p.remote_main%4096)%4096;
  uccl::gpu_p2p::zero_memory(p.control,kAtomicBufferSize,
      uccl::gpu_p2p::atomic_buffer_is_gpu_memory()?uccl::gpu_p2p::MemoryKind::Device:uccl::gpu_p2p::MemoryKind::HostMapped,p.device);
  urma_micro_gpu_prepare(p.main,p.geometry,rank);p.prepared=true;
}
void UrmaMicroSession::notification_mode(int operation) {
  auto& p=*impl_;std::lock_guard<std::mutex> lock(p.mutex);
  if(p.closed || p.armed || !p.prepared || p.threads!=4 || p.geometry.window!=1 || p.expected_sequence || operation<0 || operation>2)
    throw std::invalid_argument("invalid URMA notification mode");
  p.notification_operation=operation;
}
void UrmaMicroSession::arm(size_t sequence,bool visible,bool with_fadd,size_t messages) {
  auto& p=*impl_;std::lock_guard<std::mutex> lock(p.mutex);
  if(p.closed || !p.prepared || p.armed || sequence!=p.expected_sequence || messages<8 || messages>128 || messages%8 ||
      (visible && (!with_fadd || p.geometry.window!=1 || messages!=8)))
    throw std::invalid_argument("invalid micro step");
  if(p.observer){urma_micro_observer_finish(p.observer);p.observer=nullptr;}
  if(p.notification_operation>=0 && (messages!=8 || with_fadd!=(p.notification_operation!=0) || (visible && p.notification_operation!=2)))throw std::invalid_argument("notification operation/arm mismatch");
  p.visible=visible;p.with_fadd=with_fadd;p.armed_messages=messages;
  if(visible && p.receive)p.observer=urma_micro_observer_start(p.main,p.control,p.geometry,1-p.rank,int(sequence%2),p.expected_counter+1,p.scan);
  p.armed=true;
}
std::string UrmaMicroSession::step(size_t sequence,bool with_fadd,size_t messages) {
  auto& p=*impl_;std::lock_guard<std::mutex> lock(p.mutex);
  if(p.closed || !p.armed || sequence!=p.expected_sequence || with_fadd!=p.with_fadd || messages!=p.armed_messages)
    throw std::invalid_argument("micro step was not armed");
  struct Result {uint64_t begin=0,end=0,cq_end=0,cqes=0,retired=0,build=0,post=0,poll=0,observe=0;std::exception_ptr error;};
  std::vector<Result> result(p.threads);
  std::atomic<size_t> ready{0};std::atomic<bool> go{false},cancel{false};
  std::vector<std::thread> workers;
  auto worker=[&](size_t t){
    auto& r=result[t];
    try{
      if(!p.cpus.empty()) {
        cpu_set_t mask;CPU_ZERO(&mask);CPU_SET(p.cpus[t],&mask);
        if(pthread_setaffinity_np(pthread_self(),sizeof(mask),&mask))throw std::runtime_error("micro thread affinity failed");
        cpu_set_t actual;CPU_ZERO(&actual);
        if(pthread_getaffinity_np(pthread_self(),sizeof(actual),&actual) || CPU_COUNT(&actual)!=1 || !CPU_ISSET(p.cpus[t],&actual))
          throw std::runtime_error("micro thread affinity verification failed");
      }
      uccl::gpu_p2p::set_device(p.device);
    }catch(...){r.error=std::current_exception();cancel=true;}
    ++ready;while(!go.load(std::memory_order_acquire))std::this_thread::yield();
    if(cancel)return;
    try {
      uccl::urma_diag::identity(p.device,int(t));
      auto& transport=*p.transports[t];auto const& g=p.geometry;
      r.begin=micro_now();uint64_t deadline=r.begin+15000000000ULL;
      size_t sent=0;
      while(p.send && sent<messages) {
        uint64_t mark=micro_now();
        size_t n=std::min({size_t(g.lanes*g.window),messages-sent,transport.capacity()/(with_fadd && p.notification_operation!=1?2:1)});
        std::vector<UrmaWorkRequest> batch;batch.reserve(n*(with_fadd?2:1));
        if(p.notification_operation!=1)for(size_t j=0;j<n;++j) {
          size_t m=sent+j,lane=m%g.lanes,slot=t*g.lanes*g.window+lane*g.window+(m/g.lanes)%g.window;
          UrmaWorkRequest w;w.wr_id=(uint64_t(lane)<<32)|(2*m);
          w.local_offset=(sequence%2)*g.region+slot*g.stride+g.src_pad;
          w.remote_offset=2*g.region+slot*g.stride+p.remote_dst_pad;w.bytes=uint32_t(g.bytes);batch.push_back(w);
        }
        if(with_fadd)for(size_t j=0;j<n;++j) {
          size_t m=sent+j,lane=m%g.lanes;
          UrmaWorkRequest a;a.kind=UrmaWorkRequest::Kind::Atomic;a.wr_id=(uint64_t(lane)<<32)|(2*m+1);
          a.remote_offset=(t*g.lanes+lane)*8;a.value=1;batch.push_back(a);
        }
        r.build+=micro_now()-mark;mark=micro_now();transport.post_batch(0,batch);
        r.post+=micro_now()-mark;mark=micro_now();
        uint64_t done=0;
        while(done<batch.size()) {
          TransportCompletion out[64];int count=transport.poll(out,64);
          for(int i=0;i<count;++i){if(out[i].status)throw std::runtime_error("micro completion error");done+=out[i].retired_ops;}
          r.cqes+=count;
          if(micro_now()>deadline)throw std::runtime_error("micro completion timeout");
        }
        if(done!=batch.size())throw std::runtime_error("micro retirement count mismatch");
        r.poll+=micro_now()-mark;
        r.retired+=done;sent+=n;
      }
      r.cq_end=micro_now();auto mark=micro_now();
      if(p.visible && p.receive)while(!urma_micro_observer_done(p.observer,t*8,8)) {
        if(micro_now()>deadline)throw std::runtime_error("micro GPU observation timeout");
        std::this_thread::yield();
      }
      r.observe=micro_now()-mark;
      r.end=micro_now();
    }catch(...){r.error=std::current_exception();}
  };
  try{for(size_t i=0;i<p.threads;++i)workers.emplace_back(worker,i);}
  catch(...){cancel=true;go=true;for(auto& t:workers)t.join();throw;}
  while(ready.load()!=p.threads)std::this_thread::yield();
  go.store(true,std::memory_order_release);for(auto& t:workers)t.join();
  for(auto const& r:result)if(r.error)std::rethrow_exception(r.error);
  p.armed=false;++p.expected_sequence;if(with_fadd && p.receive)p.expected_counter+=messages/8;
  uint64_t begin=UINT64_MAX,end=0,cq_end=0,cqes=0,retired=0,build=0,post=0,poll=0,observe=0;
  for(auto const& r:result){begin=std::min(begin,r.begin);end=std::max(end,r.end);cq_end=std::max(cq_end,r.cq_end);cqes+=r.cqes;retired+=r.retired;
    build+=r.build;post+=r.post;poll+=r.poll;observe+=r.observe;}
  std::ostringstream out;out<<"{\"span_ns\":"<<end-begin<<",\"cqes\":"<<cqes<<",\"retired\":"<<retired
      <<",\"write_bytes\":"<<(p.send && p.notification_operation!=1?p.threads*messages*p.geometry.bytes:0)<<",\"build_ns_sum\":"<<build
      <<",\"cq_span_ns\":"<<cq_end-begin<<",\"post_ns_sum\":"<<post<<",\"poll_ns_sum\":"<<poll<<",\"observe_ns_sum\":"<<observe<<",\"threads\":[";
  for(size_t i=0;i<result.size();++i){if(i)out<<',';out<<result[i].end-result[i].begin;}
  out<<"]}";return out.str();
}
void UrmaMicroSession::validate(size_t sequence,bool with_fadd,size_t messages) {
  auto& p=*impl_;std::lock_guard<std::mutex> lock(p.mutex);
  if(p.closed || p.armed || sequence+1!=p.expected_sequence || messages!=p.armed_messages || with_fadd!=p.with_fadd)
    throw std::invalid_argument("invalid micro validation boundary");
  if(p.receive && p.notification_operation==1){
    std::vector<uint64_t> counters(p.threads*8);
    if(uccl::gpu_p2p::atomic_buffer_is_gpu_memory())uccl::gpu_p2p::copy_device_to_host(counters.data(),p.control,counters.size()*8,p.device);
    else std::memcpy(counters.data(),p.control,counters.size()*8);
    for(auto c:counters)if(c!=p.expected_counter)throw std::runtime_error("micro GPU atomic counter mismatch");
    return;
  }
  if(p.receive && !urma_micro_gpu_validate(p.main,p.control,p.geometry,1-p.rank,int(sequence%2),p.expected_counter))
    throw std::runtime_error("micro received payload/control validation failed");
}
void UrmaMicroSession::experiment(bool send,bool receive,bool scan,std::vector<int> const& cpus) {
  auto& p=*impl_;std::lock_guard<std::mutex> lock(p.mutex);
  if(p.closed || p.armed || (cpus.size()!=p.threads && !cpus.empty()))throw std::invalid_argument("invalid micro experiment");
  for(int cpu:cpus)if(cpu<0 || cpu>=CPU_SETSIZE)throw std::invalid_argument("micro CPU outside affinity mask range");
  p.send=send;p.receive=receive;p.scan=scan;p.cpus=cpus;
}
std::string UrmaMicroSession::local_scan(size_t sequence) {
  auto& p=*impl_;std::lock_guard<std::mutex> lock(p.mutex);
  if(p.closed || !p.prepared || p.armed || p.geometry.window!=1)throw std::invalid_argument("invalid local scan");
  return urma_micro_gpu_local_scan(p.main,p.geometry,p.rank,int(sequence%2));
}
void UrmaMicroSession::close() {
  if(!impl_)return;
  auto& p=*impl_;std::lock_guard<std::mutex> lock(p.mutex);
  if(p.closed)return;
  if(p.observer){urma_micro_observer_finish(p.observer);p.observer=nullptr;}
  for(auto& t:p.transports)t->destroy();
  p.transports.clear();
  p.control_owner.reset();p.main_owner.reset();p.closed=true;
}
#endif
