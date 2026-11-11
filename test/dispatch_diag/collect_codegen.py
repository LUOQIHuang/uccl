"""Read existing ELF/compiler diagnostics; never import torch or run a GPU task."""
import argparse
import hashlib
import json
import mmap
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import tempfile


def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b''):
            result.update(chunk)
    return result.hexdigest()


def elf(data, start=0):
    """Parse ELF sections at an offset, including embedded device ELF images."""
    if data[start:start+4] != b'\x7fELF':
        raise ValueError('not ELF')
    cls, order = data[start+4], data[start+5]
    if cls not in (1, 2) or order not in (1, 2):
        raise ValueError('unsupported ELF encoding')
    endian = '<' if order == 1 else '>'
    header = struct.Struct(endian + ('HHIQQQIHHHHHH' if cls == 2 else 'HHIIIIIHHHHHH'))
    h = header.unpack_from(data, start+16)
    section = struct.Struct(endian + ('IIQQQQIIQQ' if cls == 2 else 'IIIIIIIIII'))
    shoff, shsize, count, names = h[5], h[10], h[11], h[12]
    if not shoff or shsize < section.size:
        raise ValueError('ELF has no usable section table')
    first = section.unpack_from(data, start+shoff)
    count = count or first[5]
    names = first[6] if names == 65535 else names
    if not 0 < count <= 65536 or not 0 <= names < count or start+shoff+shsize*count > len(data):
        raise ValueError('invalid ELF section table')
    sections = [section.unpack_from(data, start+shoff+i*shsize) for i in range(count)]
    ns = sections[names]
    if start+ns[4]+ns[5] > len(data):
        raise ValueError('invalid ELF string table')
    strings = data[start+ns[4]:start+ns[4]+ns[5]]
    result = []
    end = max(16+header.size, shoff+shsize*count)
    for s in sections:
        if s[0] >= len(strings):
            raise ValueError('invalid ELF section name')
        name = strings[s[0]:].split(b'\0', 1)[0].decode('utf-8', errors='replace')
        if s[1] != 8:
            if start+s[4]+s[5] > len(data):
                raise ValueError('ELF section out of bounds')
            end = max(end, s[4]+s[5])
        result.append(dict(name=name, type=s[1], flags=s[2], offset=s[4], size=s[5]))
    return dict(offset=start, size=end, bits=64 if cls == 2 else 32,
                endian=order, machine=h[1], sections=result)


def images(data, max_images=8):
    found = []
    offset = 0
    while len(found) < max_images:
        offset = data.find(b'\x7fELF', offset)
        if offset < 0:
            break
        try:
            found.append(elf(data, offset))
        except (ValueError, struct.error, IndexError):
            pass
        offset += 4
    return found


def run_report(command, path):
    outcome = dict(command=list(map(str, command)))
    try:
        with path.open('wb') as stream:
            p = subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT, timeout=45)
        outcome['rc'] = p.returncode
    except (OSError, subprocess.TimeoutExpired) as exc:
        outcome['error'] = str(exc)
    if path.exists() and path.stat().st_size > 2*1024*1024:
        with path.open('r+b') as stream:
            stream.truncate(2*1024*1024)
        outcome['truncated_at_bytes'] = 2*1024*1024
    return outcome


def collect(base, trace, output, maca, max_image_mib=128):
    manifests = [json.loads((p/'manifest.json').read_text()) for p in (base, trace)]
    b, t = manifests
    if b['sources'] != t['sources'] or b.get('sdk', {}) != t.get('sdk', {}):
        raise ValueError('different build source/SDK inputs')
    clean = lambda m: {k:v for k,v in m['flags'].items() if k not in ('UCCL_GPU_STAGE_TRACE', 'UCCL_GPU_STAGE_ROLES_ONLY')}
    if clean(b) != clean(t) or b['flags']['UCCL_GPU_STAGE_TRACE'] != '0' or t['flags'].get('UCCL_GPU_STAGE_ROLES_ONLY') != '1' or t['flags']['UCCL_GPU_STAGE_TRACE'] != '1':
        raise ValueError('requires matching base and roles-only trace builds')
    for build, manifest in zip((base, trace), manifests):
        if digest(build/manifest['module']) != manifest['module_sha']:
            raise ValueError('module SHA mismatch')
    output.mkdir(parents=True, exist_ok=False)
    report = dict(builds={}, tools={}, artifacts=[], max_images_per_artifact=8, note='Read-only existing objects/module analysis. No GPU benchmark, GPU import or compilation. Embedded ELF candidates are identified structurally, not assumed to be device code. Missing resource counts are not zero counts. At most eight ELF candidates per artifact are inspected; extraction limits/skips are recorded.')
    tool_dirs = [maca/'mxgpu_llvm/bin', maca/'bin', maca/'tools/cu-bridge/bin', maca/'tools/cu-bridge/tools']
    report['available_tools'] = [str(p) for d in tool_dirs if d.is_dir() for p in sorted(d.iterdir()) if p.is_file() and re.search(r'(objdump|readelf|readobj|cuobjdump|cucc|llvm-nm)', p.name)]
    tool = lambda name: next((str(d/name) for d in tool_dirs if (d/name).is_file()), shutil.which(name))
    readelf = tool('llvm-readelf') or tool('readelf')
    compiler = tool('cucc')
    if compiler:
        for flag in ('--version', '--help'):
            report['tools']['cucc'+flag] = run_report([compiler, flag], output/('cucc_'+flag[2:]+'.txt'))
    if readelf:
        report['tools']['readelf'] = run_report([readelf, '--version'], output/'readelf_version.txt')
    for kind, build, manifest in zip(('base', 'roles'), (base, trace), manifests):
        folder = output/kind
        folder.mkdir()
        report['builds'][kind] = dict(path=str(build), manifest=manifest)
        for name in ('manifest.json', 'build_request.json', 'request.json', 'build.log', 'host_tests.log'):
            if (build/name).is_file():
                shutil.copy2(build/name, folder/name)
        paths = [('module', build/manifest['module']), ('internode', build/'objects/src/internode.o'), ('trace_runtime', build/'objects/src/gpu_stage_trace.o')]
        for label, path in paths:
            item = dict(build=kind, label=label, path=str(path), commands=[])
            report['artifacts'].append(item)
            if not path.is_file():
                item['missing'] = True
                continue
            item['file_bytes'] = path.stat().st_size
            item['sha256'] = digest(path)
            with path.open('rb') as stream, mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as data:
                item['images'] = images(data)
                for i, info in enumerate(item['images']):
                    prefix = folder/('%s_elf%02d' % (label, i))
                    if info['size'] > max_image_mib*1024*1024:
                        info['tool_skipped'] = 'image exceeds extraction limit; section sizes retained'
                        continue
                    with tempfile.TemporaryDirectory(prefix='uccl-codegen-') as tmp:
                        target = Path(tmp)/'image.elf'
                        target.write_bytes(data[info['offset']:info['offset']+info['size']])
                        if readelf:
                            for flag, suffix in (('-hW','header'),('-SW','sections'),('-nW','notes'),('-sW','symbols')):
                                item['commands'].append(run_report([readelf, flag, str(target)], Path(str(prefix)+'_'+suffix+'.txt')))
                            for j, section in enumerate(info['sections']):
                                if section['size'] and section['size'] <= 1024*1024 and re.search(r'(metadata|note|resource|kernel_info)', section['name'], re.I):
                                    item['commands'].append(run_report([readelf, '-x', str(j), str(target)], Path(str(prefix)+'_section%d.txt' % j)))
    (output/'codegen.json').write_text(json.dumps(report, indent=2))
    print('DISPATCH_CODEGEN_COLLECTED ' + str(output))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--base-build', type=Path, required=True)
    p.add_argument('--trace-build', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--maca-path', type=Path, default=Path(os.environ.get('MACA_PATH', '/opt/maca-3.9.0')))
    p.add_argument('--max-image-mib', type=int, default=128)
    a = p.parse_args()
    if not 1 <= a.max_image_mib <= 512:
        p.error('max-image-mib must be 1..512')
    collect(a.base_build.resolve(), a.trace_build.resolve(), a.output.resolve(), a.maca_path.resolve(), a.max_image_mib)
