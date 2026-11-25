"""Read-only endpoint identity and module provenance; no context, WR or GPU use."""
import argparse
import datetime
import glob
import json
import os
from pathlib import Path
import re
import socket

from collect_kernel_sources import command, module_source_metadata
from eid_inventory import sha
from connection_diag import read


def compare_versions(loaded, installed, built):
    def equal(a,b):
        return a.upper()==b.upper() if a and b else None
    return dict(loaded_equals_installed=equal(loaded,installed),
                built_equals_loaded=equal(built,loaded),
                note='srcversion agreement supports provenance; it is not a proof of reproducible binary equality.')


def snapshot(device, source_root=None):
    result=dict(host=socket.gethostname(),utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        kernel=os.uname().release,boot_id=read('/proc/sys/kernel/random/boot_id'),device=device)
    root=Path('/sys/class/ubcore')/device
    result['device_path']=str(root.resolve())
    attrs=['guid','ubdev','driver_name','transport_type','dynamic_eid','ue_cnt',
           'virtualization','port_count','net_dev','feature','max_eid_cnt','max_upi_cnt']
    attrs+=['eids/eid'+str(i) for i in range(16)]
    result['device_attrs']={name:read(root/name) for name in attrs}
    result['namespaces']={name:os.readlink('/proc/self/ns/'+name) for name in ('net','user','mnt','pid')}
    result['ummu_attrs']={}
    patterns=('/sys/class/iommu/*/ummu-iommu', '/sys/devices/platform/ummu.*/ummu-iommu')
    for pattern in patterns:
        for directory in glob.glob(pattern)[:16]:
            for name in ('eid_list','features','tid_bits','ias','oas','options'):
                path=Path(directory)/name
                result['ummu_attrs'][str(path)]=read(path)
    result['commands']=[command(args) for args in (
        ['urma_admin','main_ue_eid','--help'],['urma_admin','main_ue_eid','show'])]
    built=module_source_metadata(source_root) if source_root else {}
    result['source_module_metadata']=built
    result['modules']={}
    for name in ('ummu','ummu_core','ubmem_vmmu','udma','ubcore','uburma'):
        loaded=read(Path('/sys/module')/name/'srcversion')
        installed=command(['modinfo',name])
        fields=dict(re.findall(r'^(filename|srcversion|vermagic):\s*(.+)$',installed.get('stdout',''),re.M))
        if fields.get('filename'):
            try:installed['binary_sha256']=sha(Path(fields['filename']))
            except OSError as exc:installed['binary_hash_error']=str(exc)
        result['modules'][name]=dict(loaded=loaded,installed=installed,
            version_comparison=compare_versions(loaded.get('text','').strip(),fields.get('srcversion'),built.get(name,{}).get('srcversion')))
    if source_root:
        result['source_release']=read(source_root/'include/generated/utsrelease.h')
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--device',default='udmac0d1e2')
    p.add_argument('--source-root',type=Path)
    a=p.parse_args()
    if not re.fullmatch('[a-zA-Z0-9_-]+',a.device):p.error('invalid device name')
    a.output.mkdir(parents=True,exist_ok=False)
    result=snapshot(a.device,a.source_root)
    (a.output/'endpoint_state.json').write_text(json.dumps(result,indent=2))
    print('ENDPOINT_STATE_COLLECTED output='+str(a.output),flush=True)
    print('Collection does not certify that the endpoint configuration is valid.',flush=True)
