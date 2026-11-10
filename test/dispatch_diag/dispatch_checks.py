"""Accept actual Dispatch coverage, not file existence or layout-only traces."""
import json
from pathlib import Path

LABELS={'dispatch_fp8_chunk32','dispatch_bf16_chunk32'}
SITES=json.loads(Path(__file__).with_name('dispatch_sites.json').read_text())
ALLOWED={int(k) for k in SITES['sites']}
REQUIRED={40,41,42,46,50,51,52,53,54,55,56,57,59}

def validate_gpu(case,samples,mode,write_report=True):
    case=Path(case)
    windows=[json.loads(s) for s in (case/'gpu_stages.jsonl').read_text().splitlines() if s.strip()]
    expected={(label,i) for label in LABELS for i in range(samples)}
    if len(windows)!=len(expected) or {(w['label'],w['index']) for w in windows}!=expected:raise ValueError('missing Dispatch windows or unexpected layout/combine windows')
    coverage=[]
    for w in windows:
        if w.get('version')!=2 or w.get('dispatch_trace_version')!=1 or w.get('storage')!='device_private_slots' or w.get('publication_correlation') is not False or w['mode']!=mode or w['clock_domain']!='per_thread_clock64_delta' or w['nominal_clock_khz']<=0:raise ValueError('wrong Dispatch trace schema/mode/clock')
        seen=set();keys=set()
        for r in w['records']:
            key=(r['block'],r['warp'],r['sample_lane'],r['stage'])
            if key in keys or not 0<=r['block']<128 or not 0<=r['warp']<32 or r['sample_lane'] not in (0,1) or r['stage'] not in ALLOWED or r['calls']<=0 or not 0<=r['max_cycles']<=r['sum_cycles']:raise ValueError('invalid/duplicate GPU record')
            keys.add(key);seen.add(r['stage'])
            if mode=='dispatch_access' and (r['sum_cycles'] or r['max_cycles']):raise ValueError('access probe unexpectedly timed')
        if mode=='bypass':
            if seen:raise ValueError('bypass unexpectedly recorded GPU stats')
        elif mode=='dispatch_clock':
            if seen!={46} or not all(r['sum_cycles']>0 for r in w['records']):raise ValueError('missing clock-only Dispatch coverage')
        elif mode in ('dispatch','dispatch_access'):
            if not REQUIRED.issubset(seen):raise ValueError('missing required role/copy/operator coverage: '+str(sorted(REQUIRED-seen)))
            if mode=='dispatch' and not all(r['sum_cycles']>0 for r in w['records'] if r['stage']>=53):raise ValueError('role/issue clocks not recorded')
        else:raise ValueError('unsupported focused mode')
        coverage.append(dict(label=w['label'],index=w['index'],stages=sorted(seen),conditional_absent=sorted(ALLOWED-seen)))
    if write_report:(case/'dispatch_coverage.json').write_text(json.dumps(dict(version=1,windows=coverage,sites=SITES),indent=2))
    return True
