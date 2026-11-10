// Test fixture only: CPU simulation of GPU allocation/copy/device boundaries.
#pragma once
#include <cstdlib>
#include <cstring>
#define __device__
#define __constant__
using cudaError_t=int;
constexpr int cudaSuccess=0,cudaMemcpyDeviceToHost=1;
struct cudaDeviceProp {int clockRate=1600000;};
inline int fixture_device=0,fixture_allocations=0;
inline int cudaGetDevice(int* d){*d=fixture_device;return 0;}
inline int cudaDeviceSynchronize(){return 0;}
inline char const* cudaGetErrorString(int){return "fixture";}
inline int cudaGetDeviceProperties(cudaDeviceProp* p,int){p->clockRate=1600000;return 0;}
inline int cudaMalloc(void** p,size_t n){*p=std::malloc(n);++fixture_allocations;return *p?0:1;}
inline int cudaFree(void* p){std::free(p);--fixture_allocations;return 0;}
inline int cudaMemset(void* p,int v,size_t n){std::memset(p,v,n);return 0;}
inline int cudaMemcpy(void* dst,void const* src,size_t n,int){std::memcpy(dst,src,n);return 0;}
template<class T>int cudaMemcpyToSymbol(T& dst,void const* src,size_t n){std::memcpy(&dst,src,n);return 0;}
