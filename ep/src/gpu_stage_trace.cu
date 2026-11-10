#include <cuda_runtime.h>
#include "gpu_stage_trace.cuh"
#include "gpu_stage_trace_host.hpp"
#include <mutex>
#include <sstream>
#include <stdexcept>
#include <thread>
#include <vector>

namespace uccl { namespace gpu_stages {
#ifdef UCCL_GPU_STAGE_TRACE
__device__ __constant__ Control control;
namespace {
std::mutex session_mutex;
State* buffer=nullptr;
bool active=false,stream_seen=false;
int device=-1;
uintptr_t selected_stream=0;
std::thread::id selected_thread;
std::string selected_mode;
void check(cudaError_t rc){
  if(rc!=cudaSuccess)throw std::runtime_error(std::string("GPU_STAGE_V2: ")+cudaGetErrorString(rc));
}
void check_device(){int current=-1;check(cudaGetDevice(&current));if(current!=device)throw std::runtime_error("GPU_STAGE_V2_DEVICE_CHANGED");}
uint64_t mask_for(std::string const& mode){
  uint64_t mask=0;
  for(unsigned i=0;i<kStages;++i){
    bool focus=mode=="dispatch" || mode=="dispatch_access";
    bool use=focus?((i>=3 && i<=7) || i==9 || (i>=11 && i<=13) || i==15 || i==17 ||
                   (i>=40 && i<=42) || i==46 || (i>=50 && i<=57) || i==59 || i==60):
      mode=="dispatch_clock"?i==46:
      (mode=="all" || mode=="access")? (i<24 || (i>=40 && i<=48)):
      mode=="clock"?i==46:mode=="kernels"?(i>=45 && i<=48):
      mode=="waits"?i<24:mode=="operators"?(i>=40 && i<=44):false;
    if(use)mask|=uint64_t{1}<<i;
  }
  return mask;
}
}
#endif

void begin(std::string const& mode){
#ifdef UCCL_GPU_STAGE_TRACE
  std::lock_guard<std::mutex> lock(session_mutex);
  if(active)throw std::runtime_error("GPU_STAGE_V2_OVERLAPPING_WINDOW");
  if(mode!="bypass" && !mask_for(mode))throw std::runtime_error("GPU_STAGE_V2_INVALID_MODE");
  check(cudaDeviceSynchronize());check(cudaGetDevice(&device));
  Control next{};
  try {
    if(mode!="bypass"){
      check(cudaMalloc(reinterpret_cast<void**>(&buffer),sizeof(State)));
      check(cudaMemset(buffer,0,sizeof(State)));
      next.state=buffer;next.mask=mask_for(mode);next.mode=(mode=="access" || mode=="dispatch_access")?1:2;
      next.dispatch_focus=mode.compare(0,8,"dispatch")==0;
    }
    check(cudaMemcpyToSymbol(control,&next,sizeof(next)));
  }catch(...){if(buffer){cudaFree(buffer);buffer=nullptr;}throw;}
  selected_mode=mode;stream_seen=false;active=true;
#else
  (void)mode;throw std::runtime_error("GPU_STAGE_V2_REQUIRES_TRACE_BUILD");
#endif
}

void validate_launch(uintptr_t stream,unsigned gx,unsigned gy,unsigned gz,unsigned bx,unsigned by,unsigned bz){
#ifdef UCCL_GPU_STAGE_TRACE
  std::lock_guard<std::mutex> lock(session_mutex);
  if(!active)return;
  check_device();
  // Private slots are valid only for ordered, one-dimensional Normal launches.
  if(gy!=1 || gz!=1 || by!=1 || bz!=1 || gx>kBlocks || bx>1024)
    throw std::runtime_error("GPU_STAGE_V2_LAUNCH_BOUNDS");
  if(stream_seen && stream!=selected_stream)throw std::runtime_error("GPU_STAGE_V2_MULTIPLE_STREAMS");
  if(stream_seen && selected_thread!=std::this_thread::get_id())throw std::runtime_error("GPU_STAGE_V2_MULTIPLE_LAUNCH_THREADS");
  selected_stream=stream;selected_thread=std::this_thread::get_id();stream_seen=true;
#else
  (void)stream;(void)gx;(void)gy;(void)gz;(void)bx;(void)by;(void)bz;
#endif
}

std::string end(){
#ifdef UCCL_GPU_STAGE_TRACE
  std::lock_guard<std::mutex> lock(session_mutex);
  if(!active)throw std::runtime_error("GPU_STAGE_V2_NO_WINDOW");
  check_device();check(cudaDeviceSynchronize());
  Control off{};check(cudaMemcpyToSymbol(control,&off,sizeof(off)));
  std::vector<State> host;
  if(buffer){host.resize(1);check(cudaMemcpy(host.data(),buffer,sizeof(State),cudaMemcpyDeviceToHost));check(cudaFree(buffer));buffer=nullptr;}
  active=false;
  int current;cudaDeviceProp props{};check(cudaGetDevice(&current));check(cudaGetDeviceProperties(&props,current));
  std::ostringstream out;out<<"{\"version\":2,\"dispatch_trace_version\":1,\"storage\":\"device_private_slots\",\"publication_correlation\":false,\"mode\":\""<<selected_mode<<"\",\"clock_domain\":\"per_thread_clock64_delta\",\"nominal_clock_khz\":"<<props.clockRate<<",\"records\":[";
  bool comma=false;
  if(!host.empty())for(unsigned b=0;b<kBlocks;++b)for(unsigned w=0;w<kWarps;++w)for(unsigned lane=0;lane<2;++lane)for(unsigned stage=0;stage<kStages;++stage){
    auto const& m=host[0].metrics[b][w][lane][stage];if(!m.calls)continue;
    if(comma){out<<',';}comma=true;
    out<<"{\"ring\":0,\"block\":"<<b<<",\"warp\":"<<w<<",\"stage\":"<<stage<<",\"sample_lane\":"<<lane<<",\"calls\":"<<m.calls<<",\"sum_cycles\":"<<m.cycles<<",\"max_cycles\":"<<m.max_cycles<<'}';
  }
  out<<"]}";return out.str();
#else
  throw std::runtime_error("GPU_STAGE_V2_REQUIRES_TRACE_BUILD");
#endif
}
} }
