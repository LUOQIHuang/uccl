#pragma once
// Event layout follows reference 940f7c7b; Normal only, no interval RAII state.
namespace uccl::reference_events {
constexpr unsigned kCapacity=8192;
struct alignas(8) Trace {
  unsigned count[6];
  unsigned dropped,reserved;
  unsigned long long ticks[6][kCapacity];
  unsigned long long device_before[6][kCapacity];
  unsigned long long device_after[6][kCapacity];
  unsigned block_id[6][kCapacity];
  unsigned epoch[6][kCapacity];
  unsigned warp_id[6][kCapacity];
};
static_assert(sizeof(Trace)==32+6*kCapacity*36,"reference trace ABI mismatch");
struct ClockStats {unsigned long long updates,max_gap_ns,gaps_over_1us,gaps_over_10us;int scheduler_error,reserved;};
static_assert(sizeof(ClockStats)==40,"reference clock ABI mismatch");
#if defined(UCCL_REFERENCE_EVENTS) && defined(__CUDACC__)
extern __device__ Trace trace;
extern __device__ int enabled;
extern __device__ unsigned epoch;
extern __device__ volatile unsigned long long* host_clock;
__device__ __forceinline__ void tick(unsigned event) {
  if(!enabled || !host_clock || event>=6)return;
  auto before=clock64();auto now=*host_clock;auto after=clock64();
  unsigned slot=atomicAdd(&trace.count[event],1u);
  if(slot<kCapacity){
    trace.ticks[event][slot]=now;trace.device_before[event][slot]=before;
    trace.device_after[event][slot]=after;trace.block_id[event][slot]=blockIdx.x;
    trace.epoch[event][slot]=epoch;
#ifdef WARP_SIZE
    trace.warp_id[event][slot]=threadIdx.x/WARP_SIZE;
#else
    trace.warp_id[event][slot]=threadIdx.x/warpSize;
#endif
  }else atomicAdd(&trace.dropped,1u);
}
#endif
}
extern "C" int uccl_reference_events_version();
extern "C" int uccl_reference_events_begin(unsigned epoch);
extern "C" int uccl_reference_events_end(void* trace,void* stats);
