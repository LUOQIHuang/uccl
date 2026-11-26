"""Verify the device and the actual verbs data path for the diag-kernel smoke."""

import argparse
from collections import Counter
import json
from pathlib import Path
import re

KERNEL = '6.6.0-gdr_f00955680_diag'


def inventory(hca, gid):
    device = Path('/sys/class/infiniband') / hca
    port = device / 'ports/1'
    fields = ('state', 'phys_state', 'link_layer', 'active_mtu', f'gids/{gid}',
              f'gid_attrs/types/{gid}', f'gid_attrs/ndevs/{gid}')
    info = {key: (port/key).read_text().strip() for key in fields}
    info.update(hca=hca, port=1, gid_index=gid, pci=str((device/'device').resolve()),
                driver=str((device/'device/driver').resolve()))
    print(json.dumps(info, indent=2), flush=True)
    if not re.search(r'^4:\s*ACTIVE$', info['state']) or info['link_layer'] != 'Ethernet':
        raise ValueError('selected verbs port is not active Ethernet')
    if not info[f'gids/{gid}'].replace(':', '').strip('0') or 'v2' not in info[f'gid_attrs/types/{gid}'].lower():
        raise ValueError('selected GID is not a usable RoCE v2 GID')


def check_events(path):
    records = (json.loads(line) for line in path.read_text().splitlines() if line.strip())
    samples = [r for r in records if r['label'] == 'dispatch_bf16_chunk32']
    if [r['index'] for r in samples] != [0, 1, 2]:
        raise ValueError('expected three BF16 smoke trace windows')
    output = []
    for sample in samples:
        events = sample['cpu']['events']
        completed = Counter((e['proxy'], e['wr_id']) for e in events
                            if e['stage'] == 'cqe' and e['value'] == 0)
        counts = {}
        for name, opcode in (('write', 0), ('native_fadd', 6)):
            posted = Counter((e['proxy'], e['wr_id']) for e in events if e['stage'] == 'post_begin'
                             and e['value'] == opcode and e['wr_id'] != (1 << 64) - 1)
            if not posted or posted - completed:
                raise ValueError(f'missing {name} requests or successful completions')
            counts[name] = sum(posted.values())
        output.append(dict(index=sample['index'], **counts))
    return output


def verify(node, hca, gid):
    def read(path):
        return json.loads((node/path).read_text())

    setup = read('setup.json')
    runtime = read('runtime.json')
    manifest = read('module_manifest.json')
    status = read('r01_baseline/normal/status.json')
    fingerprint = setup['fingerprint']
    if (setup['error'] or fingerprint.get('kernel') != KERNEL or fingerprint.get('hca') != hca or
            fingerprint.get('gid_index') != gid or fingerprint.get('normal_tokens') != 128):
        raise ValueError('unexpected kernel, port or workload in smoke record')
    if manifest['flags'].get('USE_URMA') != '0' or manifest['flags'].get('USE_DMABUF') != '1':
        raise ValueError('smoke requires a pure verbs DMA-BUF module')
    if any('/liburma.so' in path for path in runtime['loaded_libraries']):
        raise ValueError('unexpected URMA library')
    if (status['rc'] != 0 or not all(status['checks'].values()) or
            not {'dmabuf', 'correctness', 'reference_events'} <= status['checks'].keys()):
        raise ValueError('normal correctness, DMA-BUF or trace checks failed')
    log = (node/'r01_baseline/normal/bench.log').read_text(errors='replace')
    if f'Selected NIC {hca} (' not in log:
        raise ValueError('missing selected HCA in the benchmark log')
    samples = check_events(node/'r01_baseline/normal/event_trace.jsonl')
    print(json.dumps(dict(kernel=KERNEL, hca=hca, gid=gid, module_sha=runtime['module_sha'], samples=samples), indent=2))
    print('VERBS_ON_DIAG_SMOKE_PASSED')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('inventory', 'verify'))
    parser.add_argument('--hca', required=True)
    parser.add_argument('--gid-index', type=int, required=True)
    parser.add_argument('--node', type=Path)
    args = parser.parse_args()
    if args.action == 'inventory':
        inventory(args.hca, args.gid_index)
    else:
        verify(args.node, args.hca, args.gid_index)
