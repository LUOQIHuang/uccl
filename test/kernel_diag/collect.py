"""Read-only installed-kernel comparison; no module loads or GPU contexts."""
import argparse
import csv
import datetime
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess

VERBS='6.6.0-145.3.34.165.oe2403sp3.aarch64'
URMA='6.6.0-gdr_f00955680_diag'
MODULES=('metax','udma_metax_p2p_bridge','udma','ubcore','uburma','ummu','ummu_core',
         'ib_core','ib_uverbs','rdma_ucm','hns_roce','mlx5_core','mlx5_ib')
FOCUS=re.compile(r'CONFIG_(PREEMPT|HZ|NO_HZ|NUMA|SCHED|CPU_FREQ|CPU_IDLE|IOMMU|ARM_SMMU|HISI|UB|UMMU|METAX|DRM|HMM|SWIOTLB|INFINIBAND|RDMA|DMA|PCI|ZONE_DEVICE|DEV_DAX|TRANSPARENT_HUGEPAGE|DEBUG|KASAN|KCSAN|LOCKDEP|PROVE_LOCKING|FTRACE|FUNCTION_TRACER|KEXEC|CRASH|OOM)')
LIMIT=4*1024*1024

def read_file(path):
    path=Path(path)
    try:
        # proc/sys handlers may reject a multi-MiB read request with ENOMEM
        # even when the machine has ample RAM. Bound individual read sizes too.
        chunks=[];size=0
        with path.open('rb',buffering=0) as f:
            while size<LIMIT+1:
                block=f.read(min(4096,LIMIT+1-size))
                if not block:break
                chunks.append(block);size+=len(block)
        data=b''.join(chunks)
        return dict(path=str(path),text=data[:LIMIT].decode('utf8','replace'),truncated=len(data)>LIMIT)
    except OSError as exc:return dict(path=str(path),error=str(exc))

def digest(path):
    try:
        h=hashlib.sha256()
        with Path(path).open('rb') as f:
            for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
        return dict(sha256=h.hexdigest(),bytes=Path(path).stat().st_size)
    except OSError as exc:return dict(error=str(exc))

def command(args,timeout=15):
    try:
        r=subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True,timeout=timeout)
        return dict(command=args,rc=r.returncode,stdout=r.stdout[:LIMIT],stderr=r.stderr[:LIMIT],truncated=len(r.stdout)>LIMIT or len(r.stderr)>LIMIT)
    except (OSError,subprocess.TimeoutExpired) as exc:return dict(command=args,error=str(exc))

def config_values(text):
    out={}
    for line in text.splitlines():
        if line.startswith('CONFIG_') and '=' in line:
            k,v=line.split('=',1);out[k]=v
        else:
            m=re.match(r'# (CONFIG_\w+) is not set$',line)
            if m:out[m.group(1)]='n'
    return out

def parse_modinfo(text):
    out={}
    for line in text.splitlines():
        if ':' in line:
            key,value=line.split(':',1);out.setdefault(key,[]).append(value.strip())
    return out

def inspect_kernel(release,directory,module_root=Path('/lib/modules'),boot=Path('/boot')):
    directory.mkdir(parents=True)
    tree=module_root/release
    result=dict(release=release,module_tree=str(tree),module_tree_exists=tree.is_dir(),config=None,modules={})
    for candidate in (boot/('config-'+release),tree/'build/.config',tree/'source/.config'):
        data=read_file(candidate)
        if 'error' not in data:
            (directory/'config.txt').write_text(data['text'])
            result['config']=dict(source=str(candidate),truncated=data['truncated'],values=config_values(data['text']),**digest(candidate));break
    builtins=read_file(tree/'modules.builtin')
    builtin_names={Path(line).name.split('.ko')[0].replace('-','_') for line in builtins.get('text','').splitlines()}
    result['builtin_index_available']='error' not in builtins
    modinfo=shutil.which('modinfo')
    for module in MODULES:
        raw=command([modinfo,'-k',release,module]) if modinfo else dict(error='modinfo command unavailable')
        fields=parse_modinfo(raw.get('stdout','')) if raw.get('rc')==0 else {}
        filename=next(iter(fields.get('filename',[])),None)
        magic=next(iter(fields.get('vermagic',[])),None)
        item=dict(metadata_resolved=bool(fields),builtin_listed=module in builtin_names,metadata=fields,query=raw)
        if magic:item['vermagic_release_matches']=magic.split()[0]==release
        if filename and filename.startswith('/'):
            item['file']=dict(path=filename,resolved_path=str(Path(filename).resolve()),**digest(filename))
        result['modules'][module]=item
    for name in ('build','source'):
        p=tree/name;result[name+'_path']=str(p.resolve()) if p.exists() else None
    return result

def runtime_snapshot():
    files=('/proc/version','/proc/cmdline','/proc/meminfo','/proc/interrupts',
           '/proc/sys/kernel/numa_balancing','/proc/sys/kernel/printk',
           '/proc/sys/kernel/panic','/proc/sys/kernel/panic_on_oops','/proc/sys/vm/panic_on_oom','/proc/sys/vm/overcommit_memory',
           '/sys/devices/system/cpu/online','/sys/devices/system/cpu/isolated',
           '/sys/kernel/security/lockdown','/sys/fs/cgroup/memory.max',
           '/sys/fs/cgroup/memory.current','/sys/fs/cgroup/memory.events')
    result=dict(files={p:read_file(p) for p in files},modules={},pci={},network={},cpufreq={})
    # No '=' in this setpci invocation: read Device Control 2 only, never write.
    result['atomic_control']=command(['setpci','-s','0000:a0:08.0','CAP_EXP+28.w'],5)
    for module in MODULES:
        base=Path('/sys/module')/module
        d=dict(present=base.is_dir(),fields={},parameters={})
        if base.is_dir():
            for name in ('version','srcversion','taint','initstate'):d['fields'][name]=read_file(base/name)
            try:params=sorted((base/'parameters').iterdir())
            except OSError:params=[]
            for p in params:
                if p.is_file():d['parameters'][p.name]=read_file(p)
        result['modules'][module]=d
    for address in ('0000:a1:00.0','0000:a0:08.0'):
        base=Path('/sys/bus/pci/devices')/address
        result['pci'][address]=dict(present=base.exists(),resolved_path=str(base.resolve()),driver=str((base/'driver').resolve()) if (base/'driver').exists() else None,
          fields={n:read_file(base/n) for n in ('vendor','device','numa_node','local_cpulist','current_link_speed','current_link_width','power/control','power/runtime_status')})
    for family in ('infiniband','uburma'):
        base=Path('/sys/class')/family
        try:devices=sorted(base.iterdir())
        except OSError:devices=[]
        for dev in devices:
            entry=dict(resolved_path=str(dev.resolve()),fields={})
            entry['driver']=str((dev/'device/driver').resolve()) if (dev/'device/driver').exists() else None
            entry['numa_node']=read_file(dev/'device/numa_node')
            if family=='infiniband':
                for n in ('state','phys_state','active_mtu','rate','link_layer','gids/1','gids/3','gid_attrs/types/1','gid_attrs/types/3'):
                    entry['fields'][n]=read_file(dev/'ports/1'/n)
            else:entry['devnode_present']=(Path('/dev/uburma')/dev.name).exists()
            result['network'][family+'/'+dev.name]=entry
    for p in sorted(Path('/sys/devices/system/cpu/cpufreq').glob('policy*')):
        result['cpufreq'][p.name]={n:read_file(p/n) for n in ('affected_cpus','scaling_driver','scaling_governor','scaling_min_freq','scaling_max_freq')}
    try:result['collector_affinity']=sorted(os.sched_getaffinity(0))
    except AttributeError:pass
    return result

def render(data,out):
    kernels=data['kernels'];a,b=kernels
    differences=[]
    av=(a['config'] or {}).get('values',{});bv=(b['config'] or {}).get('values',{})
    complete=bool(av and bv and not a['config']['truncated'] and not b['config']['truncated'])
    if complete:
        for name in sorted(av.keys()|bv.keys()):
            if av.get(name)!=bv.get(name):differences.append(dict(symbol=name,verbs=av.get(name,'<not listed>'),urma=bv.get(name,'<not listed>'),focus=bool(FOCUS.match(name))))
    with (out/'config_diff.csv').open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=['symbol','verbs','urma','focus']);w.writeheader();w.writerows(differences)
    lines=['# 内核环境只读诊断','', '当前运行内核：`'+data['running_kernel']+'`；节点：'+data['host'],
      '', '两套内核的下列模块信息来自磁盘，不代表非运行内核的实际加载状态，也不证明GPU注册/通信可用。',
      '', '| 模块 | verbs内核磁盘记录 | URMA内核磁盘记录 |','| --- | --- | --- |']
    for module in MODULES:
        cells=[]
        for kernel in kernels:
            m=kernel['modules'][module]
            state='已解析' if m['metadata_resolved'] else 'builtin清单列出' if m['builtin_listed'] else '未解析（需核实文件/索引/权限/名称）'
            version=', '.join(m['metadata'].get('version',[])) or '版本未提供'
            if m.get('vermagic_release_matches') is False:state+='；vermagic版本不符'
            cells.append(state+'；'+version)
        lines.append('| '+module+' | '+' | '.join(cells)+' |')
    lines+=['','配置文件：'+('两套可读，共发现%d项差异，见config_diff.csv。'%len(differences) if complete else '至少一套不可读或截断，不能输出完整配置差异。'),
      '', '## 当前内核的URMA GPU路径预检依赖（仅文件状态）','']
    rt=data['runtime']
    for module in ('udma','ubcore','uburma','metax','udma_metax_p2p_bridge'):
        lines.append('- `/sys/module/'+module+'`：'+('存在' if rt['modules'][module]['present'] else '未观察到；不等于已证明功能缺失'))
    control=rt.get('atomic_control',{}).get('stdout','').strip()
    if re.fullmatch(r'[0-9a-fA-F]{4}',control):lines.append('- 当前Root Port Device Control 2=`'+control+'`，Atomic Request bit6='+('1' if int(control,16)&0x40 else '0')+'；这是当前状态，不是另一内核的历史状态。')
    for kernel in kernels:
        if kernel['release']!=data['running_kernel']:continue
        for module in MODULES:
            loaded=rt['modules'][module]['fields'].get('srcversion',{}).get('text','').strip()
            installed=kernel['modules'][module]['metadata'].get('srcversion',[])
            if loaded and installed and loaded!=installed[0]:lines+=['','注意：'+module+'已加载srcversion与磁盘模块不同，不能用磁盘记录替代运行模块身份。']
    lines+=['','边界：未执行modprobe/insmod/rmmod、PCI写入、GPU管理查询、编译或benchmark。未创建GPU上下文。',
      '未解析模块不等于内核不支持；版本或二进制哈希不同不等于性能差异。完整补丁差异仍需源码基线、补丁集及实际安装构建的对应关系。',
      '文件/命令不可读、权限不足、工具缺失会保留在inventory.json，不据此断言驱动不支持。']
    (out/'SUMMARY.zh-CN.md').write_text('\n'.join(lines)+'\n')

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--node-rank',type=int,choices=(0,1),required=True);p.add_argument('--job',required=True)
    p.add_argument('--output',type=Path,default=Path.home()/'metax_dev/kernel_diag_results')
    p.add_argument('--verbs-kernel',default=VERBS);p.add_argument('--urma-kernel',default=URMA)
    p.add_argument('--journal',action='store_true',help='read last 500 kernel log lines from current/previous boot')
    a=p.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_-]+',a.job):p.error('invalid job')
    for release in (a.verbs_kernel,a.urma_kernel):
        if not re.fullmatch(r'[A-Za-z0-9_.+-]+',release):p.error('invalid kernel release')
    if a.verbs_kernel==a.urma_kernel:p.error('two distinct releases required')
    out=a.output.resolve()/a.job/('node'+str(a.node_rank));out.mkdir(parents=True,exist_ok=False)
    data=dict(schema=1,host=platform.node(),node_rank=a.node_rank,running_kernel=platform.release(),utc=datetime.datetime.utcnow().isoformat()+'Z',kernels=[],runtime=runtime_snapshot())
    for index,release in enumerate((a.verbs_kernel,a.urma_kernel)):
        data['kernels'].append(inspect_kernel(release,out/('verbs_kernel' if index==0 else 'urma_kernel')))
    if a.journal:
        data['journal']={}
        for boot in ('0','-1'):
            r=command(['journalctl','-b',boot,'-k','-n','500','--no-pager'],20)
            (out/('kernel_boot_current.log' if boot=='0' else 'kernel_boot_previous.log')).write_text(r.get('stdout','')+r.get('stderr','')+r.get('error',''))
            data['journal'][boot]={k:v for k,v in r.items() if k not in ('stdout','stderr')}
    (out/'inventory.json').write_text(json.dumps(data,indent=2))
    render(data,out)
    print('KERNEL_DIAG_COLLECTED '+str(out))
    print('Collection complete; inspect missing evidence in SUMMARY/inventory. No runtime compatibility claimed.')

if __name__=='__main__':main()
