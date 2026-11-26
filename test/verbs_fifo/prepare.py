"""Build a detached, reproducible RDMA FIFO probe source tree."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess

REPO = Path(__file__).resolve().parents[2]
BASE = 'e01a6f32d0f8a4b8aa547ec9c53c35d279e113ba'
MARKER = '.verbs_fifo_probe.json'
PROXY = 'ep/src/proxy.cpp'
RUNTIME = 'test/reference_events/event_runtime.py'


def replace(source, old, new):
    if source.count(old) != 1:
        raise ValueError(f'FIFO overlay anchor changed: {old!r}')
    return source.replace(old, new)


def compose():
    proxy = subprocess.check_output(['git', '-C', str(REPO), 'show', f'{BASE}:{PROXY}']).decode('utf-8')
    proxy = replace(proxy, '    // Pop commands from the FIFO until the budget is reached', '''#ifdef UCCL_REFERENCE_EVENTS
    if (cfg_.use_normal_mode && !budget && rb_idx < 3 && !ctx_.quiet_inflight &&
        !ctx_.barrier_inflight && uccl::transport_timeline::active.load(std::memory_order_relaxed)) {
      // Read only. This uses the same sampling stride as the URMA blockers probe.
      static thread_local uint64_t frame = 0;
      static thread_local uint64_t observed[3];
      static thread_local unsigned visits[3];
      const auto current = uccl::transport_timeline::capture_epoch.load(std::memory_order_relaxed);
      if (frame != current) {
        frame = current;
        for (unsigned ring = 0; ring < 3; ++ring) {
          observed[ring] = UINT64_MAX;
          visits[ring] = 0;
        }
      }
      const auto next_wr = (uint64_t(rb_idx) << 32) | (fifo_seq_[rb_idx] & 0xffffffffULL);
      if (observed[rb_idx] != next_wr && (visits[rb_idx]++ & 31u) == 0 && fifo->poll().fst) {
        uccl::transport_timeline::record("fifo_full_ready", next_wr);
        observed[rb_idx] = next_wr;
      }
    }
#endif
    // Pop commands from the FIFO until the budget is reached''')
    runtime = (REPO / RUNTIME).read_text(encoding='utf-8')
    runtime = replace(runtime, '    cpu_events=os.environ.get(', '''    fifo_cpu=os.environ.get('VERBS_FIFO_CAPTURE')=='1' and label=='dispatch_bf16_chunk32'
    if fifo_cpu and (record_events or selected_profile()!='boundaries'):
        raise ValueError('FIFO capture requires inactive paced reference events')
    cpu_events=os.environ.get(''')
    runtime = replace(runtime, '                cpu_active=True\n', '''                cpu_active=True
            if fifo_cpu:
                ep.urma_timeline_begin()
                cpu_active=True
''')
    runtime = replace(runtime, "        append(case/'api_samples.jsonl'", """        if fifo_cpu:
            append(case/'fifo_trace.jsonl',dict(label=label,index=i,us=value,cpu=cpu))
        append(case/'api_samples.jsonl'""")
    runtime = replace(runtime, "    if not record_events:control_marker(case,label,'paced',warmups,samples)", '''    if not record_events:control_marker(case,label,'paced',warmups,samples,fifo_cpu=fifo_cpu)''')
    runtime = replace(runtime, 'def control_marker(case,label,mode,warmups,samples):',
                      'def control_marker(case,label,mode,warmups,samples,fifo_cpu=False):')
    runtime = replace(runtime, 'gpu_events=False,cpu_events=False,host_clock=False)\n    if PROFILE_NAMES[selected_profile()]',
                      'gpu_events=False,cpu_events=fifo_cpu,host_clock=False)\n    if PROFILE_NAMES[selected_profile()]')
    runtime = replace(runtime, '    if fifo_cpu:\n        if mode',
                      "    fifo_cpu = fifo_cpu or os.environ.get('VERBS_FIFO_CAPTURE')=='1'\n    if fifo_cpu:\n        if mode")
    return {PROXY: proxy.encode('utf-8'), RUNTIME: runtime.encode('utf-8')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('tree', type=Path)
    tree = parser.parse_args().tree.resolve()
    if REPO == tree or REPO in tree.parents or tree in REPO.parents:
        parser.error('use an isolated source directory outside the active checkout')
    sources = compose()
    sha = lambda data: hashlib.sha256(data).hexdigest()
    expected = dict(base=BASE, sources={name: sha(data) for name, data in sources.items()})
    marker = tree / MARKER
    if not tree.exists():
        subprocess.run(['git', '-C', str(REPO), 'worktree', 'add', '--detach', str(tree), BASE], check=True)
        for name, data in sources.items():
            (tree / name).write_bytes(data)
        marker.write_text(json.dumps(expected, indent=2) + '\n', encoding='utf-8')
    if (not marker.is_file() or json.loads(marker.read_text(encoding='utf-8')) != expected
            or any(sha((tree / name).read_bytes()) != digest for name, digest in expected['sources'].items())):
        raise ValueError('FIFO probe tree changed; use a fresh --tree and --build-dir')
    status = subprocess.check_output(['git', '-C', str(tree), 'status', '--porcelain', '-uall']).decode().splitlines()
    revision = subprocess.check_output(['git', '-C', str(tree), 'rev-parse', 'HEAD']).decode().strip()
    if revision != BASE or {line[3:] for line in status if line != f'?? {MARKER}'} != set(sources):
        raise ValueError('FIFO probe source tree has other changes')
    print('VERBS_FIFO_TREE_READY')


if __name__ == '__main__':
    main()
