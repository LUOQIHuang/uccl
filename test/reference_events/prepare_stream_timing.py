"""Prepare the cached-Dispatch stream markers in a detached source tree."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
BASE = '51744a5064a331783ff8f2fbadf11d7acb4af2d5'
MARKER = '.normal_stream_timing.json'


def replace(source, old, new):
    if source.count(old) != 1:
        raise ValueError(f'cached Dispatch stream-timing anchor changed: {old!r}')
    return source.replace(old, new)


def compose():
    ep = 'ep/src/uccl_ep.cc'
    runtime = 'test/reference_events/event_runtime.py'
    src = subprocess.check_output(['git', '-C', str(REPO), 'show', f'{BASE}:{ep}']).decode('utf-8')
    first = src.index('  std::optional<EventHandle> internode_dispatch(')
    last = src.index('  std::optional<EventHandle> internode_combine(', first)
    dispatch = src[first:last]
    dispatch = replace(dispatch, '    if (cached_mode) {\n      uccl::internode::cached_notify(',
        '    if (cached_mode) {\n      uccl::normal_stream_timing::mark(0, comm_stream);\n      uccl::internode::cached_notify(')
    dispatch = replace(dispatch, '          num_d2h_channel_addrs, atomic_buffer_ptr);\n    } else {',
        '          num_d2h_channel_addrs, atomic_buffer_ptr);\n      uccl::normal_stream_timing::mark(1, comm_stream);\n    } else {')
    dispatch = replace(dispatch, '    uccl::internode::dispatch(',
        '    if (cached_mode) uccl::normal_stream_timing::mark(2, comm_stream);\n    uccl::internode::dispatch(')
    dispatch = replace(dispatch, '    std::optional<EventHandle> event;',
        '    if (cached_mode) uccl::normal_stream_timing::mark(3, comm_stream);\n\n    std::optional<EventHandle> event;')
    src = '#include "normal_stream_timing.hpp"\n' + src[:first] + dispatch + src[last:]
    anchor = '  m.def("urma_dispatch_trial_end",[](){return uccl::dispatch_trial::end();});'
    src = replace(src, anchor, anchor+'''
  m.def("normal_stream_timing_prepare", &uccl::normal_stream_timing::prepare);
  m.def("normal_stream_timing_begin", &uccl::normal_stream_timing::begin);
  m.def("normal_stream_timing_end", &uccl::normal_stream_timing::end);
  m.def("normal_stream_timing_close", &uccl::normal_stream_timing::close);''')
    return {
        ep:src.encode('utf-8'),
        runtime:(REPO/runtime).read_text(encoding='utf-8').encode('utf-8'),
        'ep/include/normal_stream_timing.hpp':(HERE/'stream_timing.hpp').read_text(encoding='utf-8').encode('utf-8'),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('tree', type=Path)
    tree = parser.parse_args().tree.resolve()
    if tree == REPO or tree in REPO.parents or REPO in tree.parents:
        parser.error('use an isolated source directory outside the active checkout')
    sources = compose()
    sha = lambda data: hashlib.sha256(data).hexdigest()
    expected = dict(base=BASE, sources={name:sha(data) for name,data in sources.items()})
    marker = tree/MARKER
    if not tree.exists():
        subprocess.run(['git','-C',str(REPO),'worktree','add','--detach',str(tree),BASE],check=True)
        for name,data in sources.items():
            (tree/name).write_bytes(data)
        marker.write_text(json.dumps(expected,indent=2)+'\n',encoding='utf-8')
    if (not marker.is_file() or json.loads(marker.read_text(encoding='utf-8'))!=expected
            or any(sha((tree/name).read_bytes())!=value for name,value in expected['sources'].items())):
        raise ValueError('stream overlay changed; use a fresh tree and build directories')
    git = ['git','-C',str(tree)]
    revision = subprocess.check_output(git+['rev-parse','HEAD']).decode().strip()
    status = subprocess.check_output(git+['status','--porcelain','-uall']).decode().splitlines()
    if revision != BASE or {line[3:] for line in status if line != f'?? {MARKER}'} != set(sources):
        raise ValueError('stream overlay contains other changes')
    print('NORMAL_STREAM_TREE_READY')


if __name__ == '__main__':
    main()
