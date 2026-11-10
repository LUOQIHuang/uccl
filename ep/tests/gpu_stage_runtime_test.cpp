#define __CUDACC__
#define UCCL_GPU_STAGE_TRACE
#include "../src/gpu_stage_trace.cu"
#include <cassert>
#include <functional>
bool rejects(std::function<void()> f){try{f();return false;}catch(std::runtime_error const&){return true;}}
int main(){
  using namespace uccl::gpu_stages;
  assert(rejects([]{end();}) && rejects([]{begin("invalid");}));
  begin("bypass");assert(!control.state && fixture_allocations==0);
  assert(rejects([]{begin("bypass");}));
  validate_launch(7,48,1,1,512,1,1);
  assert(rejects([]{validate_launch(8,48,1,1,512,1,1);}));
  bool other_thread_rejected=false;
  std::thread other([&]{other_thread_rejected=rejects([]{validate_launch(7,48,1,1,512,1,1);});});other.join();assert(other_thread_rejected);
  assert(rejects([]{validate_launch(7,129,1,1,512,1,1);}));
  fixture_device=1;assert(rejects([]{end();}));fixture_device=0;
  assert(end().find("\"records\":[]")!=std::string::npos);
  begin("access");assert(control.state && control.mode==1 && fixture_allocations==1);
  control.state->metrics[1][2][0][41]={3,0,0};
  auto result=end();assert(result.find("\"version\":2")!=std::string::npos && result.find("\"calls\":3")!=std::string::npos && fixture_allocations==0 && !control.state);
  begin("clock");assert(control.mode==2 && control.mask==(uint64_t{1}<<46));end();
  begin("operators");assert((control.mask&(uint64_t{1}<<41)) && !(control.mask&1));end();
  begin("dispatch_access");assert(control.mode==1 && (control.mask&(uint64_t{1}<<53)) && !(control.mask&(uint64_t{1}<<45)));end();
  begin("dispatch_clock");assert(control.mode==2 && control.mask==(uint64_t{1}<<46));end();
  begin("dispatch");assert((control.mask&(uint64_t{1}<<12)) && (control.mask&(uint64_t{1}<<60)) && !(control.mask&(uint64_t{1}<<48)));end();
  assert(fixture_allocations==0);
}
