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


def source_at_base(name):
    return subprocess.check_output(['git', '-C', str(REPO), 'show', f'{BASE}:{name}']).decode('utf-8')


def with_receiver_waits(source):
    first = source.index('  } else if (warp_role == WarpRole::kRDMAAndNVLForwarder) {')
    last = source.index('  } else if (warp_role == WarpRole::kForwarderCoordinator) {', first)
    f = source[first:last]
    f = replace(f, '    // Forward tokens from RDMA buffer', '''    namespace rw = uccl::normal_receiver_waits;
    const bool wait_probe = kCachedMode && kNumRDMARanks == 2 &&
        static_cast<unsigned>(channel_id) < rw::channels && rw::state.enabled;
    rw::Counters waits{};
    const auto wait_probe_start = wait_probe && lane_id == 0 ? clock64() : 0;
    // Forward tokens from RDMA buffer''')
    f = replace(f, '      auto src_rdma_head =', '''      if (wait_probe) {
        const int local_remaining = __shfl_sync(WARP_MASK, num_tokens_to_recv_from_rdma, rdma_rank);
        if (lane_id == 0) {
          const auto elapsed = clock64() - start_time;
          if (src_rdma_rank != rdma_rank && local_remaining == 0) waits.remote_tail += elapsed;
          else waits.mixed_tail += elapsed;
        }
      }
      auto src_rdma_head =''')
    f = replace(f, '}\n          }\n        }\n// REFERENCE_EVENT_BEGIN', '''}
            if (wait_probe && lane_id == 0 && src_rdma_rank != rdma_rank)
              waits.remote_epoch += clock64() - spin_start;
          }
        }
// REFERENCE_EVENT_BEGIN''')
    f = replace(f, '          auto nvl_wait_start = clock64();', '''          auto nvl_wait_start = clock64();
          const bool was_full = wait_probe && lane_id == 0 &&
              cached_nvl_channel_tail - cached_nvl_channel_head >= num_max_nvl_chunked_recv_tokens;''')
    f = replace(f, '}\n        }\n\n        UCCL_GPU_STAGE_DETAIL(dispatch_forward_copy', '''}
          if (was_full) waits.local_space += clock64() - nvl_wait_start;
        }

        UCCL_GPU_STAGE_DETAIL(dispatch_forward_copy''')
    f = replace(f, '    // Retired\n    __syncwarp();', '''    // Retired
    __syncwarp();
    if (wait_probe && lane_id == 0) {
      waits.forwarder = clock64() - wait_probe_start;
      rw::state.counters[channel_id] = waits;
    }''')
    source = source[:first]+f+source[last:]
    source = replace(source, '  if(warp_role==WarpRole::kNVLReceivers && lane_id==0) uccl::reference_events::boundary(9);', '''  if (kCachedMode && kNumRDMARanks == 2 && warp_role == WarpRole::kNVLReceivers && lane_id == 0 &&
      static_cast<unsigned>(channel_id) < uccl::normal_receiver_waits::channels && uccl::normal_receiver_waits::state.enabled) {
    auto& state = uccl::normal_receiver_waits::state;
    state.done_order[channel_id] = atomicAdd(&state.next_done, 1u) + 1;
  }
  if(warp_role==WarpRole::kNVLReceivers && lane_id==0) uccl::reference_events::boundary(9);''')
    return '#include "normal_receiver_waits.cuh"\n'+source


def compose(receiver_waits=False):
    ep = 'ep/src/uccl_ep.cc'
    runtime = 'test/reference_events/event_runtime.py'
    src = source_at_base(ep)
    anchor = '  m.def("urma_dispatch_trial_end",[](){return uccl::dispatch_trial::end();});'
    sources = {runtime:(REPO/runtime).read_text(encoding='utf-8').encode('utf-8')}
    if receiver_waits:
        src = '#include "normal_receiver_waits.cuh"\n'+replace(src,anchor,anchor+'''
  m.def("normal_receiver_waits_begin", &uccl::normal_receiver_waits::begin);
  m.def("normal_receiver_waits_end", &uccl::normal_receiver_waits::end);''')
        sources[ep] = src.encode()
        sources['ep/include/normal_receiver_waits.cuh'] = (HERE/'receiver_waits.cuh').read_text(encoding='utf-8').encode()
        sources['ep/src/internode.cu'] = with_receiver_waits(source_at_base('ep/src/internode.cu')).encode()
        trace = source_at_base('ep/src/reference_event_trace.cu')
        trace += (HERE/'receiver_waits.cu.inc').read_text(encoding='utf-8')
        sources['ep/src/reference_event_trace.cu'] = trace.encode()
        return sources
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
    src = replace(src, anchor, anchor+'''
  m.def("normal_stream_timing_prepare", &uccl::normal_stream_timing::prepare);
  m.def("normal_stream_timing_begin", &uccl::normal_stream_timing::begin);
  m.def("normal_stream_timing_end", &uccl::normal_stream_timing::end);
  m.def("normal_stream_timing_close", &uccl::normal_stream_timing::close);''')
    sources[ep] = src.encode('utf-8')
    sources['ep/include/normal_stream_timing.hpp'] = (HERE/'stream_timing.hpp').read_text(encoding='utf-8').encode()
    return sources


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('tree', type=Path)
    parser.add_argument('--receiver-waits', action='store_true')
    args = parser.parse_args()
    tree = args.tree.resolve()
    if tree == REPO or tree in REPO.parents or REPO in tree.parents:
        parser.error('use an isolated source directory outside the active checkout')
    sources = compose(args.receiver_waits)
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
