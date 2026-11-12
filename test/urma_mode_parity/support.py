"""Source provenance and explicit loading for freshly rebuilt extensions."""
import ctypes
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import types

REPO=Path(__file__).resolve().parents[2]
def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def source_manifest():
    paths=[REPO/'ep/setup.py']
    for directory in ('ep/src','ep/include','include'):
        paths.extend(p for p in (REPO/directory).rglob('*') if p.is_file() and p.suffix in ('.h','.hpp','.cuh','.cpp','.cc','.cu','.py','.sh'))
    return {p.relative_to(REPO).as_posix():sha(p) for p in sorted(set(paths))}

def diagnostic_manifest():
    paths=[]
    for directory in ('ep/bench','ep/tests','test/urma_mode_parity','test/verbs_diag','test/gpu_stage_recovery','test/dispatch_diag','test/reference_events'):
        paths.extend(p for p in (REPO/directory).rglob('*') if p.is_file() and p.suffix in ('.h','.hpp','.cuh','.cpp','.cc','.cu','.inc','.py','.sh')
                     and not any(part in ('results','bench_results','cache','tmpdir','__pycache__') for part in p.relative_to(REPO/directory).parts))
    if (REPO/'test/urma_mode_parity/gpu_stage_sites.json').is_file():paths.append(REPO/'test/urma_mode_parity/gpu_stage_sites.json')
    if (REPO/'test/dispatch_diag/dispatch_sites.json').is_file():paths.append(REPO/'test/dispatch_diag/dispatch_sites.json')
    return {p.relative_to(REPO).as_posix():sha(p) for p in sorted(set(paths))}

def load_extension(module):
    runtime=Path(os.environ.get('MACA_PATH','/opt/maca-3.7.0'))/'lib/libmcruntime.so'
    if runtime.is_file(): ctypes.CDLL(str(runtime),mode=os.RTLD_NOW|os.RTLD_GLOBAL)
    import torch
    package=types.ModuleType('uccl'); package.__path__=[]; sys.modules['uccl']=package
    spec=importlib.util.spec_from_file_location('uccl.ep',str(module))
    extension=importlib.util.module_from_spec(spec); sys.modules['uccl.ep']=extension
    spec.loader.exec_module(extension)
    package.ep=extension
    if not hasattr(extension,'get_urma_mode_version') or extension.get_urma_mode_version()!=2:
        raise RuntimeError('MODE_PARITY_V2_EXTENSION_REQUIRED: rebuild; historical private modules are unsupported')
    loaded=sorted({line.split()[-1] for line in Path('/proc/self/maps').read_text().splitlines() if '.so' in line and '/' in line})
    if os.environ.get('UCCL_EP_TRANSPORT')=='urma':
        private=Path(os.environ['URMA_DIAG_LIBRARY_DIR']).resolve()
        actual=[Path(p).resolve() for p in loaded if '/liburma.so' in p]
        if not actual or not all(p.is_relative_to(private) for p in actual):
            raise RuntimeError(f'PRIVATE_URMA_LIBRARY_REQUIRED: {actual}')
    runtime_hashes={Path(p).name:sha(p) for p in loaded if Path(p).name.startswith(('libmcruntime.so','libruntime_cu.so'))}
    return extension,dict(module=str(Path(module).resolve()),module_sha=sha(module),wire=2,torch=torch.__version__,torch_file=torch.__file__,python=sys.executable,loaded_libraries=loaded,selected_runtime_hashes=runtime_hashes,
                          phase_diagnostics=all(hasattr(extension,n) for n in ('urma_phase_enable','urma_phase_snapshot')),
                          ll_write_order=extension.get_urma_ll_write_order() if hasattr(extension,'get_urma_ll_write_order') else None,
                          normal_write_order=extension.get_urma_normal_write_order() if hasattr(extension,'get_urma_normal_write_order') else None,
                          normal_submit_schedule=extension.get_urma_normal_submit_schedule() if hasattr(extension,'get_urma_normal_submit_schedule') else None,
                          normal_max_inflight=extension.get_normal_max_inflight() if hasattr(extension,'get_normal_max_inflight') else None,
                          normal_completion=extension.get_urma_normal_completion() if hasattr(extension,'get_urma_normal_completion') else None,
                          micro_diagnostics=hasattr(extension,'UrmaMicroSession'),
                          queue_backend=extension.get_d2h_backend() if hasattr(extension,'get_d2h_backend') else None,
                          gpu_stage_version=extension.get_gpu_stage_version() if hasattr(extension,'get_gpu_stage_version') else 0,
                          dispatch_trace_version=extension.get_dispatch_trace_version() if hasattr(extension,'get_dispatch_trace_version') else 0,
                          reference_events_version=extension.get_reference_events_version() if hasattr(extension,'get_reference_events_version') else 0,
                          reference_boundary_version=extension.get_reference_boundary_version() if hasattr(extension,'get_reference_boundary_version') else 0,
                          reference_profile=os.environ.get('UCCL_REFERENCE_PROFILE','legacy'),
                          notify_version=extension.get_urma_notify_version() if hasattr(extension,'get_urma_notify_version') else 0,
                          dispatch_trial=all(hasattr(extension,n) for n in ('urma_dispatch_trial_begin','urma_dispatch_trial_end')),
                          micro_version=extension.get_urma_micro_version() if hasattr(extension,'get_urma_micro_version') else 1,
                          layout_diagnostics=all(hasattr(extension,n) for n in ('urma_layout_begin','urma_layout_end')))

def read_build(directory):
    data=json.loads((directory/'manifest.json').read_text())
    module=directory/data['module']
    if sha(module)!=data['module_sha']:
        raise RuntimeError('BUILT_MODULE_SHA_MISMATCH')
    if data['sources']!=source_manifest():
        raise RuntimeError('SOURCE_CHANGED_SINCE_BUILD: build into a new --build-dir')
    return module,data

if __name__=='__main__':
    _,info=load_extension(Path(sys.argv[1]))
    print('MODE_PARITY_IMPORT_OK '+json.dumps(info),flush=True)
