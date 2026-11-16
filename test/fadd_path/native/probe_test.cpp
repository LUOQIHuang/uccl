#include "probe.hpp"
#include <cassert>
#include <iostream>
using namespace uccl::fadd_path;
int main(){
  assert(post_begin().frame==0);
  begin(6);
  for(uint64_t i=0;i<6;++i){auto t=post_begin();issued(t,1,2,100+i*2,true,false,459264,0,nullptr,0,1,0);post_end(t,0);}
  uint64_t value=0;auto a=post_begin();issued(a,1,2,111,false,true,8,32,&value,1,2,0);post_end(a,0);
  auto w=poll_begin(1);auto wt=poll_end(w,1,1);completed(w,1,110,wt,0);
  assert(local->pairs.size()==1 && local->pairs[0].initial_zero);
  // A sampled empty poll while the result is still zero.
  for(unsigned i=0;i<64;++i){auto p=poll_begin(1);poll_end(p,1,0);}
  value=160;
  for(unsigned i=0;i<64;++i){auto p=poll_begin(1);poll_end(p,1,0);}
  assert(local->pairs[0].result_lower>0 && local->pairs[0].result_upper>local->pairs[0].result_lower);
  auto p=poll_begin(1);auto t=poll_end(p,1,1);completed(p,1,111,t,0);
  assert(local->pairs[0].finished && local->pairs[0].final_value==160);
  assert(local->pairs[0].w_cqe<local->pairs[0].a_cqe);
  auto text=end();assert(!active.load());std::cout<<text<<'\n';
  // A new capture cannot reuse records or a completed context's pointer.
  begin(6);auto old=post_begin();auto empty=poll_begin(1);assert(empty.frame==0);
  assert(local->pairs.empty());end();
  assert(old.frame!=a.frame);
  // Local CQE order is independent: retain an atomic-before-WRITE result,
  // and stop touching scratch as soon as its FADD completion was handled.
  begin(6);
  for(uint64_t i=0;i<6;++i){auto s=post_begin();issued(s,7,8,200+i*2,true,false,459264,0,nullptr,0,1,1);post_end(s,0);}
  value=0;auto s=post_begin();issued(s,7,8,211,false,true,8,32,&value,1,2,1);post_end(s,0);
  value=160;auto first=poll_begin(7);auto ft=poll_end(first,7,1);completed(first,7,211,ft,0);
  assert(!local->pairs[0].finished);
  local->pairs[0].result=reinterpret_cast<uint64_t*>(uintptr_t(1)); // freed-slot stand-in; must never read it
  for(unsigned i=0;i<64;++i){auto e=poll_begin(7);poll_end(e,7,0);}
  auto second=poll_begin(7);auto st=poll_end(second,7,1);completed(second,7,210,st,0);
  assert(local->pairs[0].finished && local->pairs[0].a_cqe<local->pairs[0].w_cqe);
  end();
}
