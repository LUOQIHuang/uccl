"""Compare an inactive paced control with active trace under identical provenance."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

REPO=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('reference_summary',REPO/'test/reference_events/summarize.py')
reference=importlib.util.module_from_spec(spec);spec.loader.exec_module(reference)
base=reference.base


def summarize(control,trace,output):
    rows=[];context=[]
    for rank in (0,1):
        p=base.case_data(control,rank,False,reference_events='paced')
        t=base.case_data(trace,rank,False,reference_events='on')
        if base.sig(p['identity'])!=base.sig(t['identity']) or p['runtime']['module_sha']!=t['runtime']['module_sha']:
            raise ValueError('paced/on source, diagnostics, runtime, boot or module changed; do not relabel historical results')
        context.append(dict(node=rank,**t['identity'],module_sha=t['runtime']['module_sha']))
        for label in sorted(base.ALL_LABELS):
            pv,tv=p['means'][label],t['means'][label]
            change=(tv['mean_us']/pv['mean_us']-1)*100
            rows.append(dict(node=rank,label=label,paced_us=pv['mean_us'],trace_us=tv['mean_us'],change_pct=change,
                paced_cv_pct=pv['cv_pct'],trace_cv_pct=tv['cv_pct'],
                representative=abs(change)<=5 and max(pv['cv_pct'],tv['cv_pct'])<=5))
    output.mkdir(parents=True,exist_ok=False)
    base.write(output/'matched_controls.csv',rows)
    (output/'context.json').write_text(json.dumps(dict(nodes=context,
        analyzer_sha=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        note='Paced and active trace use the same explicit synchronization/barrier and Event creation pattern, with 5 warmups and 10 samples. Active trace additionally resets/reads records and runs a host clock/CPU timeline. This is a two-case gate without a new post control, not proof of zero internal perturbation or an additive critical path.'),indent=2))
    usable=all(r['representative'] for r in rows if r['label'].startswith('dispatch_'))
    print('REFERENCE_MATCHED_SUMMARY_PASSED usable='+str(usable)+' output='+str(output))
    return rows


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('control','trace','output'):parser.add_argument('--'+name,type=Path,required=True)
    args=parser.parse_args();summarize(args.control,args.trace,args.output)
