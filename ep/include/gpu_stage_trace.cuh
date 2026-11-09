#pragma once
#include <cstdint>
namespace uccl { namespace gpu_stages {
constexpr unsigned kStages=64;
struct Publication {uint64_t tick=0,slot=0;uint32_t block=0,thread=0;};
struct Metric {unsigned long long calls=0,cycles=0,max_cycles=0;};
struct alignas(128) State {volatile uint32_t enabled=0;Metric metrics[kStages][2]{};};
#if defined(__CUDACC__) || defined(__HIPCC__) || defined(__MCC__) || defined(__MXCC__) || defined(__MC_PLATFORM_MXCC__)
struct Span {
  State* state=nullptr;unsigned stage=0,lane=0;unsigned long long begin=0;
  __device__ Span(State* s,unsigned id):stage(id) {
#ifdef UCCL_GPU_STAGE_TRACE
#ifdef WARP_SIZE
    lane=threadIdx.x%WARP_SIZE;
#else
    lane=threadIdx.x%warpSize;
#endif
    if(s && s->enabled && (id>=40?lane==0:lane<2)){state=s;begin=clock64();}
#else
    (void)s;
#endif
  }
  __device__ void finish(){
#ifdef UCCL_GPU_STAGE_TRACE
    if(state){auto elapsed=clock64()-begin;auto* m=&state->metrics[stage][lane];atomicAdd(&m->calls,1ULL);atomicAdd(&m->cycles,elapsed);atomicMax(&m->max_cycles,elapsed);state=nullptr;}
#endif
  }
  __device__ ~Span(){finish();}
};
#endif
}
}
