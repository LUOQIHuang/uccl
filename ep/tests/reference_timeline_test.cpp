#define UCCL_REFERENCE_EVENTS
#include "transport_timeline.hpp"
#include <cassert>
int main(){
  namespace t=uccl::transport_timeline;
  std::atomic<bool> stop{false};std::atomic<unsigned> made{0},ready{0};
  std::vector<std::thread> threads;
  t::begin();
  for(int p=0;p<4;++p)threads.emplace_back([&,p]{
    uccl::urma_diag::identity(0,p);++ready;uint64_t i=0;
    while(!stop.load()){t::record("poll",++i);++made;}
  });
  while(ready.load()!=4 || made.load()<1000)std::this_thread::yield();
  for(int frame=0;frame<10;++frame){
    auto text=t::end();
    assert(text.find("CLOCK_MONOTONIC_RAW")!=std::string::npos);
    assert(!t::events.empty());
    for(auto const& e:t::events)assert(e.ns>=t::origin && e.proxy>=0 && e.proxy<4);
    if(frame<9){t::begin();auto before=made.load();while(made.load()<before+1000)std::this_thread::yield();}
  }
  stop=true;for(auto& thread:threads)thread.join();
  t::begin();assert(t::end().find("\"events\":[]")!=std::string::npos);
  uccl::urma_diag::identity(0,0);
  t::begin(true);
  for(int i=0;i<129;++i)t::poll_heartbeat();
  auto sampled=t::end();assert(sampled.find("\"heartbeat_stride\":128")!=std::string::npos);
  assert(t::events.size()==2 && std::string(t::events[0].stage)=="poll_heartbeat");
  t::begin();t::poll_heartbeat();assert(t::end().find("\"events\":[]")!=std::string::npos);
}
