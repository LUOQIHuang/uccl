#include <cuda_runtime.h>
#include "reference_event_trace.cuh"
#include <atomic>
#include <cerrno>
#include <memory>
#include <mutex>
#include <new>
#include <sched.h>
#include <thread>
#include <time.h>

namespace uccl::reference_events {
#ifdef UCCL_REFERENCE_EVENTS
__device__ Trace trace;
__device__ int enabled;
__device__ unsigned epoch;
__device__ volatile unsigned long long* host_clock;
namespace {
std::mutex mutex;
std::atomic<bool> running{false};
std::thread updater;
unsigned long long* page=nullptr;
void* mapped=nullptr;
ClockStats stats{};
unsigned long long now(){timespec ts{};clock_gettime(CLOCK_MONOTONIC_RAW,&ts);return ts.tv_sec*1000000000ULL+ts.tv_nsec;}
void update(){
  sched_param param{};
  if(sched_setscheduler(0,SCHED_IDLE,&param))stats.scheduler_error=errno;
  auto previous=__atomic_load_n(page,__ATOMIC_ACQUIRE);
  while(running.load(std::memory_order_relaxed)){
    auto t=now();__atomic_store_n(page,t,__ATOMIC_RELEASE);auto gap=t-previous;
    if(gap>stats.max_gap_ns)stats.max_gap_ns=gap;
    stats.gaps_over_1us+=gap>1000;stats.gaps_over_10us+=gap>10000;++stats.updates;previous=t;
  }
}
}
#endif
}
extern "C" __attribute__((visibility("default"))) int uccl_reference_events_version(){
#ifdef UCCL_REFERENCE_EVENTS
  return 1;
#else
  return 0;
#endif
}
extern "C" __attribute__((visibility("default"))) int uccl_reference_events_begin(unsigned value){
#ifdef UCCL_REFERENCE_EVENTS
  using namespace uccl::reference_events;std::lock_guard<std::mutex> lock(mutex);
  if(page || running.load())return -1;
  int rc=cudaDeviceSynchronize();if(rc)return rc;
  rc=cudaHostAlloc(reinterpret_cast<void**>(&page),4096,cudaHostAllocMapped);if(rc)return rc;
  rc=cudaHostGetDevicePointer(&mapped,page,0);
  std::unique_ptr<Trace> zero(new(std::nothrow) Trace{});
  if(!zero){cudaFreeHost(page);page=nullptr;mapped=nullptr;return -3;}
  if(!rc)rc=cudaMemcpyToSymbol(trace,zero.get(),sizeof(Trace));
  if(!rc)rc=cudaMemcpyToSymbol(epoch,&value,sizeof(value));
  __atomic_store_n(page,now(),__ATOMIC_RELEASE);stats={};
  if(!rc)rc=cudaMemcpyToSymbol(host_clock,&mapped,sizeof(mapped));
  if(!rc){running=true;try{updater=std::thread(update);}catch(...){running=false;rc=-2;}}
  if(!rc){int on=1;rc=cudaMemcpyToSymbol(enabled,&on,sizeof(on));}
  if(rc){
    running=false;if(updater.joinable())updater.join();
    void* off=nullptr;int disabled=0;
    int a=cudaMemcpyToSymbol(enabled,&disabled,sizeof(disabled));
    int b=cudaMemcpyToSymbol(host_clock,&off,sizeof(off));
    if(!a && !b){cudaFreeHost(page);page=nullptr;mapped=nullptr;}
  }
  return rc;
#else
  (void)value;return -1;
#endif
}
extern "C" __attribute__((visibility("default"))) int uccl_reference_events_end(void* output,void* clock_output){
#ifdef UCCL_REFERENCE_EVENTS
  using namespace uccl::reference_events;std::lock_guard<std::mutex> lock(mutex);
  if(!page || !output || !clock_output)return -1;
  int rc=cudaDeviceSynchronize();
  running=false;if(updater.joinable())updater.join();
  auto tail_gap=now()-__atomic_load_n(page,__ATOMIC_ACQUIRE);
  if(tail_gap>stats.max_gap_ns)stats.max_gap_ns=tail_gap;
  *static_cast<ClockStats*>(clock_output)=stats;
  // On CUDA failure retain the mapped allocation; never free an in-flight page.
  if(rc)return rc;
  rc=cudaMemcpyFromSymbol(output,trace,sizeof(Trace));
  int off=0;void* nil=nullptr;
  int a=cudaMemcpyToSymbol(enabled,&off,sizeof(off));
  int b=cudaMemcpyToSymbol(host_clock,&nil,sizeof(nil));
  if(!a && !b){int freed=cudaFreeHost(page);if(!rc)rc=freed;page=nullptr;mapped=nullptr;}
  return rc?rc:a?a:b;
#else
  (void)output;(void)clock_output;return -1;
#endif
}
