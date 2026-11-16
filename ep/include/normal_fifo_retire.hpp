#pragma once
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <stdexcept>

inline bool normal_fifo_ready_from_env() {
  auto* v=std::getenv("UCCL_URMA_NORMAL_RETIRE");
  if(!v || !*v || !std::strcmp(v,"ordered"))return false;
  if(!std::strcmp(v,"ready"))return true;
  throw std::invalid_argument("UCCL_URMA_NORMAL_RETIRE must be ordered|ready");
}

// Ordinary FIFO commands have already been popped from the GPU command FIFO
// at collection. This queue tracks software credits until network completion.
// Reap only observed completions; never cross or advance a control delimiter
// while earlier requests remain. Callback owns byte accounting/control pop.
template<class Pending,class Completed,class Retire>
void reap_normal_pending(Pending& pending,Completed& completed,bool ready,
                         uint64_t quiet,uint64_t barrier,Retire retire) {
  auto it=pending.begin();
  while(it!=pending.end()) {
    auto wr=it->first;
    bool control=wr==quiet || wr==barrier;
    if(control && it!=pending.begin())break;
    auto found=completed.find(wr);
    if(found==completed.end()) {
      if(!ready || control)break;
      ++it;continue;
    }
    auto entry=*it;
    completed.erase(found);
    it=pending.erase(it);
    retire(entry.first,entry.second);
  }
}
