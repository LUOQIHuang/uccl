// Production FIFO implementation, simulated allocation/CUDA/NUMA boundaries.
#include "gpu_stage_trace.cuh"
#include <cassert>
#include <cerrno>
#include <cstdio>
#include <atomic>
#include <deque>
#include <vector>
#include <unordered_set>
#include "transport_timeline.hpp"
#include <cstdlib>
#include <cstring>
#include <memory>
#include <cstdint>
int allocations=0;
#define MPOL_PREFERRED 1
#define MSCCLPP_CUDATHROW(call) assert((call)==0)
int cudaGetDevice(int* d){*d=0;return 0;}
int set_mempolicy(int,unsigned long*,size_t){return 0;}
namespace mscclpp {
int getDeviceNumaNode(int){return -1;}
constexpr int memoryOrderAcquire=0,memoryOrderRelease=1;
template<class T>T atomicLoad(T* p,int){return *p;}
template<class T>void atomicStore(T* p,T v,int){*p=v;}
struct ProxyTrigger {uint64_t fst=0,snd=0;};
struct FifoDeviceHandle {ProxyTrigger* triggers;uint64_t *head,*tail,*tailCache;int size;uccl::gpu_stages::State* stageTrace=nullptr;uccl::gpu_stages::Publication* stagePublications=nullptr;};
namespace detail {
template<class T>struct Free {void operator()(T* p){if(p){--allocations;std::free(p);}}};
template<class T>using UniqueGpuHostPtr=std::unique_ptr<T,Free<T>>;
template<class T>using UniqueGpuPtr=UniqueGpuHostPtr<T>;
template<class T>auto gpuCallocHostUnique(size_t n=1){void* p=nullptr;assert(posix_memalign(&p,128,n*sizeof(T))==0);std::memset(p,0,n*sizeof(T));++allocations;return UniqueGpuHostPtr<T>(static_cast<T*>(p));}
template<class T>auto gpuCallocUnique(size_t n=1){return gpuCallocHostUnique<T>(n);}
}
class Fifo {struct Impl;std::unique_ptr<Impl> pimpl_;public:Fifo(int);~Fifo();ProxyTrigger poll();void pop();int size() const;FifoDeviceHandle deviceHandle() const;uccl::gpu_stages::State* gpuStageState() const;uccl::gpu_stages::Publication* gpuPublications() const;};
}
#include "fifo_implementation.inc"
#define USE_MSCCLPP_FIFO_BACKEND
struct Proxy {
  struct Handle {mscclpp::Fifo* fifo=nullptr;};
  struct {std::vector<Handle> d2h_queues;} cfg_;
  struct {int64_t quiet_wr=1,barrier_wr=-1;bool quiet_inflight=true,barrier_inflight=false;} ctx_;
  struct {void update_timer(){}} adaptive_sleeper_;
  std::vector<std::deque<std::pair<uint64_t,size_t>>> fifo_pending_;
  std::unordered_set<uint64_t> acked_wrs_;
  std::atomic<size_t> current_inflight_bytes{8};
  void notify_gpu_completion(uint64_t&);
};
#include "fifo_notify.inc"
int main(){
 {mscclpp::Fifo fifo(16);auto h=fifo.deviceHandle();assert(fifo.size()==16);
#ifdef UCCL_GPU_STAGE_TRACE
 assert(allocations==6 && fifo.gpuStageState() && fifo.gpuPublications());assert(h.stageTrace==fifo.gpuStageState() && h.stagePublications==fifo.gpuPublications());assert(!h.stageTrace->enabled);h.stageTrace->enabled=1;h.stagePublications[0]={42,0,2,3};assert(fifo.gpuPublications()[0].tick==42);
#else
 assert(allocations==4 && !h.stageTrace && !h.stagePublications);
#endif
 h.triggers[0]={123,456};auto t=fifo.poll();assert(t.fst==123 && t.snd==456);fifo.pop();assert(*h.tail==1 && !h.triggers[0].fst);
 Proxy p;p.cfg_.d2h_queues.push_back({&fifo});p.fifo_pending_.resize(1);p.fifo_pending_[0].push_back({0,8});p.fifo_pending_[0].push_back({1,0});p.acked_wrs_={0,1};
 uccl::urma_diag::identity(0,0);uccl::transport_timeline::begin();uint64_t tail=0;p.notify_gpu_completion(tail);auto trace=uccl::transport_timeline::end();
 assert(p.fifo_pending_[0].empty() && p.acked_wrs_.empty() && p.current_inflight_bytes==0 && !p.ctx_.quiet_inflight && *h.tail==2);
 size_t acks=0,pops=0;for(auto const& e:uccl::transport_timeline::events){if(std::strcmp(e.stage,"ack_slot")==0)++acks;if(std::strcmp(e.stage,"tail_published")==0)++pops;}assert(acks==2 && pops==1);
 }
 assert(allocations==0);
}
