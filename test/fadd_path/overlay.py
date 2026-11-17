"""Apply checked CPU-only probes to a separate, pinned diagnostic worktree."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

HERE=Path(__file__).resolve().parent
REPO=HERE.parents[1]
BASE='da574d61'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def replace(text, old, new, count=1):
    if text.count(old)!=count:
        raise ValueError('overlay anchor mismatch: '+old[:100])
    return text.replace(old,new)


def changes(path, source):
    if path=='ep/src/urma_transport.cpp':
        source=replace(source,'#include "urma_transport.hpp"','#include "urma_transport.hpp"\n#include "fadd_path_probe.hpp"')
        old='  int rc = urma_post_jetty_send_wr(jetties_[lane], &prepared_[0].wr, &bad);'
        new='''  auto fadd_stamp=uccl::fadd_path::post_begin();
  if(fadd_stamp.frame)for(auto const& w:prepared_){
    auto const& op=op_contexts_[w.context];
    uccl::fadd_path::issued(fadd_stamp,reinterpret_cast<uintptr_t>(jfc_),lane,op.wr_id,
      op.is_write,op.is_atomic,w.src.len,op.is_atomic?w.wr.faa.operand:0,
      op.is_atomic?reinterpret_cast<uint64_t*>(w.src.addr):nullptr,
      w.wr.flag.bs.fence,w.wr.flag.bs.place_order,ctx_->thread_idx);
  }
'''+old+'''
  uccl::fadd_path::post_end(fadd_stamp,rc);'''
        source=replace(source,old,new)
        source=replace(source,'    int n = urma_poll_jfc(jfc_,wanted,crs);',
            '''    auto fadd_cq=reinterpret_cast<uintptr_t>(jfc_);
    auto fadd_poll=uccl::fadd_path::poll_begin(fadd_cq);
    int n = urma_poll_jfc(jfc_,wanted,crs);
    auto fadd_return=uccl::fadd_path::poll_end(fadd_poll,fadd_cq,n);''')
        source=replace(source,'      uccl::transport_timeline::record("cqe",op.wr_id,op.lane,completion.status);',
            '''      uccl::fadd_path::completed(fadd_poll,fadd_cq,op.wr_id,fadd_return,completion.status);
      uccl::transport_timeline::record("cqe",op.wr_id,op.lane,completion.status);''')
    elif path=='ep/include/verbs_stage_diag.hpp':
        source=replace(source,'#include <map>','#include <map>\n#include "fadd_path_verbs.hpp"')
        source=replace(source,'inline int post(ibv_qp* qp,ibv_send_wr* wr,ibv_send_wr** bad) {',
            '''inline int post(ibv_qp* qp,ibv_send_wr* wr,ibv_send_wr** bad) {
  if(uccl::fadd_path::active.load(std::memory_order_relaxed))
    return uccl::fadd_path::verbs_post(qp,wr,bad,uccl::urma_diag::proxy);''')
        source=replace(source,'inline int poll(ibv_cq* cq,int max,ibv_wc* wc) {',
            '''inline int poll(ibv_cq* cq,int max,ibv_wc* wc) {
  if(uccl::fadd_path::active.load(std::memory_order_relaxed))return uccl::fadd_path::verbs_poll(cq,max,wc);''')
    elif path=='ep/src/uccl_ep.cc':
        source='#include "fadd_path_probe.hpp"\n'+source
        old='  m.def("get_dispatch_trace_version", []() { return 1; });'
        source=replace(source,old,old+'''
  m.def("get_fadd_path_version", [](){return 1;});
  m.def("fadd_path_begin", [](){uccl::fadd_path::begin(6);});
  m.def("fadd_path_end", [](){return uccl::fadd_path::end();});''')
    elif path in ('test/urma_mode_parity/support.py','test/verbs_baseline/common.py'):
        old="reference_events_version=extension.get_reference_events_version() if hasattr(extension,'get_reference_events_version') else 0,"
        source=replace(source,old,old+"\n                          fadd_path_version=extension.get_fadd_path_version() if hasattr(extension,'get_fadd_path_version') else 0,")
    elif path=='test/urma_mode_parity/driver.py':
        source=replace(source,'fingerprint=dict(job=args.job,backend=args.backend',
                       "fingerprint=dict(fadd_path_capture=os.environ.get('FADD_PATH_CAPTURE')=='1',job=args.job,backend=args.backend")
    elif path=='test/verbs_baseline/driver.py':
        source=replace(source,'meta = dict(job=args.job, variant=args.variant, identity=identity, benchmark=benchmark,',
                       "meta = dict(fadd_path_capture=os.environ.get('FADD_PATH_CAPTURE')=='1',job=args.job, variant=args.variant, identity=identity, benchmark=benchmark,")
    elif path=='test/reference_events/event_runtime.py':
        old='            begin.record();fn();end.record();torch.cuda.synchronize()'
        new='''            fadd_capture=os.environ.get('FADD_PATH_CAPTURE')=='1' and label=='dispatch_bf16_chunk32'
            if fadd_capture:
                if record_events or ep.get_fadd_path_version()!=1:raise RuntimeError('FADD_PATH_REQUIRES_CPU_ONLY_PROBE_MODULE')
                ep.fadd_path_begin()
            try:
                begin.record();fn();end.record();torch.cuda.synchronize()
            finally:
                if fadd_capture:append(case/'fadd_path.jsonl',dict(label=label,index=i,**json.loads(ep.fadd_path_end())))'''
        source=replace(source,old,new)
    else:
        raise ValueError('unrecognized overlay target '+path)
    return source


TARGETS=('ep/src/urma_transport.cpp','ep/include/verbs_stage_diag.hpp','ep/src/uccl_ep.cc',
         'test/urma_mode_parity/driver.py','test/verbs_baseline/driver.py',
         'test/urma_mode_parity/support.py','test/verbs_baseline/common.py','test/reference_events/event_runtime.py')


def probe_header(poll_stride):
    if poll_stride not in (1,64):
        raise ValueError('unsupported polling stride')
    return replace((HERE/'native/probe.hpp').read_text(),
                   'kStride=64, kLimit=4096', 'kStride=%d, kLimit=4096'%poll_stride)


def prepare(tree, poll_stride=64):
    header=probe_header(poll_stride)
    tree=tree.resolve()
    if tree==REPO or REPO in tree.parents:
        raise ValueError('diagnostic worktree must be outside the active checkout')
    bundle={p.relative_to(HERE).as_posix():sha(p) for p in HERE.rglob('*')
            if p.is_file() and p.suffix in ('.py','.hpp','.cpp','.sh') and not any(x in p.parts for x in ('results','__pycache__'))}
    version=subprocess.check_output(['git','-C',str(REPO),'rev-parse',BASE+'^{commit}']).decode().strip()
    marker=tree/'.fadd_path_overlay.json'
    if marker.exists():
        data=json.loads(marker.read_text())
        if data.get('poll_stride',64)!=poll_stride or data['base']!=version or data['bundle']!=bundle or any(sha(tree/n)!=v for n,v in data['overlaid'].items()):
            raise ValueError('DIAGNOSTIC_TREE_CHANGED: use a new worktree path')
        return data
    if tree.exists():
        raise ValueError('unmarked diagnostic worktree path already exists')
    # Construct/verify all replacements before creating or modifying a tree.
    sources={n:changes(n,subprocess.check_output(['git','-C',str(REPO),'show',version+':'+n]).decode()) for n in TARGETS}
    subprocess.run(['git','-C',str(REPO),'worktree','add','--detach',str(tree),version],check=True)
    for name,text in sources.items():(tree/name).write_text(text)
    (tree/'ep/include/fadd_path_probe.hpp').write_text(header)
    shutil.copy2(str(HERE/'native/verbs_hooks.hpp'),str(tree/'ep/include/fadd_path_verbs.hpp'))
    # Freeze this experiment's entry points with the build, independent of the
    # original checkout and of an in-progress tokens sweep.
    shutil.copytree(str(HERE),str(tree/'test/fadd_path'),ignore=shutil.ignore_patterns('results','__pycache__'))
    files=TARGETS+('ep/include/fadd_path_probe.hpp','ep/include/fadd_path_verbs.hpp')+tuple('test/fadd_path/'+n for n in bundle)
    data=dict(version=1,base=version,poll_stride=poll_stride,bundle=bundle,overlaid={n:sha(tree/n) for n in files},
              note='Only this isolated tree is overlaid; original benchmark and GPU kernels are unchanged.')
    marker.write_text(json.dumps(data,indent=2))
    return data
