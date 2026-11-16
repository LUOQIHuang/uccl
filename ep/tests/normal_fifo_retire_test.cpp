#include "normal_fifo_retire.hpp"
#include <cassert>
#include <deque>
#include <unordered_set>
#include <vector>
#include <algorithm>
using Queue=std::deque<std::pair<uint64_t,size_t>>;
int main(){
  {
    Queue q;std::unordered_set<uint64_t> done;size_t bytes=0;
    for(uint64_t i=1;i<=8;++i){q.push_back({i,i==1?0:100});if(i>1)done.insert(i);}
    reap_normal_pending(q,done,true,99,100,[&](uint64_t wr,size_t n){assert(wr!=1);bytes+=n;});
    assert(q.size()==1 && q.front().first==1 && bytes==700 && done.empty());
    assert(8-q.size()==7); // Keep the same 8 unfinished-command budget.
  }
  for(bool ready:{false,true}) {
    Queue q{{1,0},{2,100},{3,200}};std::unordered_set<uint64_t> done{2,3};size_t bytes=0;
    reap_normal_pending(q,done,ready,99,100,[&](uint64_t,size_t n){bytes+=n;});
    assert(q.size()==(ready?1:3));assert(bytes==(ready?300:0));
    done.insert(1);reap_normal_pending(q,done,ready,99,100,[&](uint64_t,size_t n){bytes+=n;});
    assert(q.empty() && done.empty() && bytes==300);
  }
  for(uint64_t delimiter:{90,91}) {
    Queue q{{1,0},{2,10},{delimiter,0},{4,40}};std::unordered_set<uint64_t> done{2,delimiter,4};std::vector<uint64_t> retired;
    auto cb=[&](uint64_t id,size_t){retired.push_back(id);};
    reap_normal_pending(q,done,true,90,91,cb);
    assert((retired==std::vector<uint64_t>{2}));assert(q.front().first==1 && q.size()==3);
    done.insert(1);reap_normal_pending(q,done,true,90,91,cb);
    assert((retired==std::vector<uint64_t>{2,1,delimiter,4}));assert(q.empty());
  }
  // Every completion permutation retires exactly once. Delimiter is never
  // retired until all its predecessors have actually completed.
  std::vector<uint64_t> order{1,2,3,4};
  do {
    Queue q{{1,10},{2,20},{3,30},{4,0}};std::unordered_set<uint64_t> done,observed,retired;
    for(auto id:order) {
      done.insert(id);observed.insert(id);
      reap_normal_pending(q,done,true,4,99,[&](uint64_t wr,size_t){
        assert(observed.count(wr) && retired.insert(wr).second);
        if(wr==4)assert(retired.size()==4);
      });
      assert(q.size()+retired.size()==4);
    }
    assert(q.empty() && done.empty());
  }while(std::next_permutation(order.begin(),order.end()));
  unsetenv("UCCL_URMA_NORMAL_RETIRE");assert(!normal_fifo_ready_from_env());
  setenv("UCCL_URMA_NORMAL_RETIRE","ready",1);assert(normal_fifo_ready_from_env());
}
