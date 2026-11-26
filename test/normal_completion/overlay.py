"""Current Normal implementation plus read-only CPU completion observations."""
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
FADD = HERE.parent / 'fadd_path'
spec = importlib.util.spec_from_file_location('completion_legacy_overlay', FADD / 'overlay.py')
legacy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(legacy)
BASE = '0c818748'
MARKER = '.normal_completion_overlay.json'
replace = legacy.replace
sha = legacy.sha
TARGETS = ('ep/src/urma_transport.cpp', 'ep/src/uccl_ep.cc', 'ep/src/proxy.cpp',
           'test/urma_mode_parity/support.py', 'test/urma_mode_parity/driver.py',
           'test/urma_mode_parity/worker.py', 'ep/bench/test_internode.py',
           'test/reference_events/event_runtime.py', 'ep/setup.py', 'ep/tests/run_urma_tests.py')


def header():
    source = '#include "cq_ready_probe.hpp"\n' + legacy.probe_header(1)
    source = replace(source, 'struct Stream {',
        'struct Control {uint64_t id,begin,end;unsigned kind;};\nstruct Stream {')
    source = replace(source, '  std::vector<Pair> pairs;', '  std::vector<Control> controls;\n  std::vector<Pair> pairs;')
    source = replace(source, '  Stream(){pairs.reserve(16);', '  Stream(){controls.reserve(128);pairs.reserve(16);')
    source = replace(source, 's->pairs.clear();', 's->controls.clear();s->pairs.clear();')
    source = replace(source, 'inline void begin(unsigned ordinal){', '''struct ControlSpan {
  uint64_t frame=0,begin=0,id;unsigned kind;int proxy;
  ControlSpan(uint64_t wr,unsigned type,int thread):id(wr),kind(type),proxy(thread){
    if(active.load(std::memory_order_relaxed)){frame=epoch.load(std::memory_order_acquire);begin=now();}
  }
  ~ControlSpan(){
    if(!frame)return;
    auto ended=now();Guard guard(proxy);
    if(!guard.s || guard.s->frame!=frame)return;
    if(guard.s->controls.size()==128){++guard.s->dropped;return;}
    guard.s->controls.push_back({id,begin,ended,kind});
  }
};
inline void begin(unsigned ordinal){''')
    source = replace(source, '    out<<"],\\"polls\\":[";pc=false;', '''    out<<"],\\"controls\\":[";pc=false;
    for(auto const& c:s->controls){if(pc)out<<',';pc=true;
      out<<"{\\"wr_id\\":"<<c.id<<",\\"kind\\":"<<c.kind<<",\\"begin\\":"<<c.begin<<",\\"end\\":"<<c.end<<'}';}
    out<<"],\\"polls\\":[";pc=false;''')
    return replace(source, '  out<<"]}";return out.str();',
                   '  out<<"],\\"cq_ready\\":"<<uccl::cq_ready::dump(frame)<<"}";return out.str();')


def transform(name, source):
    if name in legacy.TARGETS and name != 'test/reference_events/event_runtime.py':
        source = legacy.changes(name, source)
    if name in ('test/urma_mode_parity/driver.py', 'test/urma_mode_parity/worker.py', 'ep/bench/test_internode.py'):
        source = legacy.tail_changes(name, source)
    if name in ('ep/src/urma_transport.cpp', 'ep/setup.py', 'ep/tests/run_urma_tests.py'):
        source = legacy.cq_changes(name, source)
    if name == 'ep/tests/run_urma_tests.py':
        source = replace(source, "str(ep/'tests/urma_proxy_plan_test.cpp'),'-o',str(proxy_test)]",
            "'-include',str(ep/'include/fadd_path_probe.hpp'),str(ep/'tests/urma_proxy_plan_test.cpp'),'-o',str(proxy_test)]")
    if name == 'ep/src/proxy.cpp':
        source = '#include "fadd_path_probe.hpp"\n' + source
        source = replace(source, '      ensure_urma_capacity(limit);',
            '      uccl::fadd_path::ControlSpan control_span(wrs_to_post[i],static_cast<unsigned>(type),cfg_.thread_idx);\n      ensure_urma_capacity(limit);')
    elif name == 'test/reference_events/event_runtime.py':
        # Keep the existing inactive paced timing boundary; no GPU capture or
        # host-clock worker. Both nodes collect CPU probes only in the probe case.
        source = replace(source, '        cpu_active=False;gpu=None',
                         '        cpu_active=False;gpu=None;fadd_capture=False;fadd_data=None')
        source = replace(source, '            begin.record();fn();end.record();torch.cuda.synchronize()',
            '''            fadd_capture=os.environ.get('FADD_PATH_CAPTURE')=='1' and label=='dispatch_bf16_chunk32'
            if fadd_capture:
                if record_events or ep.get_normal_completion_probe_version()!=1:
                    raise RuntimeError('NORMAL_COMPLETION_CPU_ONLY_MODULE_REQUIRED')
                ep.fadd_path_begin()
            begin.record();fn();end.record();torch.cuda.synchronize()''')
        source = replace(source, '                if cpu_active:ep.urma_timeline_end()',
            '                if fadd_capture:fadd_data=json.loads(ep.fadd_path_end())\n                if cpu_active:ep.urma_timeline_end()')
        source = replace(source, '        value=begin.elapsed_time(end)*1000',
            "        value=begin.elapsed_time(end)*1000\n        if fadd_data is not None:append(case/'fadd_path.jsonl',dict(label=label,index=i,us=value,**fadd_data))")
    elif name == 'ep/src/uccl_ep.cc':
        source = replace(source, '  m.def("get_fadd_path_version", [](){return 1;});',
            '  m.def("get_normal_completion_probe_version", [](){return 1;});\n  m.def("get_fadd_path_version", [](){return 1;});')
    elif name == 'test/urma_mode_parity/support.py':
        source = replace(source, 'fadd_path_version=extension.get_fadd_path_version()',
            'normal_completion_probe_version=extension.get_normal_completion_probe_version() if hasattr(extension,"get_normal_completion_probe_version") else 0,\n                          fadd_path_version=extension.get_fadd_path_version()')
    return source


def blocker_header(source):
    source = replace(source, '  std::vector<Control> controls;', '''  struct FifoObservation {uint64_t id, ns; unsigned kind; uint32_t bytes;};
  std::vector<FifoObservation> fifo;
  uint64_t peeked[3]={UINT64_MAX,UINT64_MAX,UINT64_MAX};
  unsigned visits[3]={};
  std::vector<Control> controls;''')
    source = replace(source, 'Stream(){controls.reserve(128);', 'Stream(){fifo.reserve(768);controls.reserve(128);')
    source = replace(source, 's->controls.clear();', '''s->fifo.clear();
      for(unsigned ring=0;ring<3;++ring){s->peeked[ring]=UINT64_MAX;s->visits[ring]=0;}
      s->controls.clear();''')
    source = replace(source, 'struct PostStamp {', '''// kind: 1 = collected, 2 = credit retired, 3 = ready while full.
inline void fifo_observe(uint64_t id,unsigned kind,uint32_t bytes,int proxy){
  if((id>>32)>=3 || !active.load(std::memory_order_relaxed))return;
  Guard guard(proxy);
  if(!guard.s)return;
  auto& s=*guard.s;
  if(s.fifo.size()>=768){++s.dropped;return;}
  s.fifo.push_back({id,now(),kind,bytes});
  if(kind==3)s.peeked[id>>32]=id;
}
inline bool should_peek(uint64_t id){
  auto ring=id>>32;
  if(ring>=3 || !active.load(std::memory_order_relaxed) || !local ||
     local->frame!=epoch.load(std::memory_order_relaxed))return false;
  return local->peeked[ring]!=id && (local->visits[ring]++ & 31u)==0;
}
struct PostStamp {''')
    source = replace(source, 'if(++it->writes==target){', 'if(++it->writes<=target){')
    source = replace(source, 'p.atomic_id=id+1;', 'p.atomic_id=UINT64_MAX;')
    source = replace(source, '(id==p.write_id || id==p.atomic_id)', 'id==p.write_id')
    source = replace(source, 'p.finished=p.a_cqe!=0;', 'p.finished=true;')
    # Keep observing the JFC after an early WRITE completes and before the
    # next target is posted so that consumer indices remain contiguous.
    source = replace(source, 'if(q==s.queues.end() || std::none_of(s.pairs.begin(),s.pairs.end(),[&](Pair const& p){return p.cq==cq && !p.finished;}))return {};',
                     'if(q==s.queues.end() || (s.pairs.size()>=target && std::all_of(s.pairs.begin(),s.pairs.end(),[](Pair const& p){return p.finished;})))return {};')
    source = replace(source, 'ordinal!=6', 'ordinal!=7')
    source = replace(source, 'nonoverlapping sixth-payload capture', 'nonoverlapping first-seven-WRITE capture')
    source = replace(source, r'\"target_payload\":6', r'\"target_payload\":7')
    source = replace(source, '    out<<"],\\"controls\\":[";pc=false;', '''    out<<"],\\"fifo\\":[";pc=false;
    for(auto const& e:s->fifo){if(pc)out<<',';pc=true;
      out<<"{\\"wr_id\\":"<<e.id<<",\\"ns\\":"<<e.ns<<",\\"kind\\":"<<e.kind<<",\\"bytes\\":"<<e.bytes<<'}';}
    out<<"],\\"controls\\":[";pc=false;''')
    return source


def compose(study='completion'):
    sources = {name: transform(name, subprocess.check_output(
        ['git', '-C', str(REPO), 'show', BASE + ':' + name]).decode('utf-8')) for name in TARGETS}
    sources['ep/include/fadd_path_probe.hpp'] = header()
    sources['ep/include/cq_ready_probe.hpp'] = (FADD / 'native/cq_ready.hpp').read_text(encoding='utf-8')
    plan_test = subprocess.check_output(['git', '-C', str(REPO), 'show', BASE + ':ep/tests/urma_proxy_plan_test.cpp']).decode('utf-8')
    plan_test = replace(plan_test, 'struct { bool use_normal_mode; } cfg_;',
        'struct { bool use_normal_mode; int thread_idx=0; } cfg_;')
    sources['ep/tests/urma_proxy_plan_test.cpp'] = plan_test
    if study == 'blockers':
        # fifo_stage_test.cpp also extracts from proxy.cpp (fifo_notify.inc) which
        # now contains fifo_observe calls referencing uccl::fadd_path and cfg_.thread_idx.
        # Add -include fadd_path_probe.hpp to the fifo_stage compile command and
        # add thread_idx=0 to the mock cfg_ struct.
        sources['ep/tests/run_urma_tests.py'] = replace(
            sources['ep/tests/run_urma_tests.py'],
            "str(ep/'tests/fifo_stage_test.cpp'),'-o',str(fifo_test)]",
            "'-include',str(ep/'include/fadd_path_probe.hpp'),str(ep/'tests/fifo_stage_test.cpp'),'-o',str(fifo_test)]")
        fifo_test_src = subprocess.check_output(['git', '-C', str(REPO), 'show', BASE + ':ep/tests/fifo_stage_test.cpp']).decode('utf-8')
        fifo_test_src = replace(fifo_test_src, 'struct {std::vector<Handle> d2h_queues;} cfg_;',
            'struct {std::vector<Handle> d2h_queues; int thread_idx=0;} cfg_;')
        sources['ep/tests/fifo_stage_test.cpp'] = fifo_test_src
        sources['ep/include/fadd_path_probe.hpp'] = blocker_header(sources['ep/include/fadd_path_probe.hpp'])
        sources['ep/src/uccl_ep.cc'] = replace(sources['ep/src/uccl_ep.cc'], 'uccl::fadd_path::begin(6)', 'uccl::fadd_path::begin(7)')
        sources['ep/src/uccl_ep.cc'] = replace(sources['ep/src/uccl_ep.cc'], 'get_normal_completion_probe_version", [](){return 1;}', 'get_normal_completion_probe_version", [](){return 2;}')
        runtime = 'test/reference_events/event_runtime.py'
        sources[runtime] = replace(sources[runtime], 'ep.get_normal_completion_probe_version()!=1', 'ep.get_normal_completion_probe_version()!=2')
        source = replace(sources['ep/src/proxy.cpp'], '      uccl::transport_timeline::record("ack_slot",front_wr);',
            '      uccl::fadd_path::fifo_observe(front_wr,2,0,cfg_.thread_idx);\n      uccl::transport_timeline::record("ack_slot",front_wr);')
        source = replace(source, '    // Pop commands from the FIFO until the budget is reached', '''    if (!budget && rb_idx < 3 && !ctx_.quiet_inflight && !ctx_.barrier_inflight) {
      auto next_wr=(uint64_t(rb_idx)<<32)|(fifo_seq_[rb_idx]&0xffffffffULL);
      // Fifo::poll is a read-only load. No pop or command decoding here.
      if(uccl::fadd_path::should_peek(next_wr) && fifo->poll().fst)
        uccl::fadd_path::fifo_observe(next_wr,3,0,cfg_.thread_idx);
    }
    // Pop commands from the FIFO until the budget is reached''')
        sources['ep/src/proxy.cpp'] = replace(source, '      fifo_pending_[rb_idx].push_back(', '''      uccl::fadd_path::fifo_observe(unique_wr_id,1,
          get_base_cmd(cmd.cmd_type)==CmdType::WRITE ? static_cast<uint32_t>(cmd.bytes) : 0,cfg_.thread_idx);
      fifo_pending_[rb_idx].push_back(''')
    return sources


def prepare(tree, study='completion'):
    tree = tree.resolve()
    if tree == REPO or REPO in tree.parents:
        raise ValueError('completion worktree must be outside active checkout')
    sources = compose(study)  # Check every anchor before creating a worktree.
    bundle = {p.relative_to(HERE).as_posix(): sha(p) for p in HERE.rglob('*')
              if p.is_file() and p.suffix in ('.py', '.sh', '.hpp', '.cpp')
              and not any(part in ('results', '__pycache__') for part in p.relative_to(HERE).parts)}
    expected = dict(version=1, base=subprocess.check_output(['git', '-C', str(REPO), 'rev-parse', BASE]).decode().strip(),
                    bundle=bundle, sources={n: hashlib.sha256(s.encode()).hexdigest() for n, s in sources.items()})
    if study == 'blockers':expected['study'] = study
    marker = tree / MARKER
    if marker.exists():
        saved = json.loads(marker.read_text())
        if saved != expected or any(sha(tree / n) != digest for n, digest in saved['sources'].items()) or any(
                sha(tree / 'test/normal_completion' / n) != digest for n, digest in saved['bundle'].items()):
            raise ValueError('COMPLETION_TREE_CHANGED: use new TREE/BUILD')
        return expected
    if tree.exists():
        raise ValueError('unmarked completion worktree exists')
    subprocess.run(['git', '-C', str(REPO), 'worktree', 'add', '--detach', str(tree), expected['base']], check=True)
    for name, source in sources.items():
        with (tree / name).open('w', encoding='utf-8', newline='\n') as stream:
            stream.write(source)
    shutil.copytree(str(HERE), str(tree / 'test/normal_completion'), ignore=shutil.ignore_patterns('results', '__pycache__'))
    marker.write_text(json.dumps(expected, indent=2))
    return expected
