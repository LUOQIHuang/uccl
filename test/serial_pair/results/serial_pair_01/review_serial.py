"""Offline audit of both uploaded serial backends; no GPU or source overlay changes."""
import argparse,csv,json,statistics as st,sys,tempfile
from pathlib import Path
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parents[1]))
import analyze,compare

def audit(out):
    if out.exists():raise ValueError('review output must not exist')
    contexts={};positions=[];environments=[]
    with tempfile.TemporaryDirectory(prefix='serial-review-') as t:
        t=Path(t)
        for backend in ('verbs','urma'):
            folder=t/('summary_'+backend);analyze.summarize(HERE,backend,folder)
            for name in ('pairs','iterations','per_run','summary'):
                with (folder/(name+'.csv')).open() as f:actual=list(csv.DictReader(f))
                with (HERE/('summary_'+backend)/(name+'.csv')).open() as f:uploaded=list(csv.DictReader(f))
                if actual!=uploaded:raise ValueError('uploaded/recomputed mismatch: '+backend+'/'+name)
            context=json.loads((folder/'context.json').read_text());contexts[backend]=context
            for node in context['nodes']:
                rank=node['node'];p=HERE/backend/'cases'/('serial_pair_01_'+backend+'_run')/('node%d'%rank)
                host=json.loads((p/'host.json').read_text());rt=node['runtime'];a=node['affinity']
                libs=rt.get('selected_runtime_hashes',rt.get('selected_library_hashes',{}))
                banner='; '.join(s.strip() for s in (p/'gpu_before.txt').read_text().splitlines() if 'Kernel Mode Driver Version' in s or 'MACA Version' in s)
                environments.append(dict(backend=backend,node=rank,cpu=a['cpu'],effective=str(a['effective']),gpu_numa=a['gpu_numa_node'],
                    kernel=host['kernel'],torch=rt['torch'],mcruntime_sha=libs.get('libmcruntime.so',''),gpu_banner=banner))
            with (folder/'pairs.csv').open() as f:raw=list(csv.DictReader(f))
            for label,(tokens,_) in analyze.LABELS.items():
                for index in range(tokens//32):
                    pp=[r for r in raw if r['label']==label and int(r['pair_index'])==index]
                    v=[float(r['pair_us']) for r in pp]
                    if len(v)!=300:raise ValueError('expected 3 rounds x100 pairs at each position')
                    positions.append(dict(backend=backend,label=label,pair_index=index,n=len(v),mean_pair_us=st.mean(v),median_pair_us=st.median(v),
                        p95_pair_us=analyze.percentile(v,.95),p99_pair_us=analyze.percentile(v,.99),max_pair_us=max(v),
                        median_write_us=st.median(float(r['write_us']) for r in pp),median_gap_us=st.median(float(r['gap_us']) for r in pp)))
        # Reuse the structural comparison after independently checking raw files.
        compare.compare(HERE,t/'comparison')
        with (t/'comparison/comparison.csv').open() as f:comparison=list(csv.DictReader(f))
    def placement(backend,node):
        a=next(x['affinity'] for x in contexts[backend]['nodes'] if x['node']==node)
        return a['cpu'],a['effective'],a['gpu_numa_node']
    for row in comparison:
        sender=analyze.LABELS[row['label']][1]
        row.update(verbs_sender_cpu=placement('verbs',sender)[0],urma_sender_cpu=placement('urma',sender)[0],
            sender_placement_match=placement('verbs',sender)==placement('urma',sender),
            both_nodes_placement_match=all(placement('verbs',n)==placement('urma',n) for n in (0,1)))
    out.mkdir(parents=True)
    analyze.write(out/'per_position.csv',positions);analyze.write(out/'environment.csv',environments);analyze.write(out/'comparison_audited.csv',comparison)
    (out/'validation.json').write_text(json.dumps(dict(raw_both_backends_passed=True,uploaded_csvs_match_recomputed=True,
        source_plan_checks_passed=True,placement_match=[placement('verbs',n)==placement('urma',n) for n in (0,1)]),indent=2))
    print('SERIAL_COMPARISON_AUDIT_PASSED output='+str(out))
if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,default=HERE/'comparison_review')
    audit(p.parse_args().output)
