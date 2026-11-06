#include "urma_dispatch_trial.hpp"
#include <cassert>
#include <iostream>
int main() {
  namespace d=uccl::dispatch_trial;
  uint64_t seen=0;d::payload(0,seen,8192);assert(seen==0);
  d::begin(200,true);
  bool rejected=false;try{d::begin(0,false);}catch(std::invalid_argument const&){rejected=true;}assert(rejected);
  std::vector<std::thread> threads;
  for(int p=0;p<4;++p)threads.emplace_back([p](){
    uint64_t local=0;d::payload(p,local,16);assert(local==0);
    d::payload(p,local,8192);auto released=d::first[p].released.load();
    d::payload(p,local,8192);assert(released==d::first[p].released.load());
    assert(released-d::first[p].ready.load()>=200000);
    d::submit(p,1,8192,1,1);
  });
  for(auto& t:threads)t.join();
  assert(d::submits.size()==4);
  for(int i=0;i<4100;++i)d::submit(0,1,1,1,0);
  assert(d::submits.size()==4096 && d::dropped==8);
  auto text=d::end();assert(text.find("\"delay_us\":200")!=std::string::npos);
  d::begin(0,false);assert(d::submits.empty());d::payload(0,seen,8192);d::submit(0,1,1,1,0);
  assert(d::submits.empty());d::end();
  std::cout<<"Real Normal dispatch trial control tests passed\n";
}
