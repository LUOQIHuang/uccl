// Sparse probes must erase even evaluation of detailed probe arguments.
#include <cassert>
#include <cstdint>
#include <memory>
#define __CUDACC__
#define __CUDA_ARCH__ 1
#define __device__
#define __constant__
#define __forceinline__ inline __attribute__((always_inline))
#define WARP_SIZE 32
#define UCCL_GPU_STAGE_TRACE
#define UCCL_GPU_STAGE_ROLES_ONLY
struct {unsigned x;} threadIdx,blockIdx;
uint64_t ticks=0;
unsigned reads=0,arguments=0;
unsigned long long clock64(){++reads;return ticks;}
#include "gpu_stage_trace.cuh"
namespace uccl { namespace gpu_stages {Control control;}}
uccl::gpu_stages::State* forbidden_argument(){++arguments;return nullptr;}
int main(){
  using namespace uccl::gpu_stages;
  UCCL_GPU_STAGE_DETAIL(excluded,forbidden_argument(),17);
  UCCL_GPU_STAGE_DISPATCH_SCOPE(excluded_flag,forbidden_argument());
  assert(arguments==0);
  {Span disabled(active_state(),46);ticks+=10;}
  assert(reads==0);
  std::unique_ptr<State> state(new State{});
  control={state.get(),~uint64_t{0},2,true};
  {Span role(active_state(),53);ticks+=25;}
  assert(reads==2 && state->metrics[0][0][0][53].cycles==25);
  assert(!state->dispatch_active[0][0][0]);
}
