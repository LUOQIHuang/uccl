#pragma once
#include <cstdint>
namespace uccl { namespace gpu_stages {
constexpr unsigned kStages=64, kBlocks=128, kWarps=32;
struct Metric {unsigned long long calls=0,cycles=0,max_cycles=0;};
// A slot has exactly one writer: (CTA, warp, sampled lane). Normal kernels
// must be ordered on one stream while the diagnostic window is active.
struct State {
  Metric metrics[kBlocks][kWarps][2][kStages]{};
  // Per-thread marker excludes notify/cached_notify operator calls from the
  // focused Dispatch window. The same thread sets/clears its private flag.
  unsigned char dispatch_active[kBlocks][kWarps][2]{};
};
struct Control {State* state=nullptr;uint64_t mask=0;unsigned mode=0;bool dispatch_focus=false;};
#if defined(__CUDACC__) || defined(__HIPCC__) || defined(__MCC__) || defined(__MXCC__) || defined(__MC_PLATFORM_MXCC__)
#if defined(UCCL_GPU_STAGE_TRACE) && (defined(__CUDACC__) || defined(__HIPCC__))
extern __device__ __constant__ Control control;
#endif
__device__ inline State* active_state(){
#if defined(UCCL_GPU_STAGE_TRACE) && (defined(__CUDA_ARCH__) || defined(__HIP_DEVICE_COMPILE__))
  return control.state;
#else
  return nullptr;
#endif
}
struct Span {
  Metric* metric=nullptr;unsigned mode=0;unsigned long long begin=0;
  __device__ Span(State* s,unsigned id) {
#if defined(UCCL_GPU_STAGE_TRACE) && (defined(__CUDA_ARCH__) || defined(__HIP_DEVICE_COMPILE__))
#ifdef WARP_SIZE
    unsigned width=WARP_SIZE;
#else
    unsigned width=warpSize;
#endif
    unsigned lane=threadIdx.x%width,warp=threadIdx.x/width;
    if(!s || id>=kStages || !(control.mask&(uint64_t{1}<<id)) ||
       blockIdx.x>=kBlocks || warp>=kWarps || ((id>=40 && id<50 && !control.dispatch_focus)?lane!=0:lane>=2))return;
    if(control.dispatch_focus && id>=40 && id<=42 && !s->dispatch_active[blockIdx.x][warp][lane])return;
    metric=&s->metrics[blockIdx.x][warp][lane][id];mode=control.mode;
    if(mode==2)begin=clock64();
#else
    (void)s;(void)id;
#endif
  }
  __device__ inline void finish(){
#if defined(UCCL_GPU_STAGE_TRACE) && (defined(__CUDA_ARCH__) || defined(__HIP_DEVICE_COMPILE__))
    if(metric){
      // Private slot, no GPU atomics and no mapped-host-memory access.
      auto elapsed=mode==2?clock64()-begin:0;
      ++metric->calls;metric->cycles+=elapsed;
      if(elapsed>metric->max_cycles)metric->max_cycles=elapsed;
      metric=nullptr;
    }
#endif
  }
  __device__ ~Span(){finish();}
};
struct DispatchScope {
  unsigned char* flag=nullptr;
  __device__ DispatchScope(State* s) {
#if defined(UCCL_GPU_STAGE_TRACE) && (defined(__CUDA_ARCH__) || defined(__HIP_DEVICE_COMPILE__))
#ifdef WARP_SIZE
    unsigned width=WARP_SIZE;
#else
    unsigned width=warpSize;
#endif
    unsigned lane=threadIdx.x%width,warp=threadIdx.x/width;
    if(!s || !control.dispatch_focus || blockIdx.x>=kBlocks || warp>=kWarps || lane>=2)return;
    flag=&s->dispatch_active[blockIdx.x][warp][lane];*flag=1;
#else
    (void)s;
#endif
  }
  __device__ ~DispatchScope(){
#if defined(UCCL_GPU_STAGE_TRACE) && (defined(__CUDA_ARCH__) || defined(__HIP_DEVICE_COMPILE__))
    if(flag)*flag=0;
#endif
  }
};
#endif
} }
