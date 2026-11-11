#pragma once
// Independent diagnostic window. All timestamps use this process's CPU clock.
#include "urma_phase_diag.hpp"
#include <stdexcept>
#include <time.h>
#include <thread>
#include <algorithm>
namespace uccl::transport_timeline {
inline std::atomic<bool> active{false};
inline std::mutex mutex;
struct Event {uint64_t ns,id,value;uint32_t qp;int proxy;char const* stage;};
inline std::vector<Event> events;
inline uint64_t origin=0,dropped=0;
#ifdef UCCL_REFERENCE_EVENTS
struct alignas(64) Stream {std::atomic<bool> writing{false};std::vector<Event> records;uint64_t dropped=0;};
inline std::vector<std::shared_ptr<Stream>> streams;
inline thread_local std::shared_ptr<Stream> stream;
inline std::atomic<uint64_t> capture_epoch{0};
#endif
inline uint64_t now(){
#ifdef UCCL_REFERENCE_EVENTS
  timespec ts{};clock_gettime(CLOCK_MONOTONIC_RAW,&ts);
  return uint64_t(ts.tv_sec)*1000000000ULL+ts.tv_nsec;
#else
  return urma_diag::now();
#endif
}
inline void record_at(char const* stage,uint64_t id,uint64_t ns,uint32_t qp=0,uint64_t value=0) {
#ifdef UCCL_REFERENCE_EVENTS
  if(!active.load())return;
  auto frame=capture_epoch.load();
  if(!stream){
    auto next=std::make_shared<Stream>();next->records.reserve(32768);
    std::lock_guard<std::mutex> lock(mutex);streams.push_back(next);stream=next;
  }
  stream->writing=true;
  if(active.load() && frame==capture_epoch.load() && ns>=origin){
    if(stream->records.size()<32768)stream->records.push_back({ns,id,value,qp,urma_diag::proxy,stage});
    else ++stream->dropped;
  }
  stream->writing=false;
#else
  if(!active.load(std::memory_order_relaxed))return;
  std::lock_guard<std::mutex> lock(mutex);
  if(!active.load(std::memory_order_relaxed))return;
  if(events.size()>=32768){++dropped;return;}
  events.push_back({ns,id,value,qp,urma_diag::proxy,stage});
#endif
}
inline void record(char const* stage,uint64_t id,uint32_t qp=0,uint64_t value=0) {
  if(!active.load(std::memory_order_relaxed))return;
  record_at(stage,id,now(),qp,value);
}
inline void begin(){
  std::lock_guard<std::mutex> lock(mutex);
  if(active.load())throw std::runtime_error("overlapping transport timeline");
#ifdef UCCL_REFERENCE_EVENTS
  ++capture_epoch;
  for(auto const& s:streams){s->records.clear();s->dropped=0;}
#endif
  events.clear();events.reserve(32768);dropped=0;origin=now();active=true;
}
inline std::string end(){
  std::lock_guard<std::mutex> lock(mutex);active=false;std::ostringstream out;
#ifdef UCCL_REFERENCE_EVENTS
  for(auto const& s:streams){
    while(s->writing.load())std::this_thread::yield();
    auto take=std::min(size_t(32768)-events.size(),s->records.size());
    events.insert(events.end(),s->records.begin(),s->records.begin()+take);
    dropped+=s->dropped+s->records.size()-take;
  }
#endif
  out<<"{\"version\":1,";
#ifdef UCCL_REFERENCE_EVENTS
  out<<"\"clock\":\"CLOCK_MONOTONIC_RAW\",";
#endif
  out<<"\"origin_ns\":"<<origin<<",\"dropped\":"<<dropped<<",\"events\":[";
  bool comma=false;for(auto const& e:events){if(comma)out<<',';comma=true;
    out<<"{\"ns\":"<<e.ns<<",\"wr_id\":"<<e.id<<",\"qp\":"<<e.qp<<",\"proxy\":"<<e.proxy
       <<",\"stage\":\""<<e.stage<<"\",\"value\":"<<e.value<<'}';}
  out<<"]}";return out.str();
}
}
