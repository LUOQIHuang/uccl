#pragma once
// Opt-in causal experiment in the real Normal path. Default is inactive.
#include <array>
#include <atomic>
#include <chrono>
#include <mutex>
#include <sstream>
#include <thread>
#include <vector>
#include <stdexcept>
namespace uccl::dispatch_trial {
inline std::atomic<bool> active{false},trace{false};
inline std::atomic<uint64_t> epoch{0},delay_us{0},origin{0};
struct First {std::atomic<uint64_t> ready{0},released{0};};
inline std::array<First,64> first;
struct Submit {uint64_t ns,bytes,writes,fadds;int proxy;size_t lane;};
inline std::mutex mutex;
inline std::vector<Submit> submits;
inline uint64_t dropped=0;
inline uint64_t now(){return std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now().time_since_epoch()).count();}
inline void begin(uint64_t us,bool detailed) {
  if(us>1000 || active.load())throw std::invalid_argument("dispatch trial invalid delay/overlapping call");
  std::lock_guard<std::mutex> lock(mutex);
  for(auto& f:first){f.ready=0;f.released=0;}
  submits.clear();dropped=0;delay_us=us;trace=detailed;origin=now();++epoch;
  active.store(true,std::memory_order_release);
}
inline void payload(int proxy,uint64_t& seen,uint32_t bytes) {
  if(bytes<4096 || !active.load(std::memory_order_acquire))return;
  auto e=epoch.load();if(seen==e)return;
  if(proxy<0 || proxy>=64)throw std::runtime_error("dispatch trial proxy outside range");
  seen=e;first[proxy].ready=now();
  auto us=delay_us.load();if(us)std::this_thread::sleep_for(std::chrono::microseconds(us));
  first[proxy].released=now();
}
inline void submit(int proxy,size_t lane,uint64_t bytes,uint64_t writes,uint64_t fadds) {
  if(!active.load(std::memory_order_acquire) || !trace.load())return;
  auto ns=now();std::lock_guard<std::mutex> lock(mutex);
  if(submits.size()>=4096){++dropped;return;}
  submits.push_back({ns,bytes,writes,fadds,proxy,lane});
}
inline std::string end() {
  active.store(false,std::memory_order_release);
  std::lock_guard<std::mutex> lock(mutex);std::ostringstream out;
  out<<"{\"version\":1,\"delay_us\":"<<delay_us.load()<<",\"origin_ns\":"<<origin.load()
     <<",\"dropped\":"<<dropped<<",\"first_payload\":[";
  bool comma=false;
  for(size_t i=0;i<first.size();++i)if(first[i].ready.load()) {
    if(comma)out<<',';
    comma=true;out<<"{\"proxy\":"<<i<<",\"ready_ns\":"<<first[i].ready.load()<<",\"released_ns\":"<<first[i].released.load()<<'}';
  }
  out<<"],\"submits\":[";comma=false;
  for(auto const& s:submits){if(comma)out<<',';comma=true;
    out<<"{\"ns\":"<<s.ns<<",\"proxy\":"<<s.proxy<<",\"lane\":"<<s.lane<<",\"bytes\":"<<s.bytes<<",\"writes\":"<<s.writes<<",\"fadds\":"<<s.fadds<<'}';}
  out<<"]}";return out.str();
}
}
