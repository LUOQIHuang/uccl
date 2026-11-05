"""GPU measurement adapter; CPU counter snapshots are process-local."""
import json
import time
import phase_support


class Measurements:
    def __init__(self,torch,dist,ep,case,mode,enabled,warmups,samples,capture_layout=False):
        self.torch,self.dist,self.ep=torch,dist,ep
        self.case,self.mode,self.enabled=case,mode,enabled
        self.warmups,self.samples=warmups,samples
        self.capture_layout=capture_layout

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
        torch=self.torch
        torch.cuda.synchronize()
        cache=torch.empty(int(256e6//4),dtype=torch.int,device=f'cuda:{torch.cuda.current_device()}')
        for _ in range(self.warmups): fn()
        cache.zero_()
        start=[torch.cuda.Event(enable_timing=True) for _ in range(self.samples)]
        end=[torch.cuda.Event(enable_timing=True) for _ in range(self.samples)]
        def timed():
            for i in range(self.samples):
                start[i].record(); fn(); end[i].record()
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
