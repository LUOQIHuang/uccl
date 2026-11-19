"""Apply checked CPU-only probes to a separate, pinned diagnostic worktree."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

HERE=Path(__file__).resolve().parent
REPO=HERE.parents[1]
BASE='da574d61'
TAIL_BASE='d822cf31'
BF16_LABEL='dispatch_bf16_chunk32'


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


def tail_changes(path, source):
    if path=='test/reference_events/event_runtime.py':
        source=replace(source,'    capture=(capture_factory or Capture)(ep.__file__) if record_events else None',
            '''    tail_probe=os.environ.get('FADD_PATH_TAIL_SCOPE')=='1' and os.environ.get('FADD_PATH_CAPTURE')=='1'
    receiver=tail_probe and dist.get_rank()==0
    capture=(capture_factory or Capture)(ep.__file__) if record_events or receiver else None''')
        source=replace(source,'        cpu_active=False;gpu=None',
                              '        cpu_active=False;gpu=None;fadd_capture=False')
        source=replace(source,'            if capture is not None:\n',
                              '            if capture is not None and not receiver:\n')
        source=replace(source,"            fadd_capture=os.environ.get('FADD_PATH_CAPTURE')=='1' and label=='dispatch_bf16_chunk32'",
            "            fadd_capture=os.environ.get('FADD_PATH_CAPTURE')=='1' and label=='dispatch_bf16_chunk32' and not receiver")
        source=replace(source,"                if fadd_capture:append(case/'fadd_path.jsonl',dict(label=label,index=i,**json.loads(ep.fadd_path_end())))",
            "                if fadd_capture and not tail_probe:append(case/'fadd_path.jsonl',dict(label=label,index=i,**json.loads(ep.fadd_path_end())))")
        source=replace(source,'                if capture is not None:gpu=capture.end()',
            '''                if capture is not None:gpu=capture.end()
                if tail_probe and not receiver and fadd_capture:
                    append(case/'fadd_path.jsonl',dict(label=label,index=i,**json.loads(ep.fadd_path_end())))''')
        source=replace(source,"        if capture is not None:append(case/'event_trace.jsonl',dict(label=label,index=i,us=value,gpu=gpu,cpu=cpu))",
            "        if receiver:append(case/'tail_receiver.jsonl',dict(label=label,index=i,us=value,gpu=gpu))\n"
            "        elif capture is not None:append(case/'event_trace.jsonl',dict(label=label,index=i,us=value,gpu=gpu,cpu=cpu))")
        return single_control_label(source)
    if path=='test/urma_mode_parity/driver.py':
        source=replace(source,"    error=None; fingerprint=dict(fadd_path_capture=", "    tail_scope=os.environ.get('FADD_PATH_TAIL_SCOPE')=='1'\n    error=None; fingerprint=dict(fadd_tail_scope=tail_scope,fadd_path_capture=")
        source=replace(source,"        expected_points=4 if mode=='normal' else 2", "        expected_points=(1 if tail_scope else 4) if mode=='normal' else 2")
        source=replace(source,"profiles=text.count(point_marker)==4,tuning=text.count('[tuning] SMs ')==4)","profiles=text.count(point_marker)==expected_points,tuning=text.count('[tuning] SMs ')==(0 if tail_scope else 4))")
        source=replace(source,"validate_control(case,args.reference_events,args.warmups,args.samples,args.reference_profile)","validate_control(case,args.reference_events,args.warmups,args.samples,args.reference_profile,\n                                                          labels={'dispatch_bf16_chunk32'} if tail_scope else None)")
        source=replace(source,"and not any(r=='unknown' for r,f in roles)",
                              "and not any(r=='unknown' for r,f in roles)\n                    if tail_scope:checks['normal_atomic_roles'] &= observed_roles=={'tail','head'}")
        return replace(source,"text.count('MODE_PARITY_SAMPLES ')==(5 if mode=='normal'", "text.count('MODE_PARITY_SAMPLES ')==((1 if tail_scope else 5) if mode=='normal'")
    if path=='test/urma_mode_parity/worker.py':
        source=replace(source,"normal_plan=[('dispatch_fp8',32),('dispatch_bf16',32),('combine',12),('combine',16)]",
            "normal_plan=([('dispatch_bf16',32)] if os.environ.get('FADD_PATH_TAIL_SCOPE')=='1' else\n             [('dispatch_fp8',32),('dispatch_bf16',32),('combine',12),('combine',16)])")
        source=replace(source,"index==(3 if mode=='normal' else 1)","index==(len(normal_plan)-1 if mode=='normal' else 1)")
        return replace(source,"assert count==(4 if mode=='normal' else 2),count", "assert count==(len(normal_plan) if mode=='normal' else 2),count")
    if path=='ep/bench/test_internode.py':
        # Keep the real correctness matrix and the same dispatch handle; omit
        # the layout timer and the other three performance points.
        begin=source.index('    t = bench(lambda: buffer.get_dispatch_layout')
        end=source.index('    group.barrier()\n',begin)
        source=source[:begin]+source[end:]
        begin=source.index('    # Tune dispatch performance\n')
        # Keep the original tuning-loop anchors for prepare_benchmark(), which
        # also records the benchmark fingerprint. The early return skips them.
        focused='''    if os.environ.get('FADD_PATH_TAIL_SCOPE')=='1':
        config = Config(num_sms, 4, nvl_buffer_size, 32, rdma_buffer_size)
        bench_kineto(lambda: buffer.dispatch(x=x, handle=handle, config=config), ("dispatch", "notify"))
        return hash_value

'''
        return source[:begin]+focused+source[begin:]
    if path=='test/reference_events/run.sh':
        source=replace(source,'GID=3 PROFILE=legacy','GID=3 PROFILE=legacy FENCE=default')
        source=replace(source,' --reference-profile) PROFILE=${2:?};shift 2;;',
                              ' --reference-profile) PROFILE=${2:?};shift 2;;\n --normal-fadd-fence-plan) FENCE=${2:?};shift 2;;')
        return replace(source,'else\n ENTRY="$HERE/../verbs_baseline/run.sh"',
            ''' if [[ "$ACTION" != build && "$FENCE" != default ]];then
  COMMON+=(--normal-fadd-fence-plan "$FENCE" --require-normal-atomic-roles)
 fi
else
 ENTRY="$HERE/../verbs_baseline/run.sh"''')
    return source


def single_control_label(source):
    source=replace(source,"def validate_control(case,mode,warmups,samples,profile='legacy'):",
                          "def validate_control(case,mode,warmups,samples,profile='legacy',labels=None):")
    return replace(source,'for l in sorted(LABELS)]','for l in sorted(LABELS if labels is None else labels)]')


def prepare(tree, poll_stride=64, tail_scope=False):
    header=probe_header(poll_stride)
    tree=tree.resolve()
    if tree==REPO or REPO in tree.parents:
        raise ValueError('diagnostic worktree must be outside the active checkout')
    bundle={p.relative_to(HERE).as_posix():sha(p) for p in HERE.rglob('*')
            if p.is_file() and p.suffix in ('.py','.hpp','.cpp','.sh') and not any(x in p.parts for x in ('results','__pycache__'))}
    version=subprocess.check_output(['git','-C',str(REPO),'rev-parse',(TAIL_BASE if tail_scope else BASE)+'^{commit}']).decode().strip()
    marker=tree/'.fadd_path_overlay.json'
    if marker.exists():
        data=json.loads(marker.read_text())
        if data.get('poll_stride',64)!=poll_stride or data.get('tail_scope',False)!=tail_scope or data['base']!=version or data['bundle']!=bundle or any(sha(tree/n)!=v for n,v in data['overlaid'].items()):
            raise ValueError('DIAGNOSTIC_TREE_CHANGED: use a new worktree path')
        return data
    if tree.exists():
        raise ValueError('unmarked diagnostic worktree path already exists')
    # Construct/verify all replacements before creating or modifying a tree.
    targets=TARGETS+('test/urma_mode_parity/worker.py','ep/bench/test_internode.py','test/reference_events/run.sh') if tail_scope else TARGETS
    sources={}
    for name in targets:
        source=subprocess.check_output(['git','-C',str(REPO),'show',version+':'+name]).decode()
        if name in TARGETS:source=changes(name,source)
        sources[name]=tail_changes(name,source) if tail_scope else source
    subprocess.run(['git','-c','core.longpaths=true','-C',str(REPO),'worktree','add','--detach',str(tree),version],check=True)
    for name,text in sources.items():(tree/name).write_text(text,encoding='utf-8',newline='\n')
    (tree/'ep/include/fadd_path_probe.hpp').write_text(header)
    shutil.copy2(str(HERE/'native/verbs_hooks.hpp'),str(tree/'ep/include/fadd_path_verbs.hpp'))
    # Freeze this experiment's entry points with the build, independent of the
    # original checkout and of an in-progress tokens sweep.
    shutil.copytree(str(HERE),str(tree/'test/fadd_path'),ignore=shutil.ignore_patterns('results','__pycache__'),dirs_exist_ok=tail_scope)
    files=targets+('ep/include/fadd_path_probe.hpp','ep/include/fadd_path_verbs.hpp')+tuple('test/fadd_path/'+n for n in bundle)
    data=dict(version=1,base=version,poll_stride=poll_stride,bundle=bundle,overlaid={n:sha(tree/n) for n in files},
              note='Only this isolated tree is overlaid; original benchmark and GPU kernels are unchanged.')
    if tail_scope:data.update(tail_scope=True,note='Isolated BF16 harness with rank0 receiver events and rank1 completion probe. GPU kernels are unchanged.')
    marker.write_text(json.dumps(data,indent=2))
    return data
