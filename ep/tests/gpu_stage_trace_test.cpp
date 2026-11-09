#include <cassert>
#include <cstdint>
#define __CUDACC__
#define __CUDA_ARCH__ 1
#define __device__
#define WARP_SIZE 32
#define UCCL_GPU_STAGE_TRACE
struct {unsigned x;} threadIdx;
uint64_t ticks=0;
unsigned long long clock64(){return ticks;}
void atomicAdd(unsigned long long* p,unsigned long long n){*p+=n;}
void atomicMax(unsigned long long* p,unsigned long long n){if(n>*p)*p=n;}
#include "gpu_stage_trace.cuh"
int main(){
  using namespace uccl::gpu_stages;State s{};s.enabled=1;threadIdx.x=0;
  {Span outer(&s,1);ticks=10;{Span inner(&s,2);ticks=20;}ticks=40;}
  assert(s.metrics[1][0].cycles==40 && s.metrics[2][0].cycles==10);
  assert(s.metrics[1][0].calls==1 && s.metrics[1][0].max_cycles==40);
  threadIdx.x=1;{Span lane(&s,1);ticks+=5;lane.finish();lane.finish();}
  assert(s.metrics[1][1].calls==1 && s.metrics[1][1].cycles==5);
  {Span op(&s,40);ticks+=5;}assert(!s.metrics[40][1].calls);
  threadIdx.x=2;{Span omitted(&s,1);ticks+=5;}assert(s.metrics[1][0].calls==1);
  s.enabled=0;threadIdx.x=0;{Span off(&s,1);ticks+=5;}assert(s.metrics[1][0].calls==1);
}
