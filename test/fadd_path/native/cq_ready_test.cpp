#include <cassert>
#include <iostream>
#include "cq_ready.hpp"

using namespace uccl::cq_ready;
uint64_t ticks=100;
uint64_t clock_tick(){return ticks++;}
int fake_poll(urma_jfc_t*,int,urma_cr_t*){return 0;}
int main(){
  setenv("FADD_PATH_CQ_READY","1",1);
  alignas(8) uint64_t object[55]{};
  alignas(64) uint32_t entries[32*16]{};
  Layout x{reinterpret_cast<uintptr_t>(object),reinterpret_cast<uintptr_t>(entries),6,32,31,5};
  assert(ready_mask(x,0)==0);
  entries[16*2]=4;
  assert(ready_mask(x,0)==4); // target slot 2 valid behind invalid head
  entries[16*31]=4;
  assert((ready_mask(x,31)&3)==3); // index32 wraps: zero owner is now valid
  assert(!(ready_mask(x,31)&8)); // physical slot2 still carries the old owner
  entries[16*31]=0;
  assert((ready_mask(x,31)&1)==0);
  entries[16*2]=0;
  layouts.push_back(x);
  auto jfc=reinterpret_cast<urma_jfc_t*>(object);
  before(jfc,1,1,0,clock_tick);
  after(1,0,150);
  assert(local->polls.back().ready==0);
  entries[16*2]=4;
  before(jfc,1,2,0,clock_tick);
  after(1,0,160);
  assert(local->polls.back().ready==4);
  entries[0]=entries[16]=4;
  before(jfc,1,3,0,clock_tick);
  __atomic_store_n(reinterpret_cast<uint32_t*>(x.jfc+300),3u,__ATOMIC_RELEASE);
  after(1,3,200);
  completed(1,10,1000,0);completed(1,11,1001,1);completed(1,12,1002,2);
  assert(local->polls.back().completed[2].position==2);
  urma_context_t context{};urma_ops_t ops{};ops.poll_jfc=fake_poll;
  context.ops=&ops;jfc->urma_ctx=&context;
  bool rejected=false;
  try{bind(jfc);}catch(const std::runtime_error&){rejected=true;}
  assert(rejected); // refuse private offsets on any other provider Build ID
  std::cout<<dump(1)<<"\n";
}
