"""Execute REAL load methods with simulated GPU/provider boundaries; CPU only."""
import argparse,os,shutil,subprocess,tempfile
from pathlib import Path
import json
from analyze import metrics
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--tree',type=Path,required=True)
    p.add_argument('--backend',choices=('urma','verbs'),required=True);p.add_argument('--sdk-include',type=Path)
    p.add_argument('--reference-sdk-fixture',action='store_true');a=p.parse_args();ep=a.tree.resolve()/'ep'
    body=r"""
int main(){
 cpu_set_t allowed;CPU_ZERO(&allowed);assert(sched_getaffinity(0,sizeof(allowed),&allowed)==0);
 int cpu=0;while(cpu<CPU_SETSIZE && !CPU_ISSET(cpu,&allowed))++cpu;assert(cpu<CPU_SETSIZE);
 cpu_set_t one;CPU_ZERO(&one);CPU_SET(cpu,&one);assert(sched_setaffinity(0,sizeof(one),&one)==0);
 for(size_t shape=0;shape<5;++shape)for(int sender:{0,1}){
  size_t lanes=shape>=3?3:(shape==2?4:1),limit=shape==0?1:(shape==3?3:(shape==4?12:4)),count=shape>=3?12:4;
  UrmaMicroSession a(2,1,459264,true,lanes),b(2,1,459264,true,lanes);
  a.connect({b.descriptor(0)});b.connect({a.descriptor(0)});
  a.load_prepare(limit,0,count);b.load_prepare(limit,1,count);
  a.experiment(sender==0,sender!=0,false,{cpu});b.experiment(sender==1,sender!=1,false,{cpu});
  for(size_t seq=0;seq<3;++seq){auto x=a.load_step(seq,limit,count);auto y=b.load_step(seq,limit,count);
   a.load_validate(seq);b.load_validate(seq);
   std::cout<<"LOAD_SAMPLE "<<(sender==0?x:y)<<std::endl;
  }
  a.close();b.close();
 }
 assert(sched_setaffinity(0,sizeof(allowed),&allowed)==0);
}
"""
    if a.backend=='verbs':
        body=body.replace('size_t shape=0','size_t shape=3').replace(
            'UrmaMicroSession a(2,1,459264,true,lanes),b(2,1,459264,true,lanes);',
            'VerbsWriteSession a("fake",3,1,459264,true,true,lanes),b("fake",3,1,459264,true,true,lanes);'
        ).replace(',false,{cpu}',',{cpu}')
    with tempfile.TemporaryDirectory(prefix='serial-native-') as t:
        t=Path(t);compiler=os.environ.get('CXX','c++')
        # Generic host regression deliberately uses capacity16. This diagnostic
        # needs24 outstanding ops; use64 ONLY in this simulation's temporary
        # shim. Production capacity remains min(2048,1024), unchanged.
        shim=t/'ep_shim.hpp'
        shim.write_text((ep/'tests/urma_support/ep_shim.hpp').read_text().replace('= 16;','= 64;'))
        flags=[compiler,'-std=c++17','-pthread','-Wall','-Wextra','-Werror','-I'+str(ep/'include'),
               '-I'+str(ep/'tests/urma_support'),'-include',str(shim)]
        if a.backend=='verbs':
            text=(ep/'tests/verbs_notify_test.cpp').read_text().split('int main(){',1)[0]
            text=text.replace('#include "../src/verbs_write_micro.cpp"','#include "'+str(ep/'src/verbs_write_micro.cpp')+'"')
            source=t/'verbs.cpp';source.write_text(text+body)
            build_and_check(flags+['-DUSE_DMABUF',str(source),'-libverbs','-o',str(t/'test')],t,12,a.backend)
            return
        if not a.sdk_include:p.error('URMA host simulation requires --sdk-include')
        sdk=a.sdk_include.resolve()
        if a.reference_sdk_fixture:
            shutil.copytree(sdk,t/'sdk');sdk=t/'sdk';types=sdk/'urma_types.h';text=types.read_text()
            if 'is_gpu_seg' not in text:types.write_text(text.replace('} urma_seg_cfg_t;','    uint8_t is_gpu_seg; /* test placeholder, NOT private ABI */\n} urma_seg_cfg_t;'))
        nested=t/'ub/umdk/urma';nested.parent.mkdir(parents=True);nested.symlink_to(sdk,target_is_directory=True)
        text=(ep/'tests/urma_transport_test.cpp').read_text().split('#ifndef UCCL_URMA_MICRO_TEST\nint main()',1)[0]
        helpers=(ep/'tests/urma_micro_test.inc').read_text().split('int main() {',1)[0]
        helpers=helpers.replace('allocate_atomic_buffer(81960,device)','allocate_device(81960,device)').replace('acquire_buffer_ownership(p,true,device)','acquire_buffer_ownership(p,false,device)')
        source=t/'urma.cpp';source.write_text(text+helpers+body)
        # A fake CUDA allocation is the simulated GPU in this test. Override
        # ONLY the test runtime's GPU-control classification, never the tree.
        runtime=(ep/'src/gpu_p2p_runtime.cpp').read_text();start=runtime.index('bool atomic_buffer_is_gpu_memory()');end=runtime.index('void set_device(',start)
        runtime=runtime[:start]+'bool atomic_buffer_is_gpu_memory(){return true;}\n'+runtime[end:]
        start=runtime.index('void prepare_atomic_buffer(');end=runtime.index('void release_memory(',start)
        runtime=runtime[:start]+'void prepare_atomic_buffer(void* p,size_t n,int d){prepare_main_buffer(p,n,d);}\n'+runtime[end:]
        gpu=t/'runtime.cpp';gpu.write_text(runtime)
        cmd=flags+['-DUSE_URMA','-DUCCL_URMA_MICRO_TEST','-I'+str(sdk),'-I'+str(t),'-I'+str(ep/'tests'),str(gpu),str(ep/'src/urma_transport.cpp'),str(ep/'src/urma_micro.cpp'),str(source),'-o',str(t/'test')]
        build_and_check(cmd,t,30,a.backend)

def build_and_check(cmd, directory, expected, backend):
    build=subprocess.run(cmd,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,universal_newlines=True)
    if build.returncode:raise RuntimeError(build.stdout)
    run=subprocess.run([str(directory/'test')],stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True,timeout=60)
    if run.returncode:raise RuntimeError(run.stderr[-6000:])
    samples=[json.loads(l.split(' ',1)[1]) for l in run.stdout.splitlines() if l.startswith('LOAD_SAMPLE ')]
    if len(samples)!=expected:raise ValueError('missing simulated load samples')
    for s in samples:metrics(s,s['data_lanes'],s['max_inflight_pairs'],s['pairs_expected'])
    print('PAIR_LOAD_NATIVE_SIMULATION_PASSED backend='+backend+' samples='+str(len(samples)))
if __name__=='__main__':main()
