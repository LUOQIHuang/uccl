"""CPU-only helpers for stage measurements and reproducible LL workloads."""
import ast
import statistics


def distribution(values):
    if not values: raise ValueError('no timing samples')
    values=sorted(float(v) for v in values)
    def percentile(q):
        index=(len(values)-1)*q
        lo=int(index); hi=min(lo+1,len(values)-1)
        return values[lo]+(values[hi]-values[lo])*(index-lo)
    mean=statistics.mean(values)
    return dict(n=len(values),mean_us=mean,median_us=statistics.median(values),
                min_us=values[0],max_us=values[-1],p90_us=percentile(.90),p95_us=percentile(.95),
                std_us=statistics.pstdev(values),cv=statistics.pstdev(values)/mean if mean else 0)


def snapshot_delta(before,after):
    previous={r['id']:r for r in before['records']}
    result=[]
    for record in after['records']:
        metrics={}
        for name,m in record['metrics'].items():
            old=previous.get(record['id'],{}).get('metrics',{}).get(name,{})
            d={k:m[k]-old.get(k,0) for k in ('calls','ns','items')}
            d['histogram']=[v-(old.get('histogram',[0]*len(m['histogram']))[i])
                            for i,v in enumerate(m['histogram'])]
            if any(v<0 for v in d['histogram']) or any(d[k]<0 for k in ('calls','ns','items')):
                raise ValueError('diagnostic counters decreased')
            if d['calls'] or d['ns'] or d['items']: metrics[name]=d
        if metrics: result.append(dict(id=record['id'],rank=record['rank'],proxy=record['proxy'],metrics=metrics))
    return result


def immediate_hook_source(source):
    tree=ast.parse(source)
    functions=[n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name=='large_gemm_with_hook']
    if len(functions)!=1: raise ValueError('expected one LL hook workload function')
    node=functions[0]
    if [a.arg for a in node.args.args]!=['hook']: raise ValueError('unexpected hook function arguments')
    # Change only the benchmark-local hook workload; correctness and kernels
    # remain the real benchmark. Save the transformed source with the results.
    lines=source.splitlines(keepends=True)
    indent=' '*(node.col_offset+4)
    lines[node.body[0].lineno-1:node.end_lineno]=[indent+'hook()\n']
    transformed=''.join(lines)
    ast.parse(transformed)
    return transformed
