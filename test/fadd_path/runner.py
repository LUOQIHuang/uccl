"""Isolated CPU completion-path probes; never modify the active tokens tree."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys

import overlay
HERE=Path(__file__).resolve().parent


def copy_checked(source,destination):
    destination.parent.mkdir(parents=True,exist_ok=True)
    if destination.exists() and destination.read_bytes()!=source.read_bytes():
        raise ValueError('result conflict: '+str(destination))
    shutil.copy2(str(source),str(destination))


def capabilities(destination):
    include=os.environ.get('URMA_DIAG_INCLUDE_DIR')
    data=dict(version=1,host=os.uname().nodename,kernel=os.uname().release,
              direct_device_timestamps_used=False,
              result_marker='read-only existing FADD host scratch, not remote GPU visibility',
              sdk_headers={},completion_fields=None,tracepoints=[],tracepoint_access_errors=[])
    if include:
        for name in ('urma_api.h','urma_types.h','urma_provider.h'):
            path=Path(include)/name
            if path.is_file():data['sdk_headers'][name]=hashlib.sha256(path.read_bytes()).hexdigest()
        path=Path(include)/'urma_types.h'
        if path.is_file():
            found=re.search(r'typedef struct urma_cr\s*\{(.*?)\}\s*urma_cr_t',path.read_text(errors='replace'),re.S)
            if found:data['completion_fields']=found.group(1)
    for directory in ('/sys/kernel/tracing','/sys/kernel/debug/tracing'):
        try:
            path=Path(directory)/'available_events'
            if path.exists():data['tracepoints']=sorted({s for s in path.read_text().splitlines() if re.search(r'udma|ubcore|urma|ummu',s,re.I)});break
        except OSError as error:data['tracepoint_access_errors'].append(str(error))
    destination.write_text(json.dumps(data,indent=2))


def command(a,job,action):
    return ['bash',str(a.tree/'test/reference_events/run.sh'),'--action',action,'--step','paced',
            '--backend',a.backend,'--node-rank',str(a.node_rank),'--job',job,'--tokens','4096',
            '--master-addr',a.master_addr,'--port',str(a.port),'--gid-index',str(a.gid_index),
            '--build-dir',str(a.build_dir),'--output',str(a.output/a.job/a.backend/'cases')]


def run_one(a,suffix,action,capture=False):
    job='%s_%s_%s'%(a.job,a.backend,suffix)
    source=a.output/a.job/a.backend/'cases'/job
    env=os.environ.copy();env['FADD_PATH_CAPTURE']='1' if capture else '0'
    rc=0
    try:
        print('FADD_PATH_CASE_BEGIN '+job,flush=True)
        process=subprocess.Popen(command(a,job,action),env=env,start_new_session=True)
        try:
            rc=process.wait()
        except KeyboardInterrupt:
            # Forward the interrupt and give the existing driver time to
            # terminate its own torchrun group and release registrations.
            os.killpg(process.pid,signal.SIGINT)
            try:process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid,signal.SIGTERM);process.wait(timeout=10)
            raise
    finally:
        collect=[sys.executable,str(a.tree/'test/gpu_stage_recovery/collect.py'),str(source),
                 '--node-rank',str(a.node_rank),'--trace-build',str(a.build_dir),
                 '--destination',str(a.destination/a.job/a.backend/'cases'/job)]
        subprocess.run(collect,check=True)
    if rc:raise SystemExit(rc)


def main(argv=None):
    home=Path(os.environ.get('URMA_DIAG_HOME',os.environ.get('VERBS_DIAG_HOME',str(Path.home()))))
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--action',choices=('prepare','build','check','run','collect'),required=True)
    p.add_argument('--backend',choices=('urma','verbs'),required=True)
    p.add_argument('--node-rank',type=int,choices=(0,1),required=True)
    p.add_argument('--job',default='fadd_path_01')
    p.add_argument('--tree',type=Path,default=home/'metax_dev/fadd_path_src_v1')
    p.add_argument('--build-dir',type=Path)
    p.add_argument('--output',type=Path,default=home/'metax_dev/fadd_path_results')
    p.add_argument('--destination',type=Path,default=HERE/'results')
    p.add_argument('--master-addr',default='141.61.17.202')
    p.add_argument('--port',type=int,default=33980)
    p.add_argument('--gid-index',type=int,default=3)
    a=p.parse_args(argv)
    if not re.fullmatch(r'[a-zA-Z0-9_-]+',a.job) or not 1024<=a.port<=65533 or not 0<=a.gid_index<256:p.error('invalid job/port/GID')
    a.tree=a.tree.resolve();a.output=a.output.resolve();a.destination=a.destination.resolve()
    a.build_dir=(a.build_dir or home/('metax_dev/fadd_path_build/%s_v1'%a.backend)).resolve()
    if a.action=='prepare':
        overlay.prepare(a.tree)
        print('FADD_PATH_PREPARE_PASSED '+str(a.tree));return
    # Reuse exactly the prepared sources; refuse silent overlay updates.
    provenance=(json.loads((a.tree/'.fadd_path_overlay.json').read_text()) if a.action=='collect' else overlay.prepare(a.tree))
    root=a.output/a.job/a.backend;root.mkdir(parents=True,exist_ok=True)
    marker=root/('overlay_node%d.json'%a.node_rank)
    encoded=json.dumps(provenance,indent=2)
    if marker.exists() and marker.read_text()!=encoded:raise ValueError('overlay changed within job')
    marker.write_text(encoded);copy_checked(marker,a.destination/a.job/a.backend/marker.name)
    cap=root/('capabilities_node%d.json'%a.node_rank)
    if not cap.exists():capabilities(cap)
    copy_checked(cap,a.destination/a.job/a.backend/cap.name)
    if a.action=='collect':
        for suffix in ('build','check','pre','probe','post'):
            job='%s_%s_%s'%(a.job,a.backend,suffix);source=root/'cases'/job
            if source.exists():
                subprocess.run([sys.executable,str(a.tree/'test/gpu_stage_recovery/collect.py'),str(source),
                    '--node-rank',str(a.node_rank),'--trace-build',str(a.build_dir),
                    '--destination',str(a.destination/a.job/a.backend/'cases'/job)],check=True)
        return
    if a.action in ('build','check'):
        run_one(a,a.action,a.action)
    else:
        for suffix,on in (('pre',False),('probe',True),('post',False)):
            run_one(a,suffix,'run',on)
    print('FADD_PATH_%s_PASSED backend=%s rank=%d'%(a.action.upper(),a.backend,a.node_rank))


if __name__=='__main__':main()
