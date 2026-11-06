// Production micro driver + production transport, simulated SDK/GPU boundary.
#include "urma_micro.hpp"
#include "gpu_p2p_runtime.hpp"
#include "urma_layout_diag.hpp"
#include <future>
#include <sched.h>
std::pair<void*,std::shared_ptr<void>> allocate_urma_atomic_control(int device) {
  void* p=uccl::gpu_p2p::allocate_atomic_buffer(81960,device);
  return {p,UrmaTransport::acquire_buffer_ownership(p,true,device)};
}
unsigned char test_pattern(int rank,int parity,size_t slot) {
  return static_cast<unsigned char>((rank?0xa7:0x35)^(parity?0x5a:0)^(slot%64));
}
void urma_micro_gpu_prepare(void* base,UrmaMicroGeometry const& g,int rank) {
  auto* b=static_cast<unsigned char*>(base);
  for(size_t slot=0;slot<g.slots;++slot) {
    std::memset(b+slot*g.stride+g.src_pad,test_pattern(rank,0,slot),g.bytes);
    std::memset(b+g.region+slot*g.stride+g.src_pad,test_pattern(rank,1,slot),g.bytes);
    std::memset(b+2*g.region+slot*g.stride+g.dst_pad,0,g.bytes);
  }
}
bool urma_micro_gpu_validate(void* base,void* control,UrmaMicroGeometry const& g,int rank,int parity,uint64_t count) {
  auto* b=static_cast<unsigned char*>(base);
  auto* c=static_cast<uint64_t*>(control);
  for(size_t slot=0;slot<g.slots;++slot) {
    if(c[slot/g.window]!=count)return false;
    for(size_t i=0;i<g.bytes;++i)if(b[2*g.region+slot*g.stride+g.dst_pad+i]!=test_pattern(rank,parity,slot))return false;
  }
  return true;
}
struct FakeObserver {void* base;void* control;UrmaMicroGeometry g;int rank,parity;uint64_t count;};
void* urma_micro_observer_start(void* b,void* c,UrmaMicroGeometry const& g,int rank,int parity,uint64_t count,bool) {
  return new FakeObserver{b,c,g,rank,parity,count};
}
bool urma_micro_observer_done(void* p,size_t first,size_t lanes) {
  std::lock_guard<std::mutex> lock(micro_wire_mutex);
  auto& o=*static_cast<FakeObserver*>(p);
  auto* c=static_cast<uint64_t*>(o.control);
  for(size_t i=first;i<first+lanes;++i)if(c[i]<o.count)return false;
  return true;
}
void urma_micro_observer_finish(void* p){delete static_cast<FakeObserver*>(p);}
std::string urma_micro_gpu_local_scan(void*,UrmaMicroGeometry const&,int,int){return "{\"gpu_us\":1}";}
int main() {
  unsetenv("UCCL_URMA_NORMAL_COMPLETION");unsetenv("UCCL_URMA_LL_WRITE_ORDER");
  for(size_t threads:{1U,4U}) {
    UrmaMicroSession a(2,threads,128),b(2,threads,128);
    std::vector<RemoteConnInfo> ai,bi;
    for(size_t i=0;i<threads;++i){ai.push_back(a.descriptor(i));bi.push_back(b.descriptor(i));}
    a.connect(bi);b.connect(ai);
    for(size_t window:{1U,4U})for(int kind:{0,1,2}) {
      if(kind==2 && window!=1)continue;
      bool fadd=kind!=0,visible=kind==2;size_t messages=visible?8:32;
      a.prepare(128,12,20,window,0);b.prepare(128,12,20,window,1);
      for(size_t seq=0;seq<2;++seq) {
        bool capture=threads==1 && window==1 && kind==0 && seq==0;
        if(capture)uccl::urma_layout::begin();
        a.arm(seq,visible,fadd,messages);b.arm(seq,visible,fadd,messages);
        auto left=std::async(std::launch::async,[&]{return a.step(seq,fadd,messages);});
        auto right=std::async(std::launch::async,[&]{return b.step(seq,fadd,messages);});
        assert(left.get().find("\"span_ns\":")!=std::string::npos);right.get();
        a.validate(seq,fadd,messages);b.validate(seq,fadd,messages);
        if(capture) {
          auto layout=uccl::urma_layout::end();
          assert(layout.find("\"bytes\":128,\"src_mod\":12,\"dst_mod\":20")!=std::string::npos);
          assert(layout.find("\"count\":8")!=std::string::npos);
        }
      }
    }
    cpu_set_t available;CPU_ZERO(&available);assert(sched_getaffinity(0,sizeof(available),&available)==0);
    std::vector<int> cpus;
    for(int cpu=0;cpu<CPU_SETSIZE && cpus.size()<threads;++cpu)if(CPU_ISSET(cpu,&available))cpus.push_back(cpu);
    assert(cpus.size()==threads);
    for(int direction:{0,1,2})for(int kind:{0,1,2}) {
      bool send_a=direction!=1,send_b=direction!=0,fadd=kind!=0;
      size_t messages=fadd?8:128/threads,window=fadd?1:4;
      a.prepare(128,12,20,window,0);b.prepare(128,12,20,window,1);
      a.experiment(send_a,send_b,kind==2,cpus);b.experiment(send_b,send_a,kind==2,cpus);
      for(size_t seq=0;seq<2;++seq) {
        a.arm(seq,fadd,fadd,messages);b.arm(seq,fadd,fadd,messages);
        auto left=std::async(std::launch::async,[&]{return a.step(seq,fadd,messages);});
        auto right=std::async(std::launch::async,[&]{return b.step(seq,fadd,messages);});
        left.get();right.get();a.validate(seq,fadd,messages);b.validate(seq,fadd,messages);
      }
    }
    a.prepare(128,12,20,1,0);assert(a.local_scan(0).find("gpu_us")!=std::string::npos);
    a.close();b.close();
    assert(resources==0 && gpu_regs==0 && micro_allocations.empty());
  }
  std::cout<<"URMA production micro driver tests passed (simulated GPU/SDK boundary)\n";
}
