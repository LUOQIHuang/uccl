#pragma once
// Independent diagnostic window. All timestamps use this process's CPU clock.
#include "urma_phase_diag.hpp"
#include <stdexcept>
namespace uccl::verbs_timeline {
inline std::atomic<bool> active{false};
inline std::mutex mutex;
struct Event {uint64_t ns,id,value;uint32_t qp;int proxy;char const* stage;};
inline std::vector<Event> events;
inline uint64_t origin=0,dropped=0;
inline void record(char const* stage,uint64_t id,uint32_t qp=0,uint64_t value=0) {
  if(!active.load(std::memory_order_relaxed))return;
  auto ns=urma_diag::now();std::lock_guard<std::mutex> lock(mutex);
  if(!active.load(std::memory_order_relaxed))return;
  if(events.size()>=32768){++dropped;return;}
  events.push_back({ns,id,value,qp,urma_diag::proxy,stage});
}
inline void begin(){
  std::lock_guard<std::mutex> lock(mutex);
  if(active.load())throw std::runtime_error("overlapping verbs timeline");
  events.clear();events.reserve(32768);dropped=0;origin=urma_diag::now();active=true;
}
inline std::string end(){
  std::lock_guard<std::mutex> lock(mutex);active=false;std::ostringstream out;
  out<<"{\"version\":1,\"origin_ns\":"<<origin<<",\"dropped\":"<<dropped<<",\"events\":[";
  bool comma=false;for(auto const& e:events){if(comma)out<<',';comma=true;
    out<<"{\"ns\":"<<e.ns<<",\"wr_id\":"<<e.id<<",\"qp\":"<<e.qp<<",\"proxy\":"<<e.proxy
       <<",\"stage\":\""<<e.stage<<"\",\"value\":"<<e.value<<'}';}
  out<<"]}";return out.str();
}
}
