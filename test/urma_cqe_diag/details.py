#!/usr/bin/env python3
"""Collect provider code and resolve the provider bound to the chosen device."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

import collect

HERE = Path(__file__).resolve().parent


def command_log(argv, output, env=None, timeout=30):
    """Keep full output in a file, including partial output on timeout."""
    with output.open('w', encoding='utf-8') as stream:
        stream.write(json.dumps(argv)+'\n')
        stream.flush()
        try:
            result = subprocess.run(argv, stdout=stream, stderr=subprocess.STDOUT,
                                    env=env, timeout=timeout, check=False)
            return dict(rc=result.returncode, log=output.name)
        except (OSError, subprocess.TimeoutExpired) as exc:
            stream.write('\n'+str(exc)+'\n')
            return dict(rc=None, error=str(exc), log=output.name)


def selected_provider(text):
    matches = re.findall(r'^provider_path=(.+)$', text, re.M)
    if len(matches) != 1:
        raise ValueError('expected one device-bound provider')
    return Path(matches[0]).resolve(strict=True)


def inspect_binary(path, folder):
    folder.mkdir()
    before = collect.digest(path)
    commands = [('notes', ['readelf', '-n', str(path)]),
                ('sections', ['readelf', '-SW', str(path)]),
                ('symbols', ['readelf', '-Ws', str(path)]),
                ('dynamic', ['readelf', '-d', str(path)]),
                ('disassembly', ['objdump', '-d', '-C', '--no-show-raw-insn', str(path)])]
    results = {name:command_log(argv, folder/(name+'.log')) for name, argv in commands}
    if before != collect.digest(path):
        raise ValueError('provider changed during collection')
    return dict(path=str(path), sha256=before, commands=results)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library-dir', type=Path, required=True)
    parser.add_argument('--include-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True, help='new output directory')
    parser.add_argument('--reference-case', type=Path, default=collect.REFERENCE)
    parser.add_argument('--tracefs', type=Path, action='append')
    parser.add_argument('--device', default='udmac0d1e2')
    parser.add_argument('--inspect-only', action='store_true', help='skip SDK initialization and device enumeration')
    args = parser.parse_args()
    if args.output.exists():
        parser.error('output already exists; use a new directory')
    private = args.library_dir.resolve(strict=True)
    include = args.include_dir.resolve(strict=True)
    api = private/'liburma.so.0'
    provider = private/'urma/liburma-udma.so'
    for path in (api, provider, include/'urma_api.h', include/'urma_provider.h', include/'urma_types.h'):
        if not path.is_file():
            parser.error('required input missing: '+str(path))
    args.output.mkdir(parents=True)
    output = args.output.resolve()
    result = dict(version=1, device=args.device, device_context_created=False,
                  physical_cqe_readiness_measured=False, errors=[])
    try:
        capabilities = collect.collect(args.reference_case.resolve(), private, None,
            args.tracefs or [Path('/sys/kernel/tracing'), Path('/sys/kernel/debug/tracing')])
        (output/'capabilities.json').write_text(json.dumps(capabilities,indent=2)+'\n', encoding='utf-8')
        result['private_provider'] = inspect_binary(provider.resolve(), output/'private_provider')
        headers = output/'sdk'
        headers.mkdir()
        for path in include.glob('*.h'):
            shutil.copy2(str(path), str(headers/path.name))
        result['sdk_sha256'] = {p.name:collect.digest(p) for p in sorted(headers.glob('*.h'))}
        expected = capabilities['reference']['sdk_hashes']
        if any(result['sdk_sha256'].get(k)!=v for k,v in expected.items()):
            raise ValueError('SDK headers differ from the archived benchmark build')
        if any(r['state']!='match' for r in capabilities['known_library_checks'].values()):
            raise ValueError('private URMA libraries differ from archived benchmark inputs')
        result['device_selection'] = dict(state='skipped_inspect_only')
        if not args.inspect_only:
            cc = shutil.which('cc') or shutil.which('gcc')
            if not cc:
                raise ValueError('C compiler missing; static files retained')
            env = os.environ.copy()
            env['LD_LIBRARY_PATH'] = str(private)+(':'+env['LD_LIBRARY_PATH'] if env.get('LD_LIBRARY_PATH') else '')
            env.pop('LD_PRELOAD',None)
            env.pop('UMDK_HOME',None)
            with tempfile.TemporaryDirectory(prefix='urma_provider_select_') as temporary:
                binary = Path(temporary)/'provider_select'
                compile_result = command_log([cc,'-std=c11','-Wall','-Wextra','-Werror',
                    '-I'+str(include),str(HERE/'provider_select.c'),'-ldl','-o',str(binary)],output/'compile.log')
                result['compile'] = compile_result
                if compile_result['rc']!=0:
                    raise ValueError('provider selection probe compile failed; see compile.log')
                probe = command_log([str(binary), str(api), args.device], output/'provider_select.log',env)
            result['device_selection'] = probe
            if probe['rc']!=0:
                raise ValueError('device provider selection failed; see provider_select.log')
            actual = selected_provider((output/'provider_select.log').read_text(encoding='utf-8'))
            result['device_selection'].update(state='resolved', path=str(actual), sha256=collect.digest(actual),
                matches_private=collect.digest(actual)==result['private_provider']['sha256'])
            if actual!=provider.resolve():
                result['selected_provider'] = inspect_binary(actual, output/'selected_provider')
        bad = [name for name, log in result['private_provider']['commands'].items() if log['rc']!=0]
        if bad:
            result['errors'].append('private provider inspection commands failed: '+', '.join(bad))
    except Exception as exc:
        result['errors'].append(str(exc))
    (output/'manifest.json').write_text(json.dumps(result,indent=2)+'\n', encoding='utf-8')
    ok = not result['errors']
    print('URMA_PROVIDER_DETAILS_COLLECTED ok={} output={}'.format(ok,output))
    if not ok:
        print('\n'.join(result['errors']), file=sys.stderr)
        return 1
    return 0


if __name__=='__main__':
    sys.exit(main())
