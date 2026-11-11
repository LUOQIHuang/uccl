"""Compare two inactive controls with identical module, provenance and sample plan."""
import argparse
import importlib.util
import json
from pathlib import Path

spec=importlib.util.spec_from_file_location('reference_summary',Path(__file__).with_name('summarize.py'))
reference=importlib.util.module_from_spec(spec);spec.loader.exec_module(reference)
base=reference.base


def summarize(batch,paced,output):
    rows=[];context=[]
    for rank in (0,1):
        b=base.case_data(batch,rank,False,reference_events='batch')
        p=base.case_data(paced,rank,False,reference_events='paced')
        if base.sig(b['identity'])!=base.sig(p['identity']) or b['runtime']['module_sha']!=p['runtime']['module_sha']:
            raise ValueError('control source/runtime/boot/module changed')
        context.append(dict(node=rank,**b['identity'],module_sha=b['runtime']['module_sha']))
        for label in sorted(base.ALL_LABELS):
            bv,pv=b['means'][label],p['means'][label]
            change=(pv['mean_us']/bv['mean_us']-1)*100
            rows.append(dict(node=rank,label=label,batch_us=bv['mean_us'],paced_us=pv['mean_us'],
                change_pct=change,batch_cv_pct=bv['cv_pct'],paced_cv_pct=pv['cv_pct'],
                stable=max(bv['cv_pct'],pv['cv_pct'])<=5,within_5pct=abs(change)<=5))
    output.mkdir(parents=True,exist_ok=False)
    base.write(output/'cadence_controls.csv',rows)
    (output/'context.json').write_text(json.dumps(dict(nodes=context,
        note='Both controls use the event build with GPU/CPU capture and host clock inactive, warmups=5 samples=10. Compare batch vs per-sample synchronization/barriers and CUDA Event creation. No original URMA-versus-verbs attribution follows from this pair.'),indent=2))
    print('REFERENCE_CADENCE_SUMMARY_PASSED output='+str(output))
    return rows


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('batch','paced','output'):parser.add_argument('--'+name,type=Path,required=True)
    args=parser.parse_args();summarize(args.batch,args.paced,args.output)
