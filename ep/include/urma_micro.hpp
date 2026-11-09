#pragma once
#include "urma_transport.hpp"
#include <memory>
#include <string>
#include <vector>

struct UrmaMicroGeometry {
  size_t bytes=0,stride=0,region=0,src_pad=0,dst_pad=0;
  uint32_t threads=0,lanes=0,window=0,slots=0;
};
// GPU helpers are defined in a separate CUDA/cu-bridge compilation unit.
void urma_micro_gpu_prepare(void*,UrmaMicroGeometry const&,int);
bool urma_micro_gpu_validate(void*,void*,UrmaMicroGeometry const&,int,int,uint64_t);
void* urma_micro_observer_start(void*,void*,UrmaMicroGeometry const&,int,int,uint64_t,bool scan=true);
std::string urma_micro_gpu_local_scan(void*,UrmaMicroGeometry const&,int,int);
bool urma_micro_observer_done(void*,size_t,size_t);
void urma_micro_observer_finish(void*);

#ifdef USE_URMA
class UrmaMicroSession {
 public:
  UrmaMicroSession(int device,size_t threads,size_t max_bytes);
  ~UrmaMicroSession();
  UrmaMicroSession(UrmaMicroSession const&)=delete;
  UrmaMicroSession& operator=(UrmaMicroSession const&)=delete;
  LocalConnInfo descriptor(size_t) const;
  std::string metadata() const;
  void connect(std::vector<RemoteConnInfo> const&);
  void prepare(size_t bytes,size_t src_mod,size_t dst_mod,size_t window,int rank);
  void experiment(bool send,bool receive,bool scan,std::vector<int> const& cpus);
  std::string local_scan(size_t sequence);
  void notification_mode(int operation);
  void arm(size_t sequence,bool visible,bool with_fadd,size_t messages);
  std::string step(size_t sequence,bool with_fadd,size_t messages);
  void validate(size_t sequence,bool with_fadd,size_t messages);
  void close();
 private:
  struct Impl;
  std::unique_ptr<Impl> impl_;
};
#endif
