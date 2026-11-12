#define __CUDACC__
#define UCCL_REFERENCE_EVENTS
#define WARP_SIZE 64
struct {unsigned x;} threadIdx,blockIdx;
unsigned long long ticks=0;
unsigned long long clock64(){return ++ticks;}
unsigned atomicAdd(unsigned* p,unsigned n){auto old=*p;*p+=n;return old;}
#include "../src/reference_event_trace.cu"
#include <cassert>
int main(){
  using namespace uccl::reference_events;
  static_assert(sizeof(Trace)==1769504,"C ABI size changed");
  assert(uccl_reference_events_version()==1);
  Trace* out=new Trace{};ClockStats clock{};
  assert(uccl_reference_events_end(out,&clock)==-1);
  fixture_host_error=7;assert(uccl_reference_events_begin(1)==7);fixture_host_error=0;
  fixture_mapping_error=8;assert(uccl_reference_events_begin(1)==8);fixture_mapping_error=0;
  assert(fixture_allocations==0);
  assert(uccl_reference_events_begin(9)==0 && fixture_allocations==1);
  assert(uccl_reference_events_begin(10)==-1);
  assert(uccl_reference_events_end(nullptr,&clock)==-1);
  running=false;updater.join(); // stable page during simulated device reads
  threadIdx.x=64;blockIdx.x=3;tick(1);
  assert(trace.count[1]==1 && trace.epoch[1][0]==9 && trace.warp_id[1][0]==1 && trace.block_id[1][0]==3);
  assert(trace.device_after[1][0]>=trace.device_before[1][0] && trace.ticks[1][0]>0);
  trace.count[2]=kCapacity;tick(2);assert(trace.dropped==1);
  assert(uccl_reference_events_end(out,&clock)==0 && fixture_allocations==0);
  assert(out->count[1]==1 && out->dropped==1 && !enabled && !host_clock);
  tick(0);assert(trace.count[0]==0);
  assert(uccl_reference_events_begin(11)==0);assert(uccl_reference_events_end(out,&clock)==0);
  assert(!out->dropped && !out->count[1] && fixture_allocations==0);
  BoundaryTrace* detail=new BoundaryTrace{};
  static_assert(sizeof(BoundaryTrace)==3539000,"boundary ABI size changed");
  assert(uccl_reference_boundaries_version()==1);
  assert(uccl_reference_boundaries_begin(12)==0);
  running=false;updater.join();
  tick(1);boundary(11);
  assert(boundary_trace.count[1]==1 && boundary_trace.count[11]==1);
  assert(uccl_reference_events_end(out,&clock)==-1); // wrong-size ABI rejected
  assert(uccl_reference_boundaries_end(detail,&clock)==0);
  assert(detail->count[11]==1 && detail->epoch[11][0]==12 && fixture_allocations==0);
  assert(uccl_reference_events_begin(13)==0);
  boundary(11);assert(trace.count[1]==0);
  assert(uccl_reference_boundaries_end(detail,&clock)==-1);
  assert(uccl_reference_events_end(out,&clock)==0);
  delete detail;
  delete out;
}
