"""Prepare the isolated Normal chunk-gate build without changing the main checkout."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
BASE = '31a1281a1f523f523d281082f357e3f156e39d89'
MARKER = '.normal_chunk_gate.json'
PROXY = 'ep/src/proxy.cpp'
BINDINGS = 'ep/src/uccl_ep.cc'
RUNTIME = 'test/reference_events/event_runtime.py'
HEADER = 'ep/include/normal_chunk_gate.hpp'


def replace(source, old, new):
    if source.count(old) != 1:
        raise ValueError(f'chunk-gate overlay anchor changed: {old!r}')
    return source.replace(old, new)


def compose():
    src = {name: subprocess.check_output(['git', '-C', str(REPO), 'show', f'{BASE}:{name}']).decode('utf-8')
           for name in (PROXY, BINDINGS, RUNTIME)}
    include = '#include "normal_chunk_gate.hpp"\n'
    src[PROXY] = include + replace(src[PROXY], '      uint64_t unique_wr_id = (static_cast<uint64_t>(rb_idx) << 32) |\n',
        '''      if (uccl::normal_chunk_gate::active.load(std::memory_order_relaxed) &&
          cfg_.use_normal_mode && use_urma_transport() &&
          get_base_cmd(cmd.cmd_type) == CmdType::WRITE && cmd.bytes >= 4096 &&
          uccl::normal_chunk_gate::hold(cfg_.thread_idx, rb_idx,
              (uint64_t(rb_idx) << 32) | (fifo_seq_[rb_idx] & 0xffffffffULL))) {
        // Leave this command at the FIFO head. The Proxy continues polling CQ
        // and servicing all its other channels on the regular progress loop.
        break;
      }
      uint64_t unique_wr_id = (static_cast<uint64_t>(rb_idx) << 32) |
''')
    bind_anchor = '  m.def("urma_dispatch_trial_end",[](){return uccl::dispatch_trial::end();});'
    src[BINDINGS] = include + replace(src[BINDINGS], bind_anchor, bind_anchor + '''
  m.def("normal_chunk_gate_begin", &uccl::normal_chunk_gate::begin);
  m.def("normal_chunk_gate_end", &uccl::normal_chunk_gate::end);''')
    runtime = replace(src[RUNTIME], '    capture=(capture_factory or Capture)(ep.__file__) if record_events else None', '''    gate_us=os.environ.get('NORMAL_CHUNK_GATE_US') if label=='dispatch_bf16_chunk32' else None
    if gate_us is not None and (record_events or selected_profile()!='legacy'):
        raise ValueError('chunk gate requires paced inactive reference events')
    capture=(capture_factory or Capture)(ep.__file__) if record_events else None''')
    runtime = replace(runtime, '        cpu_active=False;gpu=None;cpu=None',
                      '        cpu_active=False;gpu=None;cpu=None;gate_active=False;gate_data=None')
    runtime = replace(runtime, '            begin.record();fn();end.record();torch.cuda.synchronize()', '''            if gate_us is not None:
                ep.normal_chunk_gate_begin(int(gate_us))
                gate_active=True
            begin.record();fn();end.record();torch.cuda.synchronize()''')
    runtime = replace(runtime, '                if cpu_active:ep.urma_timeline_end()', '''                if gate_active:gate_data=json.loads(ep.normal_chunk_gate_end())
                if cpu_active:ep.urma_timeline_end()''')
    src[RUNTIME] = replace(runtime, '        value=begin.elapsed_time(end)*1000', '''        value=begin.elapsed_time(end)*1000
        if gate_data is not None:
            if len(gate_data['channels'])!=12:raise RuntimeError('missing chunk-gate channels')
            append(case/'chunk_gate.jsonl',dict(label=label,index=i,us=value,**gate_data))''')
    src[HEADER] = (HERE/'chunk_gate.hpp').read_text(encoding='utf-8')
    return {name: source.encode('utf-8') for name, source in src.items()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('tree', type=Path)
    tree = parser.parse_args().tree.resolve()
    if tree == REPO or REPO in tree.parents or tree in REPO.parents:
        parser.error('use an isolated source directory outside the active checkout')
    src = compose()
    sha = lambda data: hashlib.sha256(data).hexdigest()
    expected = dict(base=BASE, sources={name: sha(data) for name, data in src.items()})
    marker = tree/MARKER
    if not tree.exists():
        subprocess.run(['git', '-C', str(REPO), 'worktree', 'add', '--detach', str(tree), BASE], check=True)
        for name, data in src.items():
            (tree/name).write_bytes(data)
        marker.write_text(json.dumps(expected, indent=2)+'\n', encoding='utf-8')
    if (not marker.is_file() or json.loads(marker.read_text(encoding='utf-8')) != expected
            or any(sha((tree/name).read_bytes()) != digest for name, digest in expected['sources'].items())):
        raise ValueError('chunk-gate overlay changed; use a fresh tree and build directory')
    git = ['git', '-C', str(tree)]
    changed = subprocess.check_output(git+['status', '--porcelain', '-uall']).decode().splitlines()
    revision = subprocess.check_output(git+['rev-parse', 'HEAD']).decode().strip()
    if revision != BASE or {line[3:] for line in changed if line != f'?? {MARKER}'} != set(src):
        raise ValueError('chunk-gate tree contains other changes')
    print('NORMAL_CHUNK_GATE_TREE_READY')


if __name__ == '__main__':
    main()
