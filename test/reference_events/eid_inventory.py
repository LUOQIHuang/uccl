"""Query EIDs with the benchmark's private SDK; never create a context or post work."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

SOURCE = r'''
#define _GNU_SOURCE
#include <dlfcn.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <urma_api.h>
int main(int argc, char **argv) {
    if (argc != 2) return 2;
    Dl_info library = {0};
    /* Resolve the implementation, since &urma_init may denote our PLT stub. */
    void *implementation = dlsym(RTLD_DEFAULT, "urma_init");
    if (!implementation || !dladdr(implementation, &library)) return 3;
    printf("URMA_QUERY_LIBRARY path=%s\n", library.dli_fname);
    int status = urma_init(NULL);
    if (status != 0) { fprintf(stderr, "urma_init status=%d\n", status); return 4; }
    int count = 0, selected = 0, valid = 0;
    urma_device_t **devices = urma_get_device_list(&count);
    if (devices) {
        for (int i = 0; i < count; ++i) {
            if (!devices[i]) continue;
            printf("URMA_QUERY_DEVICE name=%s\n", devices[i]->name);
            if (strcmp(devices[i]->name, argv[1])) continue;
            selected = 1;
            uint32_t n = 0;
            urma_eid_info_t *eids = urma_get_eid_list(devices[i], &n);
            if (eids) {
                for (uint32_t k = 0; k < n; ++k) {
                    printf("URMA_QUERY_EID device=%s position=%u index=%u eid=", argv[1], k, eids[k].eid_index);
                    for (unsigned b = 0; b < sizeof(eids[k].eid.raw); ++b) printf("%02x", eids[k].eid.raw[b]);
                    printf("\n");
                    valid = 1;
                }
                urma_free_eid_list(eids);
            }
        }
        urma_free_device_list(devices);
    }
    FILE *maps = fopen("/proc/self/maps", "r");
    if (maps) {
        char line[4096];
        while (fgets(line, sizeof(line), maps)) {
            char *path = strchr(line, '/');
            if (path && strstr(path, ".so")) printf("URMA_QUERY_MAP path=%s", path);
        }
        fclose(maps);
    }
    status = urma_uninit();
    return !selected ? 5 : !valid ? 6 : status != 0 ? 7 : 0;
}
'''


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_query(text):
    libraries = re.findall(r'^URMA_QUERY_LIBRARY path=(.+)$', text, re.M)
    eids = [dict(device=d, position=int(p), index=int(i), eid=e)
            for d, p, i, e in re.findall(r'^URMA_QUERY_EID device=(\S+) position=(\d+) index=(\d+) eid=([0-9a-f]{32})$', text, re.M)]
    return dict(library=libraries[0] if len(libraries)==1 else None,
                devices=re.findall(r'^URMA_QUERY_DEVICE name=(\S+)$', text, re.M), eids=eids,
                loaded_libraries=sorted(set(re.findall(r'^URMA_QUERY_MAP path=(.+)$', text, re.M))))


def run(args):
    args.output.mkdir(parents=True, exist_ok=False)
    result = dict(device=args.device, include_dir=str(args.include_dir), lib_dir=str(args.lib_dir),
                  note='Query only: urma_init/device list/EID list/uninit. No context, segment, jetty, WR or GPU allocation.')
    env = os.environ.copy()
    env.pop('LD_PRELOAD', None)
    env['LD_LIBRARY_PATH'] = str(args.lib_dir) + ':' + env.get('LD_LIBRARY_PATH', '')
    try:
        expected = json.loads(args.build_manifest.read_text())['sdk']
        actual = {name:sha(args.include_dir/name) for name in expected}
        if actual != expected:
            raise RuntimeError('SDK_HEADERS_DIFFER_FROM_BENCHMARK_BUILD')
        result['sdk'] = actual
        library = next((args.lib_dir/n for n in ('liburma.so', 'liburma.so.0') if (args.lib_dir/n).is_file()), None)
        if library is None:
            raise RuntimeError('PRIVATE_URMA_LIBRARY_MISSING')
        source, binary = args.output/'query.c', args.output/'query'
        source.write_text(SOURCE)
        result['source_sha'] = sha(source)
        command = [args.cc, '-std=c11', '-I'+str(args.include_dir), str(source), str(library),
                   '-Wl,-rpath,'+str(args.lib_dir), '-ldl', '-o', str(binary)]
        result['compile_command'] = command
        build = subprocess.run(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60)
        (args.output/'compile.log').write_bytes(build.stdout)
        if build.returncode:
            raise RuntimeError('EID_QUERY_COMPILE_FAILED')
        query = subprocess.run([str(binary), args.device], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        (args.output/'query.log').write_bytes(query.stdout+query.stderr)
        result.update(parse_query(query.stdout.decode('utf-8', 'replace')), rc=query.returncode)
        loaded = Path(result['library']) if result['library'] else None
        result['library_matches_request'] = bool(loaded and loaded.resolve()==library.resolve())
        result['library_sha'] = sha(library.resolve())
        result['loaded_library_hashes'] = {p:sha(Path(p)) for p in result['loaded_libraries'] if Path(p).is_file()}
        result['selected_first_eid'] = next((e for e in result['eids'] if e['position']==0), None)
        if query.returncode or not result['library_matches_request'] or not result['selected_first_eid']:
            raise RuntimeError('EID_QUERY_INCOMPLETE_OR_WRONG_LIBRARY')
    except (OSError, RuntimeError, ValueError, KeyError, subprocess.TimeoutExpired) as exc:
        result['error'] = str(exc)
    (args.output/'eid_inventory.json').write_text(json.dumps(result, indent=2))
    print('EID_QUERY_' + ('FAILED' if result.get('error') else 'PASSED') + ' output=' + str(args.output), flush=True)
    return 1 if result.get('error') else 0


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--include-dir', type=Path, required=True)
    p.add_argument('--lib-dir', type=Path, required=True)
    p.add_argument('--build-manifest', type=Path, required=True)
    p.add_argument('--device', default='udmac0d1e2')
    p.add_argument('--cc', default='cc')
    a=p.parse_args()
    for name in ('output','include_dir','lib_dir','build_manifest'):
        setattr(a,name,getattr(a,name).resolve())
    raise SystemExit(run(a))
