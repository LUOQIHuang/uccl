#pragma once
// Legacy ABI follows reference 940f7c7b. Extended Normal boundaries use a
// separate storage/API; neither profile adds interval RAII accounting.
namespace uccl::reference_events {
constexpr unsigned kCapacity=8192;
template<unsigned Events> struct alignas(8) TraceStorage {
  unsigned count[Events];
  unsigned dropped,reserved;
  unsigned long long ticks[Events][kCapacity];
  unsigned long long device_before[Events][kCapacity];
  unsigned long long device_after[Events][kCapacity];
  unsigned block_id[Events][kCapacity];
  unsigned epoch[Events][kCapacity];
  unsigned warp_id[Events][kCapacity];
};
using Trace=TraceStorage<6>; // Preserve the existing six-stream C ABI.
using BoundaryTrace=TraceStorage<12>;
static_assert(sizeof(Trace)==32+6*kCapacity*36,"reference trace ABI mismatch");
static_assert(sizeof(BoundaryTrace)==56+12*kCapacity*36,"boundary trace ABI mismatch");
struct ClockStats {unsigned long long updates,max_gap_ns,gaps_over_1us,gaps_over_10us;int scheduler_error,reserved;};
static_assert(sizeof(ClockStats)==40,"reference clock ABI mismatch");
#if defined(UCCL_REFERENCE_EVENTS) && defined(__CUDACC__)
extern __device__ Trace trace;
extern __device__ BoundaryTrace boundary_trace;
extern __device__ int boundary_profile;
extern __device__ int enabled;
extern __device__ unsigned epoch;
extern __device__ volatile unsigned long long* host_clock;
template<unsigned Events> __device__ __forceinline__ void append(TraceStorage<Events>& storage,unsigned event) {
  if(event>=Events)return;
  auto before=clock64();auto now=*host_clock;auto after=clock64();
  unsigned slot=atomicAdd(&storage.count[event],1u);
  if(slot<kCapacity){
    storage.ticks[event][slot]=now;storage.device_before[event][slot]=before;
    storage.device_after[event][slot]=after;storage.block_id[event][slot]=blockIdx.x;
    storage.epoch[event][slot]=epoch;
#ifdef WARP_SIZE
    storage.warp_id[event][slot]=threadIdx.x/WARP_SIZE;
#else
    storage.warp_id[event][slot]=threadIdx.x/warpSize;
#endif
  }else atomicAdd(&storage.dropped,1u);
}
__device__ __forceinline__ bool boundaries_active(){return enabled && boundary_profile;}
__device__ __forceinline__ void tick(unsigned event) {
  if(!enabled || !host_clock)return;
  if(boundary_profile)append(boundary_trace,event);else append(trace,event);
}
__device__ __forceinline__ void boundary(unsigned event){if(boundaries_active())tick(event);}
#endif
}
extern "C" int uccl_reference_events_version();
extern "C" int uccl_reference_events_begin(unsigned epoch);
extern "C" int uccl_reference_events_end(void* trace,void* stats);
extern "C" int uccl_reference_boundaries_version();
extern "C" int uccl_reference_boundaries_begin(unsigned epoch);
extern "C" int uccl_reference_boundaries_end(void* trace,void* stats);
