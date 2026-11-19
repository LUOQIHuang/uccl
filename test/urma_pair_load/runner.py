"""Compare in-flight WRITE/FADD depth on URMA and verbs."""
import argparse,json,os,re,signal,subprocess,sys
from pathlib import Path
import overlay
HERE=Path(__file__).resolve().parent
EXPERIMENT='urma_pair_load'
BACKENDS=('urma','verbs')
DEFAULT_PORT=34480
def base_command(a,action,job):
    return ['bash',str(a.tree/'test/reference_events/run.sh'),'--backend',a.backend,'--step','paced',
            '--action',action,'--node-rank',str(a.node_rank),'--build-dir',str(a.build_dir),'--job',job,
            '--master-addr',a.master_addr,'--port',str(a.port),'--gid-index',str(a.gid_index),
            '--output',str(a.output/a.job/a.backend/'cases')]
def collect(a,job):
    source=a.output/a.job/a.backend/'cases'/job
    if source.exists():
        subprocess.run([sys.executable,str(a.tree/'test/gpu_stage_recovery/collect.py'),str(source),
            '--node-rank',str(a.node_rank),'--trace-build',str(a.build_dir),
            '--destination',str(a.destination/a.job/a.backend/'cases'/job)],check=True)
def main():
    home=Path(os.environ.get('URMA_DIAG_HOME',os.environ.get('VERBS_DIAG_HOME',str(Path.home()))))
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--action',choices=('prepare','build','check','run','collect'),required=True)
    p.add_argument('--backend',choices=BACKENDS,required=True)
    p.add_argument('--node-rank',type=int,choices=(0,1),required=True)
    p.add_argument('--job',default=EXPERIMENT+'_01');p.add_argument('--tree',type=Path,default=home/'metax_dev'/(EXPERIMENT+'_src_v4'))
    p.add_argument('--build-dir',type=Path);p.add_argument('--output',type=Path,default=home/'metax_dev'/(EXPERIMENT+'_results'))
    p.add_argument('--destination',type=Path,default=HERE/'results')
    p.add_argument('--master-addr',default='141.61.17.202');p.add_argument('--port',type=int,default=DEFAULT_PORT)
    p.add_argument('--gid-index',type=int,default=3);p.add_argument('--hca',default='rocep40s0f0')
    p.add_argument('--worker-cpu',default='auto');p.add_argument('--gpu-pci',default='0000:a1:00.0')
    p.add_argument('--suite',choices=('basic4','depth12','order12'),default='basic4')
    p.add_argument('--rounds',type=int,default=3);p.add_argument('--warmups',type=int,default=20);p.add_argument('--samples',type=int,default=101)
    a=p.parse_args()
    if a.backend=='verbs' and a.suite!='depth12':p.error('verbs requires --suite depth12')
    if not re.fullmatch(r'[A-Za-z0-9_-]+',a.job) or not 1024<=a.port<=65533 or not 0<=a.gid_index<256 or not 1<=a.rounds<=5 or not 1<=a.warmups<=10000 or not 11<=a.samples<=10001:p.error('invalid job/port/GID/sample plan')
    if a.hca!='rocep40s0f0':p.error('this fixed experiment uses rocep40s0f0; do not silently change build/check HCA')
    for n in ('tree','output','destination'):setattr(a,n,getattr(a,n).resolve())
    a.build_dir=(a.build_dir or home/('metax_dev/'+EXPERIMENT+'_build/%s_v4'%a.backend)).resolve()
    prefix=EXPERIMENT.upper()
    if a.action=='prepare':overlay.prepare(a.tree);print(prefix+'_PREPARE_PASSED');return
    marker=a.tree/getattr(overlay,'MARKER','.serial_pair_overlay.json')
    provenance=json.loads(marker.read_text()) if a.action=='collect' else overlay.prepare(a.tree)
    dest=a.destination/a.job/a.backend;dest.mkdir(parents=True,exist_ok=True)
    path=dest/('overlay_node%d.json'%a.node_rank);encoded=json.dumps(provenance,indent=2)
    if path.exists() and path.read_text()!=encoded:raise ValueError('overlay changed within job')
    path.write_text(encoded)
    if a.action=='collect':
        for suffix in ('build','check','run_gate','run'):collect(a,'%s_%s_%s'%(a.job,a.backend,suffix))
        print(prefix+'_COLLECT_PASSED');return
    job='%s_%s_%s'%(a.job,a.backend,a.action)
    if a.action in ('build','check'):
        try:subprocess.run(base_command(a,a.action,job),check=True)
        finally:collect(a,job)
    else:
        # Repeat the original read-only check immediately before GPU work.
        gate=a.job+'_'+a.backend+'_run_gate'
        try:subprocess.run(base_command(a,'check',gate),check=True)
        finally:collect(a,gate)
        root=a.output/a.job/a.backend/'cases'/job;node=root/('node%d'%a.node_rank)
        node.mkdir(parents=True,exist_ok=False)
        request={k:str(v) if isinstance(v,Path) else v for k,v in vars(a).items()}
        request.update(case=str(node),case_root=str(root));req=node/'request.json';req.write_text(json.dumps(request,indent=2))
        console=root/'console'/('node%d'%a.node_rank);console.mkdir(parents=True)
        try:
            with (console/'run.log').open('w') as log:
                proc=subprocess.Popen([sys.executable,'-u',str(a.tree/'test'/EXPERIMENT/'session_driver.py'),str(req)],stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
                print(prefix+'_RUNNING log='+str(console/'run.log'),flush=True)
                try:rc=proc.wait(timeout=1800)
                except (KeyboardInterrupt,subprocess.TimeoutExpired):
                    os.killpg(proc.pid,signal.SIGINT)
                    try:proc.wait(timeout=30)
                    except subprocess.TimeoutExpired:os.killpg(proc.pid,signal.SIGTERM);proc.wait(timeout=10)
                    raise
            if rc:raise RuntimeError('load worker failed rc=%d; provide collected logs'%rc)
        finally:collect(a,job)
    print('%s_%s_PASSED backend=%s rank=%d'%(prefix,a.action.upper(),a.backend,a.node_rank))
if __name__=='__main__':main()
