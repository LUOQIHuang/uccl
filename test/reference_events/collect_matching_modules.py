"""Look at known build paths; copy only modules whose generated srcversion matches."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re

from collect_kernel_sources import command

MODULES=dict(ummu_core=('drivers/iommu/hisilicon/ummu-core/ummu-core.mod.c',),
    udma=('drivers/ub/urma/hw/udma/udma.mod.c','urma/hw/udma/udma.mod.c'),
    ubcore=('drivers/ub/urma/ubcore/ubcore.mod.c','urma/ubcore/ubcore.mod.c'),
    uburma=('drivers/ub/urma/uburma/uburma.mod.c','urma/uburma/uburma.mod.c'))


def candidates(roots, targets):
    rows=[]
    for root in roots:
        for module,rels in MODULES.items():
            for rel in rels:
                path=root/rel
                row=dict(module=module,path=str(path),expected=targets.get(module),matched=False)
                try:
                    data=path.read_bytes()
                    m=re.search(rb'MODULE_INFO\(srcversion,\s*"([0-9a-fA-F]+)"\)',data)
                    row['srcversion']=m.group(1).decode().upper() if m else None
                    row['sha256']=hashlib.sha256(data).hexdigest()
                    row['matched']=bool(row['srcversion'] and row['srcversion']==targets.get(module))
                except OSError as exc:row['error']=str(exc)
                rows.append(row)
    return rows


def copy_sources(root,dest):
    files={};size=0
    def failed(exc):raise exc
    for directory,dirs,names in os.walk(str(root),followlinks=False,onerror=failed):
        dirs[:]=[n for n in dirs if n not in ('.git','objects','__pycache__')]
        for name in names:
            p=Path(directory)/name
            if p.is_symlink() or '.mod.' in p.name or not(p.suffix in ('.c','.h','.S','.inc') or p.name in ('Makefile','Kbuild','Kconfig')):continue
            if len(files)>=10000 or size+p.stat().st_size>128*1024*1024:raise RuntimeError('COPY_LIMIT')
            data=p.read_bytes();relative=p.relative_to(root);out=dest/relative
            out.parent.mkdir(parents=True,exist_ok=True);out.write_bytes(data)
            files[relative.as_posix()]=dict(bytes=len(data),sha256=hashlib.sha256(data).hexdigest());size+=len(data)
    return files


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--state',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--root',action='append',type=Path,default=[])
    a=p.parse_args();a.output.mkdir(parents=True,exist_ok=False)
    state=json.loads(a.state.read_text())
    targets={name:state['modules'][name]['loaded'].get('text','').strip().upper() for name in MODULES}
    roots=list(a.root)
    for owner in ('f00955680','h00918771'):
        diag=Path('/home')/owner/'metax_dev/urma_after_kernel_20261029/diag'
        for build in ('gdr_build_753c812e','kernel_build_5c8e613f','kernel_candidate_e747a3b1'):
            roots.append(diag/build/'tree')
    roots=list(dict.fromkeys(roots))
    result=dict(targets=targets,candidates=candidates(roots,targets),copied={},errors=[],
        note='Matching generated srcversion is a source candidate, not a reproducible-binary proof. No compile, module load, device changes or benchmark.')
    for row in result['candidates']:
        name=row['module']
        if not row['matched'] or name in result['copied']:continue
        try:
            files=copy_sources(Path(row['path']).parent,a.output/'sources'/name)
            if not files:raise RuntimeError('NO_SOURCE_FILES')
            result['copied'][name]=dict(generated_module=row,files=files)
        except (OSError,RuntimeError) as exc:result['errors'].append(dict(module=name,error=str(exc)))
    result['not_found']=[name for name in targets if name not in result['copied']]
    current=state['device_attrs'].get('eids/eid0',{}).get('text','').strip()
    result['lookups']=[command(['urma_admin','main_ue_eid','lookup',eid]) for eid in
        dict.fromkeys((current,'0000:0000:0001:0200:0010:0000:df00:4200'))
        if re.fullmatch(r'(?:[0-9a-fA-F]{4}:){7}[0-9a-fA-F]{4}',eid)]
    (a.output/'matching_sources.json').write_text(json.dumps(result,indent=2))
    print('MATCHING_SOURCE_COLLECTION copied='+','.join(result['copied'])+' not_found='+','.join(result['not_found'])+' output='+str(a.output),flush=True)
    return 0 if not result['not_found'] and not result['errors'] else 1


if __name__=='__main__':raise SystemExit(main())
