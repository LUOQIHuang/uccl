"""Copy a bounded UMMU/UDMA source subset; no build, benchmark or device changes."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess

SUBTREES = ('drivers/iommu/hisilicon', 'drivers/ub/urma/hw/udma',
            'drivers/ub/urma/ubcore', 'drivers/ub/urma/uburma',
            'include/urma', 'include/uapi/urma', 'include/ub', 'include/uapi/ub')
MAX_BYTES = 128 * 1024 * 1024


def collect_sources(source_root, output, extras=()):
    source_root = source_root.resolve()
    output.mkdir(parents=True, exist_ok=False)
    result = dict(source_root=str(source_root), files={}, missing=[], errors=[], total_bytes=0,
                  note='Copied sources are evidence, not proof that this tree produced the installed modules.')
    def walk_error(exc):
        result['errors'].append(dict(path=str(exc.filename),error=str(exc)))
    roots = [(source_root/rel, Path('kernel')/rel) for rel in SUBTREES]
    roots += [(source_root/rel, Path('kernel')/rel) for rel in
              ('Makefile','include/generated/utsrelease.h','include/generated/compile.h','include/generated/autoconf.h')]
    for base in ('include/linux', 'include/uapi/linux'):
        for pattern in ('*ummu*.h','*ubcore*.h','*uburma*.h'):
            roots += [(p,Path('kernel')/p.relative_to(source_root)) for p in (source_root/base).glob(pattern)]
    roots += [(p.resolve(), Path('extra')/label) for label,p in extras]
    seen = set()
    for source, target in roots:
        if not source.exists():
            result['missing'].append(str(source)); continue
        files = [source] if source.is_file() else (Path(directory)/name
            for directory, dirs, names in os.walk(str(source), followlinks=False, onerror=walk_error)
            for name in names)
        for path in files:
            if path.is_symlink() or not (path.suffix in ('.c','.h','.S','.inc') or path.name in ('Makefile','Kconfig','Kbuild')):
                continue
            try:
                actual=path.resolve()
                if actual in seen:
                    continue
                seen.add(actual)
                relative=target if source.is_file() else target/path.relative_to(source)
                if len(result['files'])>=10000 or result['total_bytes']+path.stat().st_size>MAX_BYTES:
                    raise RuntimeError('SOURCE_COPY_LIMIT_EXCEEDED')
                data=path.read_bytes()
                if result['total_bytes']+len(data)>MAX_BYTES:
                    raise RuntimeError('SOURCE_COPY_LIMIT_EXCEEDED')
                dest=output/'sources'/relative
                dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(data)
                result['files'][relative.as_posix()]=dict(bytes=len(data),sha256=hashlib.sha256(data).hexdigest())
                result['total_bytes']+=len(data)
            except OSError as exc:
                result['errors'].append(dict(path=str(path),error=str(exc)))
    return result


def command(argv):
    try:
        r=subprocess.run(argv,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=15)
        return dict(command=argv,rc=r.returncode,stdout=r.stdout.decode('utf-8','replace'),stderr=r.stderr.decode('utf-8','replace'))
    except (OSError,subprocess.TimeoutExpired) as exc:
        return dict(command=argv,error=str(exc))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source-root',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--extra-root',action='append',default=[],help='label=/absolute/source/directory, optional')
    a=p.parse_args()
    extras=[]
    for item in a.extra_root:
        label,sep,path=item.partition('=')
        if not sep or not label or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in label) or not Path(path).is_absolute():
            p.error('--extra-root requires a simple label and absolute directory')
        extras.append((label,Path(path)))
    if not a.source_root.is_dir():p.error('source root does not exist or is unreadable')
    # Keep the destination outside the source tree being traversed.
    for root in [a.source_root]+[path for label,path in extras]:
        if root.resolve()==a.output.resolve() or root.resolve() in a.output.resolve().parents:
            p.error('output must be outside the source trees')
    try:
        result=collect_sources(a.source_root,a.output,extras)
    except RuntimeError as exc:
        (a.output/'kernel_source_manifest.json').write_text(json.dumps(dict(
            source_root=str(a.source_root),error=str(exc),note='Partial copied files retained; collection stopped at limit.'),indent=2))
        print('KERNEL_SOURCE_COLLECTION_PARTIAL error='+str(exc),flush=True)
        return 1
    result['commands']=[command(c) for c in (
        ['uname','-a'],['git','-C',str(a.source_root),'rev-parse','HEAD'],
        ['git','-C',str(a.source_root),'diff','--stat','--',*SUBTREES])]
    result['modules']={}
    for name in ('ummu','udma','ubcore','uburma'):
        info=command(['modinfo',name]);result['modules'][name]=info
        path=next((line.split(':',1)[1].strip() for line in info.get('stdout','').splitlines() if line.startswith('filename:')),None)
        if path:
            try:info['installed_binary_sha256']=hashlib.sha256(Path(path).read_bytes()).hexdigest()
            except OSError as exc:info['binary_hash_error']=str(exc)
    (a.output/'kernel_source_manifest.json').write_text(json.dumps(result,indent=2))
    complete=all(any(key.startswith('kernel/'+prefix+'/') for key in result['files']) for prefix in SUBTREES[:2])
    print('KERNEL_SOURCE_COLLECTION_'+('PASSED' if complete and not result['errors'] else 'PARTIAL')+
          ' files='+str(len(result['files']))+' bytes='+str(result['total_bytes'])+' output='+str(a.output),flush=True)
    return 0 if complete and not result['errors'] else 1


if __name__=='__main__':raise SystemExit(main())
