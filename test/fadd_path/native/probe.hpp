#pragma once
// CPU-only observation of the existing FADD result buffer and SDK polling.
// No GPU probes, extra requests, CQ consumption, return-buffer writes, or
// changes to ordering/retirement. One data ring per Proxy, selected payload 6.
#include <atomic>
#include <algorithm>
#include <cstdint>
#include <memory>
#include <mutex>
#include <sstream>
#include <stdexcept>
#include <thread>
#include <vector>
#include <time.h>

namespace uccl::fadd_path {
constexpr unsigned kVersion=1, kStride=64, kLimit=4096;
inline std::atomic<bool> active{false};
inline std::atomic<uint64_t> epoch{0};
inline std::mutex mutex;
inline unsigned target=6;
inline uint64_t origin=0;
inline uint64_t now(){timespec t{};clock_gettime(CLOCK_MONOTONIC_RAW,&t);return uint64_t(t.tv_sec)*1000000000ULL+t.tv_nsec;}
struct Pair {
  uintptr_t cq=0,lane=0;uint64_t write_id=0,atomic_id=0,bytes=0,operand=0;
  uint64_t w_begin=0,w_end=0,a_begin=0,a_end=0;
  uint64_t w_cqe=0,a_cqe=0,w_handled=0,a_handled=0;
  uint64_t result_zero=0,result_lower=0,result_upper=0,result_value=0,final_value=0;
  uint64_t w_seq=0,a_seq=0;
  uint64_t* result=nullptr;
  int fence=-1,order=-1,status=0;
  bool initial_zero=false,finished=false;
};
struct Lane {uintptr_t cq,lane;unsigned writes;};
struct Queue {uintptr_t cq;uint64_t calls=0;};
struct Poll {uintptr_t cq;uint64_t seq,begin,end;int count;};
struct Stream {
  std::atomic<bool> writing{false};uint64_t frame=0,dropped=0,errors=0;int proxy=-1;
  std::vector<Pair> pairs;std::vector<Lane> lanes;std::vector<Queue> queues;std::vector<Poll> polls;
  Stream(){pairs.reserve(16);lanes.reserve(16);queues.reserve(16);polls.reserve(kLimit);}
};
inline std::vector<std::shared_ptr<Stream>> streams;
inline thread_local std::shared_ptr<Stream> local;
struct Guard {
  Stream* s=nullptr;
  explicit Guard(int proxy=-1){
    if(!active.load(std::memory_order_relaxed))return;
    if(!local){auto next=std::make_shared<Stream>();std::lock_guard<std::mutex> lock(mutex);streams.push_back(next);local=next;}
    local->writing.store(true,std::memory_order_release);
    if(!active.load(std::memory_order_acquire)){local->writing.store(false,std::memory_order_release);return;}
    auto frame=epoch.load(std::memory_order_acquire);s=local.get();
    if(s->frame!=frame){s->pairs.clear();s->lanes.clear();s->queues.clear();s->polls.clear();s->dropped=s->errors=0;s->frame=frame;s->proxy=-1;}
    if(proxy>=0)s->proxy=proxy;
  }
  ~Guard(){if(s)s->writing.store(false,std::memory_order_release);}
};
struct PostStamp {uint64_t frame=0,begin=0;};
struct PollStamp {uint64_t frame=0,seq=0,begin=0;};
inline void result_sample(Pair& p){
  if(!p.result || p.a_cqe || p.result_upper)return;
  auto before=now();auto value=__atomic_load_n(p.result,__ATOMIC_ACQUIRE);auto after=now();
  if(!value)p.result_zero=before;
  else {p.result_lower=p.result_zero;p.result_upper=after;p.result_value=value;}
}
inline PostStamp post_begin(){
  if(!active.load(std::memory_order_relaxed))return {};
  return {epoch.load(std::memory_order_acquire),now()};
}
inline void issued(PostStamp stamp,uintptr_t cq,uintptr_t lane,uint64_t id,
                   bool write,bool atomic,uint64_t bytes,uint64_t operand,uint64_t* result,
                   int fence,int order,int proxy){
  if(!stamp.frame || id==UINT64_MAX || (id>>32)!=0)return;
  Guard g(proxy);if(!g.s || g.s->frame!=stamp.frame)return;auto& s=*g.s;
  if(write && bytes>=4096){
    auto it=std::find_if(s.lanes.begin(),s.lanes.end(),[&](Lane const& x){return x.cq==cq && x.lane==lane;});
    if(it==s.lanes.end()){
      if(s.lanes.size()>=16){++s.dropped;return;}
      s.lanes.push_back({cq,lane,0});it=std::prev(s.lanes.end());
    }
    if(++it->writes==target){
      if(s.pairs.size()>=16){++s.dropped;return;}
      Pair p;p.cq=cq;p.lane=lane;p.write_id=id;p.atomic_id=id+1;p.bytes=bytes;p.w_begin=stamp.begin;
      s.pairs.push_back(p);
      if(std::none_of(s.queues.begin(),s.queues.end(),[&](Queue const& q){return q.cq==cq;}))s.queues.push_back({cq,0});
    }
  }else if(atomic){
    for(auto& p:s.pairs)if(p.cq==cq && p.lane==lane && p.atomic_id==id){
      if(p.a_begin){++s.errors;return;}
      p.a_begin=stamp.begin;p.operand=operand;p.result=result;p.fence=fence;p.order=order;
      if(result){auto t=now();p.initial_zero=__atomic_load_n(result,__ATOMIC_ACQUIRE)==0;p.result_zero=t;}
    }
  }
}
inline void post_end(PostStamp stamp,int status){
  if(!stamp.frame)return;
  auto t=now();Guard g;if(!g.s || g.s->frame!=stamp.frame)return;
  if(status)++g.s->errors;
  for(auto& p:g.s->pairs){if(p.w_begin==stamp.begin)p.w_end=t;if(p.a_begin==stamp.begin)p.a_end=t;}
}
inline PollStamp poll_begin(uintptr_t cq){
  Guard g;if(!g.s)return {};auto& s=*g.s;
  auto q=std::find_if(s.queues.begin(),s.queues.end(),[&](Queue const& x){return x.cq==cq;});
  if(q==s.queues.end() || std::none_of(s.pairs.begin(),s.pairs.end(),[&](Pair const& p){return p.cq==cq && !p.finished;}))return {};
  auto seq=++q->calls;uint64_t t=0;
  if((seq-1)%kStride==0){for(auto& p:s.pairs)if(p.cq==cq)result_sample(p);t=now();}
  return {s.frame,seq,t};
}
inline uint64_t poll_end(PollStamp stamp,uintptr_t cq,int count){
  if(!stamp.frame)return 0;
  if(!stamp.begin && count==0)return 0;
  auto t=now();Guard g;if(!g.s || g.s->frame!=stamp.frame)return 0;
  if(g.s->polls.size()<kLimit)g.s->polls.push_back({cq,stamp.seq,stamp.begin,t,count});else ++g.s->dropped;
  if(count<0)++g.s->errors;
  return t;
}
inline void completed(PollStamp stamp,uintptr_t cq,uint64_t id,uint64_t sdk_return,int status){
  if(!stamp.frame || !sdk_return)return;
  Guard g;if(!g.s || g.s->frame!=stamp.frame)return;
  for(auto& p:g.s->pairs)if(p.cq==cq && (id==p.write_id || id==p.atomic_id)){
    if(status){p.status=status;++g.s->errors;}
    if(id==p.write_id){if(p.w_cqe)++g.s->errors;p.w_cqe=sdk_return;p.w_handled=now();p.w_seq=stamp.seq;p.finished=p.a_cqe!=0;}
    else {if(p.a_cqe)++g.s->errors;result_sample(p);if(p.result)p.final_value=__atomic_load_n(p.result,__ATOMIC_ACQUIRE);
      p.a_cqe=sdk_return;p.a_handled=now();p.a_seq=stamp.seq;p.finished=p.w_cqe!=0;}
  }
}
inline void begin(unsigned ordinal){
  std::lock_guard<std::mutex> lock(mutex);
  if(active.load() || ordinal!=6)throw std::runtime_error("fadd_path requires nonoverlapping sixth-payload capture");
  target=ordinal;origin=now();++epoch;active.store(true,std::memory_order_release);
}
inline std::string end(){
  std::lock_guard<std::mutex> lock(mutex);active.store(false,std::memory_order_release);
  auto frame=epoch.load();std::ostringstream out;
  out<<"{\"version\":1,\"clock\":\"CLOCK_MONOTONIC_RAW\",\"target_payload\":6,\"ring\":0,\"poll_stride\":"<<kStride<<",\"origin_ns\":"<<origin<<",\"streams\":[";
  bool comma=false;
  for(auto const& s:streams){
    while(s->writing.load(std::memory_order_acquire))std::this_thread::yield();
    if(s->frame!=frame)continue;
    if(comma)out<<',';
    comma=true;
    out<<"{\"proxy\":"<<s->proxy<<",\"dropped\":"<<s->dropped<<",\"errors\":"<<s->errors<<",\"pairs\":[";
    bool pc=false;
    for(auto const& p:s->pairs){
      if(pc)out<<',';
      pc=true;
      out<<"{\"cq\":"<<p.cq<<",\"lane\":"<<p.lane<<",\"write_id\":"<<p.write_id<<",\"atomic_id\":"<<p.atomic_id
         <<",\"bytes\":"<<p.bytes<<",\"operand\":"<<p.operand<<",\"fence\":"<<p.fence<<",\"order\":"<<p.order
         <<",\"write_post_begin\":"<<p.w_begin<<",\"write_post_end\":"<<p.w_end<<",\"atomic_post_begin\":"<<p.a_begin<<",\"atomic_post_end\":"<<p.a_end
         <<",\"write_sdk_return\":"<<p.w_cqe<<",\"atomic_sdk_return\":"<<p.a_cqe<<",\"write_handled\":"<<p.w_handled<<",\"atomic_handled\":"<<p.a_handled
         <<",\"result_last_zero\":"<<p.result_zero<<",\"result_lower\":"<<p.result_lower<<",\"result_upper\":"<<p.result_upper
         <<",\"result_value\":"<<p.result_value<<",\"result_at_completion\":"<<p.final_value
         <<",\"initial_zero\":"<<(p.initial_zero?"true":"false")<<",\"write_poll_seq\":"<<p.w_seq<<",\"atomic_poll_seq\":"<<p.a_seq
         <<",\"finished\":"<<(p.finished?"true":"false")<<",\"status\":"<<p.status<<'}';
    }
    out<<"],\"polls\":[";pc=false;
    for(auto const& p:s->polls){if(pc)out<<',';pc=true;out<<"{\"cq\":"<<p.cq<<",\"seq\":"<<p.seq<<",\"begin\":"<<p.begin<<",\"end\":"<<p.end<<",\"count\":"<<p.count<<'}';}
    out<<"]}";
  }
  out<<"]}";return out.str();
}
}
