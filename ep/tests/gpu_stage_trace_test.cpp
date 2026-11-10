#include <cassert>
#include <cstdint>
#include <memory>
#define __CUDACC__
#define __CUDA_ARCH__ 1
#define __device__
#define __constant__
#define WARP_SIZE 32
#define UCCL_GPU_STAGE_TRACE
struct {unsigned x;} threadIdx,blockIdx;
uint64_t ticks=0;
unsigned long long clock64(){return ticks;}
// Deliberately no atomicAdd/atomicMax declarations: v2 must not use them.
#include "gpu_stage_trace.cuh"
namespace uccl { namespace gpu_stages {Control control;}}
int main(){
  using namespace uccl::gpu_stages;std::unique_ptr<State> owner(new State{});auto* s=owner.get();
  assert(!active_state());control={s,~uint64_t{0},2};threadIdx.x=0;blockIdx.x=0;
  {Span outer(active_state(),1);ticks=10;{Span inner(active_state(),2);ticks=20;}ticks=40;}
  assert(s->metrics[0][0][0][1].cycles==40 && s->metrics[0][0][0][2].cycles==10);
  threadIdx.x=1;{Span lane(s,1);ticks+=5;lane.finish();lane.finish();}
  assert(s->metrics[0][0][1][1].calls==1);
  {Span op(s,40);ticks+=5;}assert(!s->metrics[0][0][1][40].calls);
  threadIdx.x=32;{Span other_warp(s,1);ticks+=7;}
  assert(s->metrics[0][1][0][1].cycles==7 && s->metrics[0][0][0][1].cycles==40);
  blockIdx.x=1;{Span other_block(s,1);ticks+=9;}assert(s->metrics[1][1][0][1].cycles==9);
  control.mode=1;{Span access(s,1);ticks+=99;}assert(s->metrics[1][1][0][1].calls==2 && s->metrics[1][1][0][1].cycles==9);
  control.mask=0;{Span excluded(s,1);ticks+=10;}assert(s->metrics[1][1][0][1].calls==2);
  control={};{Span bypass(active_state(),1);ticks+=5;}assert(!active_state());
  control={s,~uint64_t{0},2};blockIdx.x=kBlocks;{Span out(s,1);ticks+=5;}
  blockIdx.x=0;threadIdx.x=kWarps*WARP_SIZE;{Span out(s,1);ticks+=5;}
}
