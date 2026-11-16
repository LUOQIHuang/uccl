"""Summarize existing accepted BF16 software completion-to-retirement waits."""
import argparse,csv,hashlib,json,statistics
from pathlib import Path
from audit_notify_completion import quantile


def audit(root,output):
    rows=[];sources={}
    for name,tag in [('verbs_ref_matched_summary_01','verbs'),('urma_boundaries_01_summary','urma')]:
        path=root/name/'cpu_requests.csv';sources[str(path)]=hashlib.sha256(path.read_bytes()).hexdigest()
        with path.open(newline='') as stream:data=list(csv.DictReader(stream))
        for node in ('0','1'):
            for op in ('atomic','payload_write'):
                selected=[r for r in data if r['label']=='dispatch_bf16_chunk32' and r['node']==node and r['operation']==op]
                if len(selected)!=(2268 if op=='atomic' else 1188):raise ValueError('incomplete accepted BF16 request set')
                values=[float(r['cqe_to_ack_slot_us']) for r in selected]
                if any(v<0 for v in values):raise ValueError('invalid completion/retirement order')
                rows.append(dict(backend=tag,node=int(node),operation=op,requests=len(values),median_us=statistics.median(values),p95_us=quantile(values,.95),max_us=max(values),over_50us_pct=100*sum(v>50 for v in values)/len(values)))
    output.mkdir(parents=True,exist_ok=False)
    with (output/'software_retire_wait.csv').open('w',newline='') as stream:
        w=csv.DictWriter(stream,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
    (output/'sources.json').write_text(json.dumps(dict(sources=sources,note='Previously accepted BF16 captures; observational clue, not a new backend A/B or additive API decomposition. cqe is software completion observation; ack_slot is pending-entry retirement. A long payload interval can reflect waiting behind an unfinished ordinary request, not expensive CQE processing. Quantiles pooled over nine windows are not independent repetitions. Original observation overhead remains.'),indent=2))
    return rows


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();audit(a.root,a.output)
