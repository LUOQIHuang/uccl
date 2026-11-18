"""Pinned isolated source overlay. Original Normal and LL code stays untouched."""
from pathlib import Path
import hashlib,json,shutil,subprocess
HERE=Path(__file__).resolve().parent
REPO=HERE.parents[1]
BASE='fed2a437'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def replace(s,a,b,count=1):
    if s.count(a)!=count:raise ValueError('serial overlay anchor mismatch: '+a[:100])
    return s.replace(a,b)
def changes(name,s):
    if name in ('ep/include/urma_micro.hpp','ep/include/verbs_write_micro.hpp'):
        if 'urma_micro' in name:s=replace(s,'size_t max_bytes);','size_t max_bytes,bool serial=false);')
        else:s=replace(s,'bool notifications=false);','bool notifications=false,bool serial=false);')
        s=replace(s,'  void close();','  void serial_prepare(size_t pairs,int rank);\n  std::string serial_step(size_t seq,size_t pairs);\n  void serial_validate(size_t seq,size_t pairs);\n  void close();')
    elif name=='ep/src/urma_micro.cpp':
        s='#include "serial_pair.hpp"\n'+s
        s=replace(s,'  int notification_operation=-1;','  bool serial=false;\n  int notification_operation=-1;')
        s=replace(s,'int device,size_t threads,size_t max_bytes) {','int device,size_t threads,size_t max_bytes,bool serial) {')
        s=replace(s,'  p->main=uccl::gpu_p2p::allocate_device',
            '  uccl::serial_pair::require(!serial || (threads==1 && uccl::gpu_p2p::atomic_buffer_is_gpu_memory() && urma_normal_fadd_fence_from_env() && !urma_normal_tail_fence_off_from_env()),"serial requires GPU control, one thread and original FADD fence");\n  p->serial=serial;\n  p->main=uccl::gpu_p2p::allocate_device')
        s=replace(s,'transport->configure(true,8);','transport->configure(true,serial?1:8);')
        s=replace(s,'return "{\\"channels\\":8,','return "{\\"serial\\":"+std::string(impl_->serial?"true":"false")+",\\"channels\\":"+std::to_string(impl_->serial?1:8)+",')
        s=replace(s,'uint32_t(p.threads),8,uint32_t(window),uint32_t(p.threads*8*window)',
                      'uint32_t(p.threads),uint32_t(p.serial?1:8),uint32_t(window),uint32_t(p.threads*(p.serial?1:8)*window)')
        s=replace(s,'void UrmaMicroSession::close() {',(HERE/'native/urma_serial.inc').read_text()+'\nvoid UrmaMicroSession::close() {')
    elif name=='ep/src/verbs_write_micro.cpp':
        s='#include "serial_pair.hpp"\n'+s
        s=replace(s,'bool notifications=false,armed=false;','bool serial=false;bool notifications=false,armed=false;')
        s=replace(s,'size_t bytes,bool notifications) {','size_t bytes,bool notifications,bool serial) {')
        s=replace(s,'p->notifications=notifications;','p->notifications=notifications;p->serial=serial;\n  ok(!serial || (threads==1 && notifications),"serial requires one thread and native notifications");')
        s=replace(s,'threads==4 && uccl::gpu_p2p::atomic_buffer_is_gpu_memory()', '(serial?threads==1:threads==4) && uccl::gpu_p2p::atomic_buffer_is_gpu_memory()')
        s=replace(s,'lane<8;++lane){ibv_qp_', 'lane<(p->serial?1:8);++lane){ibv_qp_',2)
        # connect() holds a reference rather than the constructor's pointer.
        s=s.replace('lane<(p->serial?1:8);++lane){ibv_qp_attr','lane<(p.serial?1:8);++lane){ibv_qp_attr')
        s=replace(s,'\\"lanes_per_thread\\":8,','\\"lanes_per_thread\\":"+std::to_string(p.serial?1:8)+",\\"serial\\":"+std::string(p.serial?"true":"false")+",')
        s=replace(s,'p.g.lanes=8;', 'p.g.lanes=p.serial?1:8;')
        s=replace(s,'p.g.slots=p.threads*8*p.g.window;', 'p.g.slots=p.threads*p.g.lanes*p.g.window;')
        s=replace(s,'void VerbsWriteSession::close(){',(HERE/'native/verbs_serial.inc').read_text()+'\nvoid VerbsWriteSession::close(){')
    elif name=='ep/src/urma_transport.cpp':
        s='#include "serial_pair.hpp"\n'+s
        a='  int rc = urma_post_jetty_send_wr(jetties_[lane], &prepared_[0].wr, &bad);'
        b='''  auto serial_stamp=uccl::serial_pair::post_tick();
  if(serial_stamp)for(auto const& w:prepared_){auto const& op=op_contexts_[w.context];uccl::serial_pair::posted(op.wr_id,op.is_write,op.is_atomic,serial_stamp);}
'''+a+'\n  uccl::serial_pair::post_end(serial_stamp,rc);'
        s=replace(s,a,b)
        s=replace(s,'    int n = urma_poll_jfc(jfc_,wanted,crs);','    auto serial_seq=uccl::serial_pair::poll_begin();\n    int n = urma_poll_jfc(jfc_,wanted,crs);\n    auto serial_return=uccl::serial_pair::poll_return(serial_seq);')
        s=replace(s,'      uccl::transport_timeline::record("cqe",op.wr_id,op.lane,completion.status);','      uccl::serial_pair::completed(op.wr_id,serial_return,serial_seq,completion.status);\n      uccl::transport_timeline::record("cqe",op.wr_id,op.lane,completion.status);')
    elif name=='ep/src/uccl_ep.cc':
        s=replace(s,'  m.def("get_num_proxy_threads", []() { return kNumProxyThs; });','  m.def("get_serial_pair_version", []() { return 1; });\n  m.def("get_num_proxy_threads", []() { return kNumProxyThs; });')
        s=replace(s,'.def(nb::init<std::string const&,int,size_t,size_t,bool>())','.def(nb::init<std::string const&,int,size_t,size_t,bool>())\n    .def(nb::init<std::string const&,int,size_t,size_t,bool,bool>())')
        s=replace(s,'.def(nb::init<int,size_t,size_t>())','.def(nb::init<int,size_t,size_t>())\n      .def(nb::init<int,size_t,size_t,bool>())')
        for cls in ['VerbsWriteSession','UrmaMicroSession']:
            anchor='.def("metadata",&'+cls+'::metadata)'
            s=replace(s,anchor,anchor+'\n      .def("serial_prepare",&'+cls+'::serial_prepare)\n      .def("serial_step",[]('+cls+'& s,size_t seq,size_t count){nb::gil_scoped_release release;return s.serial_step(seq,count);})\n      .def("serial_validate",&'+cls+'::serial_validate)')
    else:raise ValueError(name)
    return s
TARGETS=('ep/include/urma_micro.hpp','ep/include/verbs_write_micro.hpp','ep/src/urma_micro.cpp',
         'ep/src/verbs_write_micro.cpp','ep/src/urma_transport.cpp','ep/src/uccl_ep.cc')
def prepare(tree):
    tree=tree.resolve()
    if tree==REPO or REPO in tree.parents:raise ValueError('serial worktree must be outside active repository')
    bundle={p.relative_to(HERE).as_posix():sha(p) for p in HERE.rglob('*') if p.is_file() and p.suffix in ('.py','.hpp','.inc','.cpp','.sh') and not any(x in p.parts for x in ('results','__pycache__'))}
    version=subprocess.check_output(['git','-C',str(REPO),'rev-parse',BASE+'^{commit}']).decode().strip()
    marker=tree/'.serial_pair_overlay.json'
    if marker.exists():
        d=json.loads(marker.read_text())
        if d['base']!=version or d['bundle']!=bundle or any(sha(tree/n)!=v for n,v in d['files'].items()):raise ValueError('SERIAL_TREE_CHANGED: use new TREE/BUILD paths')
        return d
    if tree.exists():raise ValueError('unmarked worktree exists')
    sources={n:changes(n,subprocess.check_output(['git','-C',str(REPO),'show',version+':'+n]).decode()) for n in TARGETS}
    subprocess.run(['git','-C',str(REPO),'worktree','add','--detach',str(tree),version],check=True)
    for n,s in sources.items():(tree/n).write_text(s)
    shutil.copy2(HERE/'native/common.hpp',tree/'ep/include/serial_pair.hpp')
    shutil.copytree(HERE,tree/'test/serial_pair',ignore=shutil.ignore_patterns('results','__pycache__'))
    files=TARGETS+('ep/include/serial_pair.hpp',)+tuple('test/serial_pair/'+n for n in bundle)
    d=dict(version=1,base=version,bundle=bundle,files={n:sha(tree/n) for n in files})
    marker.write_text(json.dumps(d,indent=2));return d
