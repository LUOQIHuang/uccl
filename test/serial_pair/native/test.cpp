#include "common.hpp"
#include <cassert>
#include <iostream>
using namespace uccl::serial_pair;
int main(){
  for(size_t count:{size_t(1),size_t(2)}){
    size_t sent=0,finished=0;bool reverse=false;
    auto json=measure(3,count,[&](size_t j,uint64_t w,uint64_t a){
      assert(j==finished && sent==finished);++sent;
      auto t=post_tick();posted(w,true,false,t);post_end(t,0);
      t=post_tick();posted(a,false,true,t);post_end(t,0);
    },[&](){auto q=poll_begin();auto t=poll_return(q);
      auto w=current->wid,a=current->aid;
      if(!reverse){completed(w,t,q,0);reverse=true;}
      else {completed(a,t,q,0);++finished;reverse=false;}
    });
    assert(sent==count && finished==count && current==nullptr);std::cout<<json<<'\n';
  }
  // Both CQEs in one SDK return are preserved as one observation batch.
  auto json=measure(5,1,[&](size_t,uint64_t w,uint64_t a){auto t=post_tick();posted(w,true,false,t);posted(a,false,true,t);post_end(t,0);},[&](){auto q=poll_begin();auto t=poll_return(q);completed(current->aid,t,q,0);completed(current->wid,t,q,0);});
  std::cout<<json<<'\n';
  bool failed=false;
  try{measure(7,1,[&](size_t,uint64_t,uint64_t){throw std::runtime_error("fake post failure");},[](){});}catch(std::runtime_error const&){failed=true;}
  assert(failed && current==nullptr);
}
