"""Build and validate a phase-scoped URMA interposer; no torch dependency."""
import ctypes
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

HERE=Path(__file__).resolve().parent
SOURCES=('transport_trace.cpp','tests/trace_fake_provider.cpp','tests/trace_selftest.cpp')

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def within(path,parent):
    try:
        Path(path).relative_to(parent)
        return True
    except ValueError:
        return False

def run(command, log):
    completed=subprocess.run(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True,timeout=60)
    with log.open('a') as output:
        output.write(json.dumps(command)+'\n'+completed.stdout+completed.stderr+'\n')
    if completed.returncode:
        raise RuntimeError(f'TRACE_BUILD_OR_SELFTEST_FAILED: see {log}\n{completed.stderr[-3000:]}')
    return completed.stdout

def build(directory, private_lib_dir, include_dir=None, compiler='g++', allow_mock=False):
    directory=Path(directory).resolve()
    directory.mkdir(parents=True,exist_ok=True)
    if not shutil.which(compiler):
        raise RuntimeError(f'HOST_CXX_MISSING: {compiler}; this builds only a small CPU library')
    candidates=[Path(include_dir)] if include_dir else [Path(private_lib_dir).parent/'include',Path('/usr/include/umdk'),Path('/usr/include/ub/umdk'),Path('/usr/include/urma'),Path('/usr/local/include/umdk'),Path('/usr/local/include'),Path('/usr/include')]
    include=next((p.resolve() for p in candidates if (p/'urma_api.h').is_file()),None)
    if include is None:
        raise RuntimeError('URMA_HEADERS_MISSING: set --urma-include-dir to the installed private UMDK include directory containing urma_api.h')
    if not allow_mock and 'Test fixture only' in (include/'urma_api.h').read_text()[:500]:
        raise RuntimeError('MOCK_SDK_REJECTED: benchmark instrumentation requires real server UMDK headers')
    log=directory/'build.log'
    flags=['-std=c++17','-O2','-pthread','-I',str(include)]
    version=run([compiler,'--version'],log).splitlines()[0]
    # Hash actual transitive SDK header dependencies, including the private GPU-segment ABI.
    deps=run([compiler,*flags,'-M',str(HERE/'transport_trace.cpp')],log)
    paths=deps.replace('\\\n',' ').split(':',1)[1].split()
    headers={}
    for value in paths:
        path=Path(value).resolve()
        if path.suffix=='.h' and (path.name.startswith(('urma','ub_','udma')) or (str(include) not in ('/usr/include','/usr/local/include') and within(path,include))):
            if path.name in headers and headers[path.name]!=sha(path):
                raise RuntimeError('ambiguous SDK header names')
            headers[path.name]=sha(path)
    if 'urma_api.h' not in headers:
        raise RuntimeError('TRACE_SDK_DEPENDENCIES_NOT_CAPTURED')
    library=directory/'libuccl_urma_trace.so'
    provider=directory/'libfake_urma.so'
    selftest=directory/'trace_selftest'
    for source,target in [(HERE/'transport_trace.cpp',library),(HERE/'tests/trace_fake_provider.cpp',provider)]:
        run([compiler,*flags,'-fPIC','-shared',str(source),'-ldl','-o',str(target)],log)
    run([compiler,*flags,str(HERE/'tests/trace_selftest.cpp'),'-ldl','-o',str(selftest)],log)
    output=directory/'selftest.json'
    run([str(selftest),str(library),str(provider),str(output)],log)
    data=json.loads(output.read_text())
    totals=aggregate(data['threads'])
    expected=dict(post_calls=4000,posted_wrs=8000,write_wrs=4000,fadd_wrs=4000,write_bytes=512000,completed=8000,empty_polls=4004,inflight=0)
    if len(data['threads'])!=4 or any(totals[key]!=value for key,value in expected.items()) or json.loads(Path(str(output)+'.empty').read_text())['threads']:
        raise RuntimeError(f'TRACE_SELFTEST_COUNTER_MISMATCH: {totals}')
    if any(totals[key] for key in ('post_errors','poll_errors','completion_errors','unknown_completions','map_overflow','context_errors')):
        raise RuntimeError('TRACE_SELFTEST_TRACKING_FAILED')
    for index in (0,1):
        reuse=json.loads(Path(str(output)+f'.reuse{index}').read_text())
        reused=aggregate(reuse['threads'])
        if len(reuse['threads'])!=1 or reused['posted_wrs']!=1 or reused['completed']!=1 or reused['inflight']:
            raise RuntimeError('TRACE_SELFTEST_PHASE_RESET_FAILED')
    for thread in data['threads']:
        if thread['batch_max']!=2 or thread['peak_inflight']!=2 or thread['post_write']['samples']<1 or thread['write_observed']['samples']<1:
            raise RuntimeError('TRACE_SELFTEST_TIMING_FAILED')
    manifest=dict(schema=1,sources={name:sha(HERE/name) for name in SOURCES},headers=headers,compiler=version,flags=flags,library_sha=sha(library),selftest_passed=True)
    (directory/'manifest.json').write_text(json.dumps(manifest,indent=2))
    # Do not compare per-host build paths or ELF build IDs in the peer handshake.
    fingerprint=dict(sources=manifest['sources'],headers=headers,compiler=version,selftest_passed=True)
    return library,manifest,fingerprint

def aggregate(threads):
    scalars=('post_calls','post_errors','poll_calls','empty_polls','poll_errors','completed','completion_errors','attempted_wrs','posted_wrs','write_wrs','fadd_wrs','other_wrs','write_bytes','complete_enabled','fence_enabled','inflight','unknown_completions','map_overflow','context_errors')
    return {key:sum(t[key] for t in threads) for key in scalars}

class TraceSession:
    def __init__(self, library, private_lib_dir):
        self.library=ctypes.CDLL(str(library))
        self.library.uccl_trace_real_path.restype=ctypes.c_char_p
        self.library.uccl_trace_end.argtypes=[ctypes.c_char_p]
        if self.library.uccl_trace_ready()!=1:
            raise RuntimeError('TRACE_REAL_URMA_RESOLUTION_FAILED')
        real=Path(self.library.uccl_trace_real_path().decode()).resolve()
        if not within(real,Path(private_lib_dir).resolve()):
            raise RuntimeError(f'TRACE_WRONG_REAL_LIBRARY: {real}')

    def begin(self):
        if self.library.uccl_trace_begin()!=0:
            raise RuntimeError('TRACE_BEGIN_FAILED')

    def end(self,path):
        rc=self.library.uccl_trace_end(os.fsencode(path))
        if rc:
            raise RuntimeError(f'TRACE_END_FAILED: rc={rc}')
        return json.loads(Path(path).read_text())

def validate(data, expected_tids):
    threads=data['threads']
    totals=aggregate(threads)
    if data['thread_overflow'] or not threads or not {t['tid'] for t in threads}.issubset(set(expected_tids)):
        raise RuntimeError('TRACE_PROXY_TID_COVERAGE_FAILED')
    if not totals['write_wrs'] or not totals['fadd_wrs'] or not totals['completed']:
        raise RuntimeError('TRACE_INTERPOSITION_NOT_OBSERVED: WRITE/FADD/completions required')
    if any(totals[key] for key in ('post_errors','poll_errors','completion_errors','map_overflow','context_errors')):
        raise RuntimeError('TRACE_API_OR_CONTEXT_ERRORS')
    relax=data['constants']['relax']
    if sum(t['write_order'][relax] for t in threads)!=totals['write_wrs']:
        raise RuntimeError('TRACE_EXPECTED_RELAX_WRITE_NOT_OBSERVED')
    totals['empty_poll_ratio']=totals['empty_polls']/totals['poll_calls'] if totals['poll_calls'] else None
    totals['completions_per_nonempty_poll']=totals['completed']/(totals['poll_calls']-totals['empty_polls']) if totals['poll_calls']>totals['empty_polls'] else None
    return totals
