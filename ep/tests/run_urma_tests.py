#!/usr/bin/env python3
"""Compile the real transport against the chosen P2P SDK; simulate hardware only."""
import argparse
import os
from pathlib import Path
import subprocess
import tempfile
import shutil
import json

p = argparse.ArgumentParser()
p.add_argument('--sdk-include', required=True, type=Path,
               help='Directory containing the tested urma_api.h and urma_types.h')
p.add_argument('--syntax-only', action='store_true')
p.add_argument('--reference-sdk-fixture', action='store_true',
               help='CPU simulation ONLY: add a placeholder GPU-segment field to upstream reference headers; does not validate the private SDK ABI')
a = p.parse_args()
ep = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix='uccl-urma-test-') as tmp:
    tmp = Path(tmp)
    gpu_stage_test=tmp/'gpu_stage_test'
    subprocess.run([os.environ.get('CXX','c++'),'-std=c++17','-Wall','-Wextra','-Werror','-I'+str(ep/'include'),str(ep/'tests/gpu_stage_trace_test.cpp'),'-o',str(gpu_stage_test)],check=True)
    subprocess.run([str(gpu_stage_test)],check=True)
    roles_test=tmp/'gpu_stage_roles_test'
    subprocess.run([os.environ.get('CXX','c++'),'-std=c++17','-O3','-Wall','-Wextra','-Werror','-I'+str(ep/'include'),str(ep/'tests/gpu_stage_roles_test.cpp'),'-o',str(roles_test)],check=True)
    subprocess.run([str(roles_test)],check=True)
    runtime_test=tmp/'gpu_stage_runtime_test'
    subprocess.run([os.environ.get('CXX','c++'),'-std=c++17','-pthread','-Wall','-Wextra','-Werror','-I'+str(ep/'tests/gpu_stage_runtime_support'),'-I'+str(ep/'include'),str(ep/'tests/gpu_stage_runtime_test.cpp'),'-o',str(runtime_test)],check=True)
    subprocess.run([str(runtime_test)],check=True)
    reference_test=tmp/'reference_event_test'
    subprocess.run([os.environ.get('CXX','c++'),'-std=c++17','-pthread','-Wall','-Wextra','-Werror','-I'+str(ep/'tests/gpu_stage_runtime_support'),'-I'+str(ep/'include'),str(ep/'tests/reference_event_test.cpp'),'-o',str(reference_test)],check=True)
    subprocess.run([str(reference_test)],check=True)
    reference_cpu_test=tmp/'reference_timeline_test'
    subprocess.run([os.environ.get('CXX','c++'),'-std=c++17','-pthread','-Wall','-Wextra','-Werror','-I'+str(ep/'include'),str(ep/'tests/reference_timeline_test.cpp'),'-o',str(reference_cpu_test)],check=True)
    subprocess.run([str(reference_cpu_test)],check=True)
    fifo_source=(ep/'src/fifo.cpp').read_text()
    (tmp/'fifo_implementation.inc').write_text(fifo_source[fifo_source.index('namespace mscclpp {'):])
    notify_source=(ep/'src/proxy.cpp').read_text()
    begin=notify_source.index('void Proxy::notify_gpu_completion(');end=notify_source.index('void Proxy::post_gpu_command(',begin)
    (tmp/'fifo_notify.inc').write_text(notify_source[begin:end].replace('  if (acked_wrs_.empty())','  (void)my_tail;\n  if (acked_wrs_.empty())',1))
    for trace in (False,True):
        fifo_test=tmp/('fifo_stage_'+str(trace))
        extra=['-DUCCL_GPU_STAGE_TRACE'] if trace else []
        subprocess.run([os.environ.get('CXX','c++'),'-std=c++17','-Wall','-Wextra','-Werror','-I'+str(ep/'include'),'-I'+str(tmp),*extra,str(ep/'tests/fifo_stage_test.cpp'),'-o',str(fifo_test)],check=True)
        subprocess.run([str(fifo_test)],check=True)
    phase_test=tmp/'phase_test'
    subprocess.run([os.environ.get('CXX','c++'),'-std=c++17','-pthread','-Wall','-Wextra','-Werror',
                    '-I'+str(ep/'include'),str(ep/'tests/urma_phase_diag_test.cpp'),'-o',str(phase_test)],check=True)
    phase_result=subprocess.run([str(phase_test)],check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True)
    assert len(json.loads(phase_result.stdout)['records'])==4
    print('URMA concurrent phase diagnostics tests passed',flush=True)
    trial_test=tmp/'dispatch_trial_test'
    subprocess.run([os.environ.get('CXX','c++'),'-std=c++17','-pthread','-Wall','-Wextra','-Werror',
                    '-I'+str(ep/'include'),str(ep/'tests/urma_dispatch_trial_test.cpp'),'-o',str(trial_test)],check=True)
    subprocess.run([str(trial_test)],check=True)
    allocation=(ep/'src/uccl_proxy.cpp').read_text()
    begin=allocation.index('std::pair<void*, std::shared_ptr<void>> allocate_urma_atomic_control(')
    end=allocation.index('UcclProxy::UcclProxy(',begin)
    (tmp/'atomic_allocation.inc').write_text(allocation[begin:end])
    allocation_test=tmp/'atomic_allocation_test'
    subprocess.run([os.environ.get('CXX','c++'),'-std=c++17','-Wall','-Wextra','-Werror',
                    '-I'+str(ep/'tests/urma_support'),'-I'+str(ep/'include'),'-I'+str(tmp),
                    '-include',str(ep/'tests/urma_support/ep_shim.hpp'),
                    str(ep/'tests/urma_atomic_allocation_test.cpp'),'-o',str(allocation_test)],check=True)
    subprocess.run([str(allocation_test)],check=True)
    protocol=(ep/'include/ring_buffer.cuh').read_text()
    protocol=protocol[protocol.index('enum class CmdType'):protocol.index('struct CopyTask')]
    (tmp/'transfer_protocol.inc').write_text(protocol)
    proxy=(ep/'src/proxy.cpp').read_text()
    begin=proxy.index('void Proxy::post_urma_commands(')
    end=proxy.index('CxiTransport* Proxy::cxi_transport_for_rank',begin)
    (tmp/'proxy_plan.inc').write_text(proxy[begin:end])
    proxy_test=tmp/'proxy_plan_test'
    subprocess.run([os.environ.get('CXX','c++'),'-std=c++17','-Wall','-Wextra','-Werror',
                    '-I'+str(ep/'include'),'-I'+str(tmp),
                    str(ep/'tests/urma_proxy_plan_test.cpp'),'-o',str(proxy_test)],check=True)
    subprocess.run([str(proxy_test)],check=True)
    begin=proxy.index('void Proxy::poll_urma_completions()')
    end=proxy.index('void Proxy::post_urma_commands(',begin)
    (tmp/'proxy_completion.inc').write_text(proxy[begin:end])
    completion_test=tmp/'proxy_completion_test'
    subprocess.run([os.environ.get('CXX','c++'),'-std=c++17','-pthread','-Wall','-Wextra','-Werror',
                    '-I'+str(ep/'include'),'-I'+str(tmp),
                    '-include',str(ep/'tests/urma_support/ep_shim.hpp'),
                    str(ep/'tests/urma_proxy_completion_test.cpp'),'-o',str(completion_test)],check=True)
    subprocess.run([str(completion_test)],check=True)
    # Preserve ordinary verbs-only builds: all added URMA entry points also
    # need valid fail-fast stubs without requiring SDK declarations.
    subprocess.run([os.environ.get('CXX','c++'),'-std=c++17','-Wall','-Wextra','-Werror',
                    '-I'+str(ep/'tests/urma_support'),'-I'+str(ep/'include'),
                    '-include',str(ep/'tests/urma_support/ep_shim.hpp'),
                    '-fsyntax-only',str(ep/'src/urma_transport.cpp')],check=True)
    if a.reference_sdk_fixture:
        fixture = tmp/'reference-sdk-fixture'
        shutil.copytree(str(a.sdk_include.resolve()),str(fixture))
        types = fixture/'urma_types.h'
        source = types.read_text()
        if 'is_gpu_seg' not in source:
            source = source.replace('} urma_seg_cfg_t;', '    uint8_t is_gpu_seg; /* CPU test placeholder; NOT vendor ABI */\n} urma_seg_cfg_t;')
            types.write_text(source)
        a.sdk_include = fixture
        print('REFERENCE_SDK_FIXTURE: simulated GPU-segment field; no private ABI/hardware validation',flush=True)
    fake_metax = tmp / 'fake_mcruntime.cpp'
    fake_metax.write_text(r'''
#include <cstdlib>
#include <cstring>
#include <set>
static std::set<void*> allocations;
extern "C" int mcSetDevice(int device) { return device >= 0 ? 0 : 1; }
extern "C" int mcGetDeviceCount(int* count) { *count = 8; return 0; }
extern "C" int mcMalloc(void** ptr, size_t bytes) {
    if (bytes == 0 || bytes % 4096 != 0) return 1;
    *ptr = std::malloc(bytes);
    if (*ptr) allocations.insert(*ptr);
    return *ptr ? 0 : 1;
}
extern "C" int mcFree(void* ptr) {
    if (allocations.erase(ptr)) std::free(ptr);
    return 0;
}
extern "C" int mcMemcpy(void* dst, void const* src, size_t bytes, int) {
    std::memcpy(dst, src, bytes);
    return 0;
}
''')
    fake_metax_lib = tmp / ('libmcruntime.dylib' if os.uname().sysname == 'Darwin'
                            else 'libmcruntime.so')
    shared_flag = '-dynamiclib' if os.uname().sysname == 'Darwin' else '-shared'
    subprocess.run([os.environ.get('CXX', 'c++'), '-std=c++17', shared_flag,
                    '-fPIC', str(fake_metax), '-o', str(fake_metax_lib)],
                   check=True)
    # Also support the existing installed-header spelling while testing baseline.
    nested = tmp / 'ub/umdk/urma'
    nested.parent.mkdir(parents=True)
    nested.symlink_to(a.sdk_include.resolve(), target_is_directory=True)
    cmd = [os.environ.get('CXX', 'c++'), '-std=c++17', '-pthread',
           '-Wall', '-Wextra', '-Werror', '-DUSE_URMA',
           '-I' + str(ep / 'tests/urma_support'), '-I' + str(ep / 'include'),
           '-I' + str(a.sdk_include.resolve()), '-I' + str(tmp),
           '-include', str(ep / 'tests/urma_support/ep_shim.hpp')]
    cmd += [str(ep / 'src/gpu_p2p_runtime.cpp'),
            str(ep / 'src/urma_transport.cpp')]
    if a.syntax_only:
        cmd += ['-fsyntax-only']
    else:
        cmd += [str(ep / 'tests/urma_transport_test.cpp'), '-o', str(tmp / 'test')]
    subprocess.run(cmd, check=True)
    if not a.syntax_only:
        subprocess.run([str(tmp / 'test')], check=True)
        micro_cmd=cmd[:]
        # Replace the existing output and enable an alternate main in the
        # same SDK test boundary; compile the REAL micro implementation.
        micro_cmd=micro_cmd[:micro_cmd.index('-o')]
        micro_cmd+=['-DUCCL_URMA_MICRO_TEST',str(ep/'src/urma_micro.cpp'),'-o',str(tmp/'micro_test')]
        subprocess.run(micro_cmd,check=True)
        subprocess.run([str(tmp/'micro_test')],check=True)

    metax_cmd = [os.environ.get('CXX', 'c++'), '-std=c++17', '-pthread',
                 '-Wall', '-Wextra', '-Werror', '-DUSE_URMA',
                 '-DUSE_METAX',
                 '-I' + str(ep / 'tests/urma_support'),
                 '-I' + str(ep / 'include'), '-I' + str(a.sdk_include.resolve()),
                 '-I' + str(tmp), '-include',
                 str(ep / 'tests/urma_support/ep_shim.hpp'),
                 str(ep / 'src/gpu_p2p_runtime.cpp'),
                 str(ep / 'src/urma_transport.cpp')]
    if a.syntax_only:
        subprocess.run([*metax_cmd, '-fsyntax-only'], check=True)
    else:
        metax_test = tmp / 'metax_transport_test'
        subprocess.run([*metax_cmd, str(ep / 'tests/urma_transport_test.cpp'),
                        '-ldl', '-o', str(metax_test)], check=True)
        metax_env = dict(os.environ,
                         UCCL_METAX_RUNTIME_LIB=str(fake_metax_lib))
        subprocess.run([str(metax_test)], check=True, env=metax_env)

        runtime_test = tmp / 'metax_runtime_test'
        subprocess.run([os.environ.get('CXX', 'c++'), '-std=c++17',
                        '-pthread', '-Wall', '-Wextra', '-Werror', '-DUSE_METAX',
                        '-I' + str(ep / 'include'),
                        str(ep / 'src/gpu_p2p_runtime.cpp'),
                        str(ep / 'tests/gpu_p2p_runtime_test.cpp'),
                        '-ldl', '-o', str(runtime_test)], check=True)
        subprocess.run([str(runtime_test)], check=True, env=metax_env)

    for defines in ([], ['-DUSE_URMA'],
                    ['-DUSE_URMA', '-DUSE_METAX'],
                    ['-DUSE_URMA', '-DUSE_URMA_METAX'],
                    ['-DUSE_URMA', '-DUSE_SUBSET_BARRIER'],
                    ['-DUSE_URMA', '-D__HIPCC__']):
        binary = tmp / 'backend_test'
        subprocess.run([os.environ.get('CXX', 'c++'), '-std=c++17',
                        '-Wall', '-Wextra', '-Werror',
                        '-I' + str(ep / 'include'), *defines,
                        str(ep / 'tests/backend_config_test.cpp'),
                        '-o', str(binary)], check=True)
        subprocess.run([str(binary)], check=True)
