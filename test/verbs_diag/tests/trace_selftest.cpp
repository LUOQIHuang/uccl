#include <urma_api.h>
#include <cassert>
#include <cerrno>
#include <cstdlib>
#include <dlfcn.h>
#include <string>
#include <thread>
#include <vector>
int main(int argc,char** argv) {
  assert(argc==4);
  setenv("UCCL_URMA_TRACE_REAL_LIB",argv[2],1);
  void* lib=dlopen(argv[1],RTLD_NOW|RTLD_LOCAL); assert(lib);
  auto ready=reinterpret_cast<int(*)()>(dlsym(lib,"uccl_trace_ready"));
  auto begin=reinterpret_cast<int(*)()>(dlsym(lib,"uccl_trace_begin"));
  auto end=reinterpret_cast<int(*)(const char*)>(dlsym(lib,"uccl_trace_end"));
  auto post=reinterpret_cast<urma_status_t(*)(urma_jetty_t*,urma_jfs_wr_t*,urma_jfs_wr_t**)>(dlsym(lib,"urma_post_jetty_send_wr"));
  auto poll=reinterpret_cast<int(*)(urma_jfc_t*,int,urma_cr_t*)>(dlsym(lib,"urma_poll_jfc"));
  auto reg=reinterpret_cast<urma_target_seg_t*(*)(urma_context_t*,urma_seg_cfg_t*)>(dlsym(lib,"urma_register_seg"));
  assert(ready && begin && end && post && poll && reg && ready());
  urma_seg_cfg_t cfg{}; cfg.va=4096; cfg.len=8192; cfg.is_gpu_seg=1; cfg.flag.bs.access=3;
  assert(reg(nullptr,&cfg));
  assert(begin()==0); assert(begin()!=0);
  std::vector<std::thread> threads;
  for(int t=0;t<4;++t) threads.emplace_back([=] {
    urma_sge_t src[2]{}, dst{};
    src[0].addr=4096; src[0].len=64; src[1].addr=8192; src[1].len=64;
    dst.addr=16384; dst.len=128;
    urma_jfs_wr_t write{},atomic{};
    write.opcode=URMA_OPC_WRITE; write.user_ctx=1024; write.next=&atomic;
    write.flag.bs.complete_enable=1; write.flag.bs.place_order=URMA_RELAX_ORDER;
    write.rw.src.sge=src; write.rw.src.num_sge=2; write.rw.dst.sge=&dst; write.rw.dst.num_sge=1;
    atomic.opcode=URMA_OPC_FADD; atomic.user_ctx=2048;
    atomic.flag.bs.complete_enable=1; atomic.flag.bs.fence=1; atomic.flag.bs.place_order=URMA_STRONG_ORDER;
    urma_cr_t cr[2]{}; urma_jfs_wr_t* bad=nullptr;
    assert(poll(nullptr,2,cr)==0);
    for(int i=0;i<1000;++i) {
      assert(post(nullptr,&write,&bad)==URMA_SUCCESS); assert(errno==EAGAIN);
      assert(write.next==&atomic && write.user_ctx==1024 && src[0].len==64);
      assert(poll(nullptr,1,cr)==1 && cr[0].user_ctx==1024);
      assert(poll(nullptr,2,cr)==1 && cr[0].user_ctx==2048);
      assert(poll(nullptr,2,cr)==0); assert(errno==EAGAIN);
    }
  });
  for(auto& thread:threads) thread.join();
  assert(end(argv[3])==0); assert(end(argv[3])!=0);
  // A new phase must not retain earlier threads' counters.
  assert(begin()==0);
  std::string empty=std::string(argv[3])+".empty";
  assert(end(empty.c_str())==0);
  // Reuse the same live caller/TLS slot across successive phases.
  for(int pass=0;pass<2;++pass) {
    assert(begin()==0);
    urma_sge_t src{},dst{}; src.addr=4096; src.len=64; dst.addr=8192; dst.len=64;
    urma_jfs_wr_t wr{}; wr.opcode=URMA_OPC_WRITE; wr.user_ctx=4096;
    wr.flag.bs.complete_enable=1; wr.flag.bs.place_order=URMA_RELAX_ORDER;
    wr.rw.src.sge=&src; wr.rw.src.num_sge=1; wr.rw.dst.sge=&dst; wr.rw.dst.num_sge=1;
    urma_jfs_wr_t* bad=nullptr; urma_cr_t cr{};
    assert(post(nullptr,&wr,&bad)==URMA_SUCCESS && poll(nullptr,1,&cr)==1);
    std::string name=std::string(argv[3])+".reuse"+std::to_string(pass);
    assert(end(name.c_str())==0);
  }
}
