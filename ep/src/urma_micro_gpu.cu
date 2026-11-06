#include "urma_micro.hpp"
#ifdef USE_URMA
#include <cuda_runtime.h>
#include <cstring>
#include <stdexcept>
#include <chrono>
#include <thread>

namespace {
void checked(cudaError_t rc){if(rc!=cudaSuccess)throw std::runtime_error(cudaGetErrorString(rc));}
__host__ __device__ unsigned char pattern(int rank,int parity,size_t slot) {
  return static_cast<unsigned char>((rank ? 0xa7 : 0x35) ^ (parity ? 0x5a : 0) ^ (slot%64));
}
__global__ void fill(unsigned char* base,UrmaMicroGeometry g,int rank) {
  size_t slot=blockIdx.x;
  for(size_t i=threadIdx.x;i<g.bytes;i+=blockDim.x) {
    base[slot*g.stride+g.src_pad+i]=pattern(rank,0,slot);
    base[g.region+slot*g.stride+g.src_pad+i]=pattern(rank,1,slot);
    base[2*g.region+slot*g.stride+g.dst_pad+i]=0;
  }
}
__global__ void validate_payload(unsigned char const* base,unsigned long long volatile const* control,
    UrmaMicroGeometry g,int rank,int parity,unsigned long long count,int* result) {
  size_t slot=blockIdx.x;bool bad=false;
  auto* data=reinterpret_cast<unsigned char volatile const*>(base+2*g.region+slot*g.stride+g.dst_pad);
  for(size_t i=threadIdx.x;i<g.bytes;i+=blockDim.x)if(data[i]!=pattern(rank,parity,slot))bad=true;
  if(threadIdx.x==0 && control[slot/g.window]!=count)bad=true;
  if(bad)atomicExch(result,1);
}
struct Observer {int* host=nullptr;int* gpu=nullptr;cudaStream_t stream=nullptr;size_t lanes=0;};
__global__ void observe(unsigned char const* base,unsigned long long volatile const* control,
    UrmaMicroGeometry g,int rank,int parity,unsigned long long count,int volatile* flags,unsigned long long cycles,bool scan) {
  size_t lane=blockIdx.x;__shared__ int error;
  if(threadIdx.x==0) {
    error=0;flags[lane*3]=1;__threadfence_system();auto start=clock64();
    while(control[lane]<count)if(clock64()-start>cycles){error=1;break;}
    __threadfence_system();
  }
  __syncthreads();
  if(!error && scan) {
    auto* data=reinterpret_cast<unsigned char volatile const*>(base+2*g.region+lane*g.stride+g.dst_pad);
    for(size_t i=threadIdx.x;i<g.bytes;i+=blockDim.x)
      if(data[i]!=pattern(rank,parity,lane))atomicExch(&error,2);
  }
  __syncthreads();
  if(threadIdx.x==0){flags[lane*3+1]=error;__threadfence_system();flags[lane*3+2]=1;__threadfence_system();}
}
}
void urma_micro_gpu_prepare(void* base,UrmaMicroGeometry const& g,int rank) {
  fill<<<g.slots,256>>>(static_cast<unsigned char*>(base),g,rank);
  checked(cudaGetLastError());checked(cudaDeviceSynchronize());
}
bool urma_micro_gpu_validate(void* base,void* control,UrmaMicroGeometry const& g,int rank,int parity,uint64_t count) {
  int* result=nullptr;checked(cudaMalloc(reinterpret_cast<void**>(&result),sizeof(int)));
  try {
    checked(cudaMemset(result,0,sizeof(int)));
    validate_payload<<<g.slots,256>>>(static_cast<unsigned char*>(base),static_cast<unsigned long long*>(control),g,rank,parity,count,result);
    checked(cudaGetLastError());checked(cudaDeviceSynchronize());int host=1;
    checked(cudaMemcpy(&host,result,sizeof(int),cudaMemcpyDeviceToHost));checked(cudaFree(result));return host==0;
  }catch(...){cudaFree(result);throw;}
}
void* urma_micro_observer_start(void* base,void* control,UrmaMicroGeometry const& g,int rank,int parity,uint64_t count,bool scan) {
  auto* o=new Observer;o->lanes=g.threads*g.lanes;
  try {
    checked(cudaHostAlloc(reinterpret_cast<void**>(&o->host),o->lanes*3*sizeof(int),cudaHostAllocMapped));
    std::memset(o->host,0,o->lanes*3*sizeof(int));
    checked(cudaHostGetDevicePointer(reinterpret_cast<void**>(&o->gpu),o->host,0));
    checked(cudaStreamCreateWithFlags(&o->stream,cudaStreamNonBlocking));
    int device=0;checked(cudaGetDevice(&device));cudaDeviceProp props{};checked(cudaGetDeviceProperties(&props,device));
    observe<<<o->lanes,256,0,o->stream>>>(static_cast<unsigned char*>(base),static_cast<unsigned long long*>(control),g,rank,parity,count,o->gpu,
        static_cast<unsigned long long>(props.clockRate)*1000*10,scan);
    checked(cudaGetLastError());
    auto deadline=std::chrono::steady_clock::now()+std::chrono::seconds(15);
    for(size_t i=0;i<o->lanes;++i)while(!__atomic_load_n(o->host+i*3,__ATOMIC_ACQUIRE)) {
      if(std::chrono::steady_clock::now()>deadline)throw std::runtime_error("micro GPU observer failed to start");
      std::this_thread::yield();
    }
    return o;
  }catch(...){if(o->stream){cudaStreamSynchronize(o->stream);cudaStreamDestroy(o->stream);}if(o->host)cudaFreeHost(o->host);delete o;throw;}
}
namespace {
__global__ void scan_local(unsigned char const* base,UrmaMicroGeometry g,int rank,int parity,int* result) {
  size_t lane=blockIdx.x;
  auto* data=reinterpret_cast<unsigned char volatile const*>(base+2*g.region+lane*g.stride+g.dst_pad);
  for(size_t i=threadIdx.x;i<g.bytes;i+=blockDim.x)
    if(data[i]!=pattern(rank,parity,lane))atomicExch(result,1);
}
}
std::string urma_micro_gpu_local_scan(void* base,UrmaMicroGeometry const& g,int rank,int parity) {
  auto* b=static_cast<unsigned char*>(base);
  // Populate the same receive slots before measuring the same volatile byte scan.
  for(size_t lane=0;lane<g.slots;++lane)
    checked(cudaMemcpy(b+2*g.region+lane*g.stride+g.dst_pad,b+parity*g.region+lane*g.stride+g.src_pad,g.bytes,cudaMemcpyDeviceToDevice));
  int* error=nullptr;cudaEvent_t begin=nullptr,end=nullptr;
  try {
    checked(cudaMalloc(reinterpret_cast<void**>(&error),sizeof(int)));checked(cudaMemset(error,0,sizeof(int)));
    checked(cudaEventCreate(&begin));checked(cudaEventCreate(&end));
    auto start=std::chrono::steady_clock::now();checked(cudaEventRecord(begin));
    scan_local<<<g.slots,256>>>(b,g,rank,parity,error);
    checked(cudaGetLastError());checked(cudaEventRecord(end));checked(cudaEventSynchronize(end));
    auto host_ns=std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now()-start).count();
    float ms=0;checked(cudaEventElapsedTime(&ms,begin,end));int bad=1;
    checked(cudaMemcpy(&bad,error,sizeof(int),cudaMemcpyDeviceToHost));
    if(bad)throw std::runtime_error("local GPU scan payload mismatch");
    checked(cudaEventDestroy(begin));begin=nullptr;checked(cudaEventDestroy(end));end=nullptr;checked(cudaFree(error));error=nullptr;
    return "{\"span_ns\":"+std::to_string(host_ns)+",\"gpu_us\":"+std::to_string(ms*1000)+",\"write_bytes\":0,\"cqes\":0,\"retired\":0}";
  }catch(...){if(begin)cudaEventDestroy(begin);if(end)cudaEventDestroy(end);if(error)cudaFree(error);throw;}
}
bool urma_micro_observer_done(void* handle,size_t first,size_t lanes) {
  auto* o=static_cast<Observer*>(handle);
  if(first+lanes>o->lanes)throw std::invalid_argument("micro observer lane range");
  for(size_t i=first;i<first+lanes;++i) {
    if(!__atomic_load_n(o->host+i*3+2,__ATOMIC_ACQUIRE))return false;
    if(__atomic_load_n(o->host+i*3+1,__ATOMIC_ACQUIRE))throw std::runtime_error("micro GPU observer timeout/payload mismatch");
  }
  return true;
}
void urma_micro_observer_finish(void* handle) {
  auto* o=static_cast<Observer*>(handle);
  checked(cudaStreamSynchronize(o->stream));checked(cudaStreamDestroy(o->stream));
  checked(cudaFreeHost(o->host));delete o;
}
#endif
