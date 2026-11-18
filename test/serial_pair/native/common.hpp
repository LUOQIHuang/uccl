#pragma once
// Local SDK observations only. No requests, fences or queue parameters here.
#include <algorithm>
#include <cstdint>
#include <sstream>
#include <stdexcept>
#include <vector>
#include <time.h>
#include <sched.h>
namespace uccl::serial_pair {
inline uint64_t now(){timespec t{};clock_gettime(CLOCK_MONOTONIC_RAW,&t);return uint64_t(t.tv_sec)*1000000000ULL+t.tv_nsec;}
inline void require(bool x,char const* why){if(!x)throw std::runtime_error(why);}
inline void verify_cpu(int cpu){cpu_set_t m;CPU_ZERO(&m);require(cpu>=0 && cpu<CPU_SETSIZE && sched_getaffinity(0,sizeof(m),&m)==0 && CPU_COUNT(&m)==1 && CPU_ISSET(cpu,&m),"serial worker must be pinned to one selected CPU");}
struct Pair {
  uint64_t wid=0,aid=0,wb=0,we=0,ab=0,ae=0,wc=0,ac=0,ws=0,as=0,polls=0;
  bool done()const{return wc && ac;}
};
inline thread_local Pair* current=nullptr;
inline uint64_t post_tick(){return current?now():0;}
inline void posted(uint64_t id,bool write,bool atomic,uint64_t t){
  if(!current || !t)return;
  auto& p=*current;
  if(write && id==p.wid){require(!p.wb,"duplicate serial WRITE submission");p.wb=t;}
  else if(atomic && id==p.aid){require(!p.ab,"duplicate serial FADD submission");p.ab=t;}
  else throw std::runtime_error("unexpected request during serial pair");
}
inline void post_end(uint64_t t,int rc){
  if(!current || !t)return;
  require(rc==0,"serial SDK post failed; retain DMA ownership");auto end=now();
  if(current->wb==t)current->we=end;
  if(current->ab==t)current->ae=end;
}
inline uint64_t poll_begin(){return current?++current->polls:0;}
inline uint64_t poll_return(uint64_t seq){return seq?now():0;}
inline void completed(uint64_t id,uint64_t t,uint64_t seq,int status){
  if(!current || !seq)return;
  require(status==0,"serial completion error");auto& p=*current;
  if(id==p.wid){require(!p.wc,"duplicate serial WRITE completion");p.wc=t;p.ws=seq;}
  else if(id==p.aid){require(!p.ac,"duplicate serial FADD completion");p.ac=t;p.as=seq;}
  else throw std::runtime_error("unexpected serial completion");
}
template<class Submit,class Poll>
std::string measure(size_t sequence,size_t count,Submit submit,Poll poll){
  require(!current && (count==1 || count==2),"invalid serial capture");
  std::vector<Pair> rows;rows.reserve(count);auto begin=now();
  for(size_t j=0;j<count;++j){
    Pair p;p.wid=uint64_t(sequence)*4+j*2;p.aid=p.wid+1;
    struct Reset{~Reset(){current=nullptr;}} reset;
    current=&p;submit(j,p.wid,p.aid);
    auto deadline=now()+15000000000ULL;
    while(!p.done()){poll();require(now()<deadline,"serial completion timeout");}
    require(p.wb && p.ab && p.we>=p.wb && p.ae>=p.ab && p.wc>=p.we && p.ac>=p.ae,"invalid serial timestamps");
    if(!rows.empty())require(p.wb>=std::max(rows.back().wc,rows.back().ac),"overlapping serial pairs");
    rows.push_back(p);current=nullptr;
  }
  auto end=now();std::ostringstream s;
  s<<"{\"version\":1,\"clock\":\"CLOCK_MONOTONIC_RAW\",\"max_inflight_pairs\":1,\"pairs_expected\":"<<count
   <<",\"loop_api_ns\":"<<end-begin<<",\"pairs\":[";
  for(size_t i=0;i<rows.size();++i){auto const& p=rows[i];if(i)s<<',';
    s<<"{\"write_id\":"<<p.wid<<",\"atomic_id\":"<<p.aid<<",\"write_post_begin\":"<<p.wb<<",\"write_post_end\":"<<p.we
     <<",\"atomic_post_begin\":"<<p.ab<<",\"atomic_post_end\":"<<p.ae<<",\"write_cqe\":"<<p.wc<<",\"atomic_cqe\":"<<p.ac
     <<",\"write_poll_seq\":"<<p.ws<<",\"atomic_poll_seq\":"<<p.as<<",\"sdk_poll_calls\":"<<p.polls<<'}';
  }
  s<<"]}";return s.str();
}
}
