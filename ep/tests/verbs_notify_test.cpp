// Real verbs declarations and production micro implementation; simulated NIC/GPU boundaries.
#include <infiniband/verbs.h>
#include "urma_micro.hpp"
#include "gpu_p2p_runtime.hpp"
#include <cassert>
#include <cstdlib>
#include <deque>
#include <map>
#include <thread>
#include <iostream>
#include <atomic>
#include <cstring>
#include <sched.h>
namespace fake {
struct Cq {std::deque<ibv_wc> wc;};
std::map<uint32_t,ibv_qp*> qps;uint32_t next_qp=1,next_key=1;ibv_device device{};ibv_device* devices[]={&device,nullptr};
int post(ibv_qp* q,ibv_send_wr* w,ibv_send_wr**){
 for(;w;w=w->next){assert(w->send_flags==IBV_SEND_SIGNALED && w->num_sge==1);
  auto& sg=w->sg_list[0];ibv_wc wc{};wc.wr_id=w->wr_id;wc.qp_num=q->qp_num;wc.status=IBV_WC_SUCCESS;
  if(w->opcode==IBV_WR_RDMA_WRITE){std::memcpy(reinterpret_cast<void*>(w->wr.rdma.remote_addr),reinterpret_cast<void*>(sg.addr),sg.length);wc.opcode=IBV_WC_RDMA_WRITE;}
  else{assert(w->opcode==IBV_WR_ATOMIC_FETCH_AND_ADD && sg.length==8);auto old=__atomic_fetch_add(reinterpret_cast<uint64_t*>(w->wr.atomic.remote_addr),w->wr.atomic.compare_add,__ATOMIC_RELEASE);std::memcpy(reinterpret_cast<void*>(sg.addr),&old,8);wc.opcode=IBV_WC_FETCH_ADD;}
  static_cast<Cq*>(q->send_cq->cq_context)->wc.push_back(wc);
 }return 0;
}
int poll(ibv_cq* q,int n,ibv_wc* wc){auto& d=static_cast<Cq*>(q->cq_context)->wc;int i=0;while(i<n && !d.empty()){wc[i++]=d.front();d.pop_front();}return i;}
ibv_device** get(int* n){*n=1;return devices;}void free_devices(ibv_device**){}
char const* name(ibv_device*){return "fake";}
ibv_context* open(ibv_device*){auto* c=new ibv_context{};c->ops.post_send=post;c->ops.poll_cq=poll;return c;}
int close(ibv_context* c){delete c;return 0;}
int port(ibv_context*,uint8_t,ibv_port_attr* p){p->state=IBV_PORT_ACTIVE;p->gid_tbl_len=4;p->active_mtu=IBV_MTU_1024;return 0;}
int gid(ibv_context*,uint8_t,int,ibv_gid* g){*g={};g->raw[15]=1;return 0;}
int caps(ibv_context*,ibv_device_attr* a){a->atomic_cap=IBV_ATOMIC_HCA;return 0;}
ibv_pd* pd(ibv_context* c){auto* p=new ibv_pd{};p->context=c;return p;}int free_pd(ibv_pd* p){delete p;return 0;}
ibv_mr* mr(ibv_pd* p,void* ptr,size_t bytes,int){auto* m=new ibv_mr{};m->pd=p;m->addr=ptr;m->length=bytes;m->lkey=next_key++;m->rkey=next_key++;return m;}
int free_mr(ibv_mr* m){delete m;return 0;}
ibv_cq* cq(ibv_context* c,int,void*,ibv_comp_channel*,int){auto* q=new ibv_cq{};q->context=c;q->cq_context=new Cq;return q;}int free_cq(ibv_cq* q){assert(static_cast<Cq*>(q->cq_context)->wc.empty());delete static_cast<Cq*>(q->cq_context);delete q;return 0;}
ibv_qp* qp(ibv_pd* p,ibv_qp_init_attr* a){auto* q=new ibv_qp{};q->context=p->context;q->qp_num=next_qp++;q->send_cq=a->send_cq;qps[q->qp_num]=q;return q;}
int modify(ibv_qp*,ibv_qp_attr*,int){return 0;}int free_qp(ibv_qp* q){qps.erase(q->qp_num);delete q;return 0;}
unsigned char pattern(int rank,int parity,size_t slot){return (rank?0xa7:0x35)^(parity?0x5a:0)^(slot%64);}
struct Observer{uint64_t* control;size_t lanes;uint64_t count;};
}
namespace uccl::gpu_p2p {
bool atomic_buffer_is_gpu_memory(){return true;}void set_device(int){}
void* allocate_device(size_t n,int){void* p=nullptr;assert(posix_memalign(&p,4096,n)==0);return p;}
void* allocate_atomic_buffer(size_t n,int d){return allocate_device(n,d);}
void prepare_main_buffer(void*,size_t,int){}void prepare_atomic_buffer(void*,size_t,int){}
void zero_memory(void* p,size_t n,MemoryKind,int){std::memset(p,0,n);}
void copy_device_to_host(void* h,void const* d,size_t n,int){std::memcpy(h,d,n);}
void release_memory(void* p,MemoryKind,int){std::free(p);}
}
void urma_micro_gpu_prepare(void* ptr,UrmaMicroGeometry const& g,int rank){auto* p=static_cast<unsigned char*>(ptr);for(size_t i=0;i<g.slots;++i){for(int b=0;b<2;++b)std::memset(p+b*g.region+i*g.stride+g.src_pad,fake::pattern(rank,b,i),g.bytes);std::memset(p+2*g.region+i*g.stride+g.dst_pad,0,g.bytes);}}
bool urma_micro_gpu_validate(void* ptr,void* control,UrmaMicroGeometry const& g,int rank,int parity,uint64_t count){auto* p=static_cast<unsigned char*>(ptr);auto* c=static_cast<uint64_t*>(control);for(size_t i=0;i<g.slots;++i){if(c[i/g.window]!=count)return false;for(size_t j=0;j<g.bytes;++j)if(p[2*g.region+i*g.stride+g.dst_pad+j]!=fake::pattern(rank,parity,i))return false;}return true;}
void* urma_micro_observer_start(void*,void* c,UrmaMicroGeometry const& g,int,int,uint64_t count,bool scan){assert(!scan);return new fake::Observer{static_cast<uint64_t*>(c),g.threads*g.lanes,count};}
bool urma_micro_observer_done(void* h,size_t first,size_t n){auto* o=static_cast<fake::Observer*>(h);assert(first+n<=o->lanes);for(size_t i=first;i<first+n;++i)if(__atomic_load_n(o->control+i,__ATOMIC_ACQUIRE)<o->count)return false;return true;}
void urma_micro_observer_finish(void* h){delete static_cast<fake::Observer*>(h);}
ibv_mr* reg_mr_gpu_dmabuf(ibv_pd* p,void* b,size_t n,uint64_t,int access){return fake::mr(p,b,n,access);}
ibv_mr* register_control_buffer_mr(ibv_pd* p,void* b,size_t n,int access){assert(access&IBV_ACCESS_REMOTE_ATOMIC);return fake::mr(p,b,n,access);}
#define ibv_get_device_list fake::get
#define ibv_free_device_list fake::free_devices
#define ibv_get_device_name fake::name
#define ibv_open_device fake::open
#define ibv_close_device fake::close
#undef ibv_query_port
#define ibv_query_port fake::port
#define ibv_query_gid fake::gid
#define ibv_query_device fake::caps
#define ibv_alloc_pd fake::pd
#define ibv_dealloc_pd fake::free_pd
#undef ibv_reg_mr
#define ibv_reg_mr fake::mr
#define ibv_dereg_mr fake::free_mr
#define ibv_create_cq fake::cq
#define ibv_destroy_cq fake::free_cq
#define ibv_create_qp fake::qp
#define ibv_modify_qp fake::modify
#define ibv_destroy_qp fake::free_qp
#include "../src/verbs_write_micro.cpp"
int main(){
 cpu_set_t allowed;CPU_ZERO(&allowed);assert(sched_getaffinity(0,sizeof(allowed),&allowed)==0);std::vector<int> cpus;for(int i=0;i<CPU_SETSIZE && cpus.size()<4;++i)if(CPU_ISSET(i,&allowed))cpus.push_back(i);assert(cpus.size()==4);
 VerbsWriteSession a("fake",3,4,8192,true),b("fake",3,4,8192,true);std::vector<std::string> ad,bd;for(int i=0;i<4;++i){ad.push_back(a.descriptor(i));bd.push_back(b.descriptor(i));}a.connect(bd);b.connect(ad);a.experiment(true,true,cpus);b.experiment(true,true,cpus);
 for(int mode=0;mode<4;++mode){a.prepare(8192,12,20,0);b.prepare(8192,12,20,1);int op=mode==0?0:mode==1?1:2;
  for(size_t seq=0;seq<3;++seq){a.arm(seq,op,mode==3);b.arm(seq,op,mode==3);std::string x,y;std::thread t([&]{x=a.step(seq);});std::thread u([&]{y=b.step(seq);});t.join();u.join();a.validate(seq);b.validate(seq);
   for(auto const& r:{x,y}){assert(r.find(op==2?"\"cqes\":64":"\"cqes\":32")!=std::string::npos);assert(r.find(op==1?"\"write_bytes\":0":"\"write_bytes\":262144")!=std::string::npos);}
  }
 }
 a.close();b.close();
 for(size_t threads:{size_t(1),size_t(4)}){
  VerbsWriteSession c("fake",3,threads,8192),d("fake",3,threads,8192);std::vector<std::string> cd,dd;
  for(size_t i=0;i<threads;++i){cd.push_back(c.descriptor(i));dd.push_back(d.descriptor(i));}c.connect(dd);d.connect(cd);
  std::vector<int> workers(cpus.begin(),cpus.begin()+threads);c.experiment(true,true,workers);d.experiment(true,true,workers);c.prepare(8192,12,20,0);d.prepare(8192,12,20,1);
  for(size_t seq=0;seq<2;++seq){auto x=c.step(seq);auto y=d.step(seq);c.validate(seq);d.validate(seq);for(auto const& r:{x,y})assert(r.find("\"cqes\":128")!=std::string::npos);}
  c.close();d.close();
 }
 assert(fake::qps.empty());std::cout<<"Native verbs WRITE/FADD/GPU-flag control flow passed (simulated boundaries)\n";
}
