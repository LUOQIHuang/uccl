"""Execute REAL serial methods with simulated GPU/provider boundaries; CPU only."""
import argparse,os,shutil,subprocess,tempfile
from pathlib import Path
import json
from analyze import metrics
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--tree',type=Path,required=True)
    p.add_argument('--backend',choices=('verbs','urma'),required=True);p.add_argument('--sdk-include',type=Path)
    p.add_argument('--reference-sdk-fixture',action='store_true');a=p.parse_args();ep=a.tree.resolve()/'ep'
    ctor='UrmaMicroSession a(2,1,459264,true),b(2,1,459264,true);' if a.backend=='urma' else 'VerbsWriteSession a("fake",3,1,459264,true,true),b("fake",3,1,459264,true,true);'
    connect='a.connect({b.descriptor(0)});b.connect({a.descriptor(0)});'
    extra='false,' if a.backend=='urma' else ''
    body='''
int main(){
 cpu_set_t allowed;CPU_ZERO(&allowed);assert(sched_getaffinity(0,sizeof(allowed),&allowed)==0);
 int cpu=0;while(cpu<CPU_SETSIZE && !CPU_ISSET(cpu,&allowed))++cpu;assert(cpu<CPU_SETSIZE);
 cpu_set_t one;CPU_ZERO(&one);CPU_SET(cpu,&one);assert(sched_setaffinity(0,sizeof(one),&one)==0);
 CTOR
 CONNECT
 for(size_t count:{size_t(1),size_t(2)})for(int sender:{0,1}){
  a.serial_prepare(count,0);b.serial_prepare(count,1);
  a.experiment(sender==0,sender!=0,EXTRA{cpu});b.experiment(sender==1,sender!=1,EXTRA{cpu});
  for(size_t seq=0;seq<3;++seq){auto x=a.serial_step(seq,count);auto y=b.serial_step(seq,count);
   a.serial_validate(seq,count);b.serial_validate(seq,count);
   std::cout<<"SERIAL_SAMPLE "<<(sender==0?x:y)<<'\\n';
  }
 }
 a.close();b.close();assert(sched_setaffinity(0,sizeof(allowed),&allowed)==0);
}
'''.replace('CTOR',ctor).replace('CONNECT',connect).replace('EXTRA',extra)
    with tempfile.TemporaryDirectory(prefix='serial-native-') as t:
        t=Path(t);compiler=os.environ.get('CXX','c++')
        flags=[compiler,'-std=c++17','-pthread','-Wall','-Wextra','-Werror','-I'+str(ep/'include'),
               '-I'+str(ep/'tests/urma_support'),'-include',str(ep/'tests/urma_support/ep_shim.hpp')]
        if a.backend=='verbs':
            text=(ep/'tests/verbs_notify_test.cpp').read_text().split('int main(){',1)[0]
            text=text.replace('#include "../src/verbs_write_micro.cpp"','#include "'+str(ep/'src/verbs_write_micro.cpp')+'"')
            source=t/'verbs.cpp';source.write_text(text+body)
            cmd=flags+['-DUSE_DMABUF',str(source),'-libverbs','-o',str(t/'test')]
        else:
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
        build=subprocess.run(cmd,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,universal_newlines=True)
        if build.returncode:raise RuntimeError(build.stdout)
        run=subprocess.run([str(t/'test')],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,universal_newlines=True,timeout=60)
        if run.returncode:raise RuntimeError(run.stdout)
        samples=[json.loads(l.split(' ',1)[1]) for l in run.stdout.splitlines() if l.startswith('SERIAL_SAMPLE ')]
        if len(samples)!=12:raise ValueError('missing simulated serial samples')
        for s in samples:metrics(s,s['pairs_expected'])
        print('SERIAL_NATIVE_SIMULATION_PASSED backend='+a.backend+' samples='+str(len(samples))+'; not hardware/private ABI validation')
if __name__=='__main__':main()
