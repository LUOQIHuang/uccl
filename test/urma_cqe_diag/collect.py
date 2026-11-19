#!/usr/bin/env python3
"""Identify the uDMA provider and list visible completion-related trace formats."""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import re
import shutil
import subprocess


REPO = Path(__file__).resolve().parents[2]
REFERENCE = (REPO / 'test/fadd_path/results/urma_fadd_tail_scope_01/urma/cases'
             / 'urma_fadd_tail_scope_01_urma_tail_probe/node1')
LIBRARY = re.compile(r'^lib(?:urma(?:[_-](?:common|udma))?|udma)(?:\.so)(?:\..*)?$')
UB_EVENT = re.compile(r'udma|ubcore|uburma|urma', re.I)
CQ_EVENT = re.compile(r'cqe|jfc|complet|(?:^|_)cq(?:_|$)|poll', re.I)


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def run(*args):
    if not shutil.which(args[0]):
        return dict(output=None, error='command unavailable: '+args[0])
    try:
        result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                encoding='utf-8', errors='replace', timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return dict(output=None, error=str(exc))
    return dict(output=result.stdout if not result.returncode else None,
                error=(result.stderr.strip() or str(result.returncode)) if result.returncode else None)


def digest(path):
    with path.open('rb') as stream:
        h = hashlib.sha256()
        for block in iter(lambda: stream.read(1024*1024), b''):
            h.update(block)
        return h.hexdigest()


def mapped_libraries(pid):
    """Optional snapshot of a caller-specified process that already exists."""
    if pid is None:
        return dict(pid=None, libraries=[], error=None)
    try:
        lines = (Path('/proc') / str(pid) / 'maps').read_text(errors='replace').splitlines()
    except OSError as exc:
        return dict(pid=pid, libraries=[], error=str(exc))
    paths = set()
    for line in lines:
        fields = line.split(maxsplit=5)
        if len(fields) == 6 and fields[5].startswith('/'):
            path = fields[5]
            filename = path[:-10] if path.endswith(' (deleted)') else path
            if LIBRARY.fullmatch(Path(filename).name):
                paths.add(path)
    return dict(pid=pid, libraries=sorted(paths), error=None)


def library_records(roots, archived, live):
    candidates = {Path(p) for p in archived+live if not p.endswith(' (deleted)')}
    for root in roots:
        for pattern in ('liburma*.so*', 'libudma*.so*'):
            candidates.update(p for p in root.glob(pattern) if LIBRARY.fullmatch(p.name))
    records = {}
    errors = []
    for path in sorted(candidates):
        try:
            resolved = path.resolve(strict=True)
            if not resolved.is_file():
                continue
            if resolved not in records:
                sha = digest(resolved)
                note = run('readelf', '-n', str(resolved))
                build_id = re.search(r'Build ID:\s*([a-fA-F0-9]+)', note['output'] or '')
                symbols = run('readelf', '-Ws', str(resolved))
                names = sorted({line.split()[-1] for line in (symbols['output'] or '').splitlines()
                                if re.search(r'\b(?:udma_[\w]*(?:poll|cqe|jfc)[\w]*|urma_poll_jfc)\b', line)})
                records[resolved] = dict(path=str(resolved), aliases=[], sha256=sha,
                                         build_id=build_id.group(1) if build_id else None,
                                         readelf_error=note['error'] or symbols['error'], candidate_symbols=names,
                                         archived_map_paths=[], live_map_paths=[])
            entry = records[resolved]
            entry['aliases'].append(str(path))
            if str(path) in archived:
                entry['archived_map_paths'].append(str(path))
            if str(path) in live:
                entry['live_map_paths'].append(str(path))
        except OSError as exc:
            errors.append(dict(path=str(path), error=str(exc)))
    return list(records.values()), errors


def trace_catalog(roots):
    attempts = []
    for root in roots:
        path = root/'available_events'
        try:
            available = path.read_text(encoding='utf-8').splitlines()
        except (OSError, UnicodeError) as exc:
            attempts.append(dict(path=str(path), error=str(exc)))
            continue
        names = sorted({line.strip() for line in available
                        if re.fullmatch(r'\w+:\w+', line.strip()) and UB_EVENT.search(line)})
        formats = []
        for name in names:
            group, event = name.split(':')
            if not CQ_EVENT.search(event):
                continue
            try:
                spec = (root/'events'/group/event/'format').read_text(encoding='utf-8')
                formats.append(dict(event=name, format=spec, error=None))
            except (OSError, UnicodeError) as exc:
                formats.append(dict(event=name, format=None, error=str(exc)))
        return dict(state='readable', root=str(root), attempts=attempts, events=names,
                    completion_candidates=formats)
    return dict(state='unavailable', root=None, attempts=attempts, events=[], completion_candidates=[])


def kernel_module(name):
    mod = run('modinfo', name)
    fields = {}
    for line in (mod['output'] or '').splitlines():
        field, _, value = line.partition(':')
        if field in ('filename','version','srcversion','vermagic'):
            fields[field] = value.strip()
    fields['loaded'] = (Path('/sys/module')/name).is_dir()
    fields['modinfo_error'] = mod['error']
    try:
        fields['loaded_srcversion'] = (Path('/sys/module')/name/'srcversion').read_text().strip()
    except OSError as exc:
        fields['loaded_srcversion_error'] = str(exc)
    return fields


def collect(case, library_dir, pid, trace_roots):
    setup = read_json(case/'setup.json')['fingerprint']
    runtime = read_json(case/'normal/runtime.json')
    host = read_json(case/'host.json')
    status = read_json(case/'normal/status.json')
    if case.name != 'node1' or setup.get('backend') != 'urma' or runtime.get('normal_fadd_fence') != 'tail-off' or status['rc']:
        raise ValueError('reference must be a successful URMA tail-off case from node1')
    archived = sorted(p for p in runtime['loaded_libraries'] if LIBRARY.fullmatch(Path(p).name))
    live = mapped_libraries(pid)
    local_dirs = {Path(p).parent for p in archived}
    if library_dir:
        local_dirs.add(library_dir.resolve())
    local_dirs.update((Path('/usr/lib64'), Path('/usr/lib'), Path('/usr/local/lib64'),
                       Path('/usr/local/lib'), Path('/usr/lib/aarch64-linux-gnu')))
    roots = sorted(local_dirs | {p/'urma' for p in local_dirs})
    records, errors = library_records(roots, archived, live['libraries'])
    expected = setup['libraries']
    configured = library_dir.resolve() if library_dir else None
    by_alias = {alias: record['sha256'] for record in records for alias in record['aliases']}
    known = {}
    for path, baseline in sorted(expected.items()):
        if not LIBRARY.fullmatch(path):
            continue
        expected_path = str(configured/path) if configured else next(
            (alias for alias in archived if Path(alias).name == path), None)
        observed = by_alias.get(expected_path)
        known[path] = dict(archived_sha256=baseline, observed_sha256=observed,
                           state='unverified' if observed is None else 'match' if observed==baseline else 'mismatch')
    kernel = platform.release()
    boot = None
    try:
        boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    except OSError:
        pass
    return dict(version=1, host=platform.node(), kernel=kernel, boot_id=boot,
                reference=dict(case=str(case), kernel=setup['kernel'], device=setup['device'],
                               boot_id=host['boot_id'], module_sha=runtime['module_sha'],
                               kernel_matches=kernel==setup['kernel'], boot_matches=boot==host['boot_id'],
                               archived_library_map=archived, sdk_hashes=read_json(case/'build_manifest.json')['sdk']),
                known_library_checks=known, live_library_map=live, library_candidates=records,
                library_access_errors=errors, kernel_modules={name:kernel_module(name) for name in ('udma','ubcore','uburma')},
                tracefs=trace_catalog(trace_roots), physical_cqe_readiness_measured=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference-case', type=Path, default=REFERENCE)
    parser.add_argument('--library-dir', type=Path, help='private UMDK lib directory used by the benchmark')
    parser.add_argument('--pid', type=int, help='optional existing benchmark worker PID on 204')
    parser.add_argument('--tracefs', type=Path, action='append', help='optional readable tracefs root')
    parser.add_argument('--output', type=Path, required=True, help='path to a new JSON output file')
    args = parser.parse_args()
    if args.pid is not None and args.pid<=0:
        parser.error('--pid must be positive')
    if args.output.exists():
        parser.error('--output already exists; choose a new file')
    default_roots = [Path('/sys/kernel/tracing'), Path('/sys/kernel/debug/tracing')]
    result = collect(args.reference_case.resolve(), args.library_dir, args.pid, args.tracefs or default_roots)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x',encoding='utf-8') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    print('URMA_CQE_CAPABILITIES_COLLECTED tracefs={} completion_candidates={} output={}'.format(
        result['tracefs']['state'],len(result['tracefs']['completion_candidates']),args.output))


if __name__ == '__main__':
    main()
