"""GPU measurement adapter; CPU counter snapshots are process-local."""
import json
import time
import phase_support


class Measurements:
    def __init__(self,torch,dist,ep,case,mode,enabled,warmups,samples,capture_layout=False,dispatch_trial=False,dispatch_delay_us=0,timeline=False,gpu_rings=None,gpu_stage_mode="all"):
        self.torch,self.dist,self.ep=torch,dist,ep
        self.case,self.mode,self.enabled=case,mode,enabled
        self.warmups,self.samples=warmups,samples
        self.gpu_rings=gpu_rings;self.gpu_stage_mode=gpu_stage_mode
        self.timeline=timeline
        self.dispatch_focus=False
        self.capture_layout=capture_layout
        self.dispatch_trial=dispatch_trial;self.dispatch_delay_us=dispatch_delay_us

    def append(self,name,data):
        with (self.case/name).open('a') as stream: stream.write(json.dumps(data)+'\n')

    def window(self,label,fn,iterations):
        self.torch.cuda.synchronize()
        self.dist.barrier()
        before=json.loads(self.ep.urma_phase_snapshot())
        if self.capture_layout:self.ep.urma_layout_begin()
        self.ep.urma_phase_enable(self.enabled)
        begin=time.perf_counter_ns()
        try:
            return fn()
        finally:
            try:
                self.torch.cuda.synchronize()
            finally:
                self.ep.urma_phase_enable(False)
            elapsed=time.perf_counter_ns()-begin
            after=json.loads(self.ep.urma_phase_snapshot())
            if self.capture_layout:
                self.append('write_layout.jsonl',dict(label=label,mode=self.mode,iterations=iterations,
                    **json.loads(self.ep.urma_layout_end())))
            self.append('phases.jsonl',dict(label=label,mode=self.mode,enabled=self.enabled,
                iterations=iterations,host_window_ns=elapsed,host_window_begin_ns=begin,
                host_window_end_ns=begin+elapsed,
                records=phase_support.snapshot_delta(before,after),
                note='inclusive scopes overlap; snapshot boundaries approximate; CQ samples include polling delay'))
            self.dist.barrier()

    def api(self,fn,label,post_fn=None):
        if self.dispatch_trial:return self.isolated_api(fn,label,post_fn)
        torch=self.torch
        torch.cuda.synchronize()
        cache=torch.empty(int(256e6//4),dtype=torch.int,device=f'cuda:{torch.cuda.current_device()}')
        for _ in range(self.warmups): fn()
        cache.zero_()
        start=[torch.cuda.Event(enable_timing=True) for _ in range(self.samples)]
        end=[torch.cuda.Event(enable_timing=True) for _ in range(self.samples)]
        def timed():
            for i in range(self.samples):
                trace_this=(self.dispatch_focus and self.gpu_rings is not None and label.startswith('dispatch_')) or (not self.dispatch_focus and self.timeline and (self.gpu_rings is not None or (i==0 and label.startswith('dispatch_'))))
                if trace_this and self.gpu_rings is not None:self.dist.barrier()
                rings=self.gpu_rings() if trace_this and self.gpu_rings is not None else None
                if rings is not None:self.ep.gpu_stage_begin(rings,self.gpu_stage_mode)
                if trace_this and self.timeline:self.ep.urma_timeline_begin()
                try:
                    start[i].record(); fn(); end[i].record()
                finally:
                    if trace_this:
                        try:
                            torch.cuda.synchronize()
                            if rings is not None:self.dist.barrier()
                        finally:
                            if rings is not None:self.append('gpu_stages.jsonl',dict(label=label,index=i,**json.loads(self.ep.gpu_stage_end(rings))))
                            if self.timeline:self.append('urma_timeline.jsonl',dict(label=label,index=i,backend='urma',**json.loads(self.ep.urma_timeline_end())))
                if post_fn is not None: post_fn()
            torch.cuda.synchronize()
        self.window(label,timed,self.samples)
        raw=[s.elapsed_time(e)*1000 for s,e in zip(start,end)]
        for i,value in enumerate(raw):
            self.append('api_samples.jsonl',dict(label=label,mode=self.mode,index=i,us=value,used=i>0))
        summary=dict(label=label,mode=self.mode,enabled=self.enabled,warmups=self.warmups,
                     measured=self.samples,dropped=1,**phase_support.distribution(raw[1:]))
        self.append('sample_summary.jsonl',summary)
        print('MODE_PARITY_SAMPLES '+json.dumps(summary),flush=True)
        return summary['mean_us']/1e6,summary['min_us']/1e6,summary['max_us']/1e6

    def isolated_api(self,fn,label,post_fn=None):
        torch=self.torch;target=label.startswith('dispatch_');raw=[]
        def once(index=None):
            torch.cuda.synchronize();self.dist.barrier()
            start=torch.cuda.Event(enable_timing=True);end=torch.cuda.Event(enable_timing=True)
            trace=target and self.enabled and index==0
            detail=None;host_begin=time.perf_counter_ns();start.record()
            if target:self.ep.urma_dispatch_trial_begin(self.dispatch_delay_us,trace)
            try:
                fn();end.record();torch.cuda.synchronize()
                host_ns=time.perf_counter_ns()-host_begin
            finally:
                if target:detail=json.loads(self.ep.urma_dispatch_trial_end())
            if post_fn is not None:post_fn()
            if index is not None:
                row=dict(label=label,mode=self.mode,index=index,used=index>0,us=start.elapsed_time(end)*1000,host_us=host_ns/1000,
                    requested_delay_us=self.dispatch_delay_us if target else 0,trial=detail)
                self.append('dispatch_trial_samples.jsonl',row);raw.append(row)
                if trace:self.append('dispatch_trial_trace.jsonl',row)
        for _ in range(self.warmups):once()
        def timed():
            for i in range(self.samples):once(i)
        self.window(label,timed,self.samples)
        for row in raw:self.append('api_samples.jsonl',{k:row[k] for k in ('label','mode','index','used','us')})
        summary=dict(label=label,mode=self.mode,enabled=self.enabled,warmups=self.warmups,measured=self.samples,dropped=1,
            **phase_support.distribution([r['us'] for r in raw if r['used']]))
        self.append('sample_summary.jsonl',summary)
        print('MODE_PARITY_SAMPLES '+json.dumps(summary),flush=True)
        return summary['mean_us']/1e6,summary['min_us']/1e6,summary['max_us']/1e6
