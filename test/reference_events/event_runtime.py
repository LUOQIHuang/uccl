"""Reference-style sparse Normal events; CPU and sampled host clock use RAW."""
import ctypes as C
import collections
import json
import math
import os
from pathlib import Path
import statistics

CAPACITY=8192
LABELS={'dispatch_fp8_chunk32','dispatch_bf16_chunk32'}
NAMES=['dispatch_sender_first_token','dispatch_first_remote_write','dispatch_last_remote_token_visible',
       'combine_first_forward_token','combine_first_remote_write','combine_receiver_done']
BOUNDARY_NAMES=NAMES[:3]+['dispatch_last_payload_ready','dispatch_last_payload_tail_published',
    'dispatch_final_tail_check_begin','dispatch_final_tail_observed','dispatch_last_epoch_check_begin',
    'dispatch_forwarder_done','dispatch_receiver_done','dispatch_chunk6_ready','dispatch_chunk6_tail_published']

def selected_profile():
    profile=os.environ.get('UCCL_REFERENCE_PROFILE','legacy')
    if profile not in ('legacy','boundaries'):raise ValueError('unknown reference profile')
    return profile


class Trace(C.Structure):
    _fields_=[('count',C.c_uint*6),('dropped',C.c_uint),('reserved',C.c_uint),
              ('ticks',(C.c_ulonglong*CAPACITY)*6),('device_before',(C.c_ulonglong*CAPACITY)*6),
              ('device_after',(C.c_ulonglong*CAPACITY)*6),('block_id',(C.c_uint*CAPACITY)*6),('epoch',(C.c_uint*CAPACITY)*6),('warp_id',(C.c_uint*CAPACITY)*6)]

class BoundaryTrace(C.Structure):
    _fields_=[('count',C.c_uint*12),('dropped',C.c_uint),('reserved',C.c_uint),
              ('ticks',(C.c_ulonglong*CAPACITY)*12),('device_before',(C.c_ulonglong*CAPACITY)*12),
              ('device_after',(C.c_ulonglong*CAPACITY)*12),('block_id',(C.c_uint*CAPACITY)*12),('epoch',(C.c_uint*CAPACITY)*12),('warp_id',(C.c_uint*CAPACITY)*12)]


class Clock(C.Structure):
    _fields_=[(k,C.c_ulonglong) for k in ('updates','max_gap_ns','gaps_over_1us','gaps_over_10us')]+[('scheduler_error',C.c_int),('reserved',C.c_int)]


class Capture:
    def __init__(self,path):
        self.lib=C.CDLL(str(path));self.epoch=0
        self.profile=selected_profile();self.trace_type=Trace;self.names=NAMES
        self.lib.uccl_reference_events_begin.argtypes=[C.c_uint]
        self.lib.uccl_reference_events_end.argtypes=[C.c_void_p,C.c_void_p]
        if self.lib.uccl_reference_events_version()!=1:raise RuntimeError('reference event build required')
        self.begin_fn=self.lib.uccl_reference_events_begin;self.end_fn=self.lib.uccl_reference_events_end
        if self.profile=='boundaries':
            if not hasattr(self.lib,'uccl_reference_boundaries_version') or self.lib.uccl_reference_boundaries_version()!=1:raise RuntimeError('BOUNDARY_PROFILE_REQUIRES_REBUILD')
            self.lib.uccl_reference_boundaries_begin.argtypes=[C.c_uint]
            self.lib.uccl_reference_boundaries_end.argtypes=[C.c_void_p,C.c_void_p]
            self.begin_fn=self.lib.uccl_reference_boundaries_begin;self.end_fn=self.lib.uccl_reference_boundaries_end
            self.trace_type=BoundaryTrace;self.names=BOUNDARY_NAMES
    def begin(self):
        self.epoch+=1
        rc=self.begin_fn(self.epoch)
        if rc:raise RuntimeError('REFERENCE_EVENTS_BEGIN rc='+str(rc))
    def end(self):
        t=self.trace_type();c=Clock();rc=self.end_fn(C.byref(t),C.byref(c))
        if rc:raise RuntimeError('REFERENCE_EVENTS_END rc='+str(rc))
        records=[]
        for event in range(len(self.names)):
            for i in range(min(t.count[event],CAPACITY)):
                records.append(dict(event=event,slot=i,name=self.names[event],host_ns=t.ticks[event][i],
                    device_before=t.device_before[event][i],device_after=t.device_after[event][i],
                    block=t.block_id[event][i],warp=t.warp_id[event][i],epoch=t.epoch[event][i]))
        result=dict(version=1,reference_commit='940f7c7b',clock='CLOCK_MONOTONIC_RAW',epoch=self.epoch,dropped=t.dropped,
            counts=list(t.count),clock_stats={k:getattr(c,k) for k,_ in c._fields_ if k!='reserved'},records=records)
        if self.profile=='boundaries':result['profile']='dispatch_boundaries_v1'
        return result


def measure(torch,dist,ep,case,fn,label,warmups,samples,post_fn=None,capture_factory=None,record_events=True):
    """Capture only fixed Normal points, keeping layouts on the existing timer."""
    capture=(capture_factory or Capture)(ep.__file__) if record_events else None
    torch.cuda.synchronize()
    cache=torch.empty(int(256e6//4),dtype=torch.int,device='cuda:'+str(torch.cuda.current_device()))
    for _ in range(warmups):
        fn()
        if post_fn is not None:post_fn()
    cache.zero_();torch.cuda.synchronize();raw=[]
    for i in range(samples):
        torch.cuda.synchronize();dist.barrier()
        if capture is not None:capture.begin()
        cpu_active=False;gpu=None
        try:
            dist.barrier();torch.cuda.synchronize() # both clocks/reset copies ready before timing
            if capture is not None:
                if selected_profile()=='boundaries':ep.urma_timeline_begin_boundaries()
                else:ep.urma_timeline_begin()
                cpu_active=True
            begin=torch.cuda.Event(enable_timing=True);end=torch.cuda.Event(enable_timing=True)
            begin.record();fn();end.record();torch.cuda.synchronize()
            dist.barrier() # retain late local completions until both peers finish
            if cpu_active:cpu=json.loads(ep.urma_timeline_end());cpu_active=False
        finally:
            try:
                if cpu_active:ep.urma_timeline_end()
            finally:
                if capture is not None:gpu=capture.end()
        value=begin.elapsed_time(end)*1000
        if post_fn is not None:post_fn()
        raw.append(value)
        if capture is not None:append(case/'event_trace.jsonl',dict(label=label,index=i,us=value,gpu=gpu,cpu=cpu))
        append(case/'api_samples.jsonl',dict(label=label,index=i,us=value,used=i>0,mode='normal'))
        dist.barrier()
    values=raw[1:];mean=statistics.mean(values)
    summary=dict(label=label,mode='normal',enabled=False,warmups=warmups,measured=samples,dropped=1,n=len(values),
                 mean_us=mean,min_us=min(values),max_us=max(values),median_us=statistics.median(values),std_us=statistics.pstdev(values),cv=statistics.pstdev(values)/mean)
    append(case/'sample_summary.jsonl',summary)
    print('MODE_PARITY_SAMPLES '+json.dumps(summary),flush=True)
    # URMA phase-suite acceptance requires a phase file; no CPU counters active.
    append(case/'phases.jsonl',dict(label=label,enabled=False,records=[],note='reference events' if record_events else 'paced control without events/clock'))
    if not record_events:control_marker(case,label,'paced',warmups,samples)
    return mean/1e6,min(values)/1e6,max(values)/1e6


def append(path,data):
    with path.open('a') as stream:stream.write(json.dumps(data)+'\n')


def control_marker(case,label,mode,warmups,samples):
    marker=dict(label=label,mode=mode,warmups=warmups,samples=samples,gpu_events=False,cpu_events=False,host_clock=False)
    if selected_profile()=='boundaries':marker['profile']='dispatch_boundaries_v1'
    append(case/'reference_control.jsonl',marker)


def validate_control(case,mode,warmups,samples,profile='legacy'):
    case=Path(case)
    if profile not in ('legacy','boundaries'):raise ValueError('unknown reference profile')
    if mode not in ('batch','paced'):raise ValueError('unknown reference control')
    expected=[dict(label=l,mode=mode,warmups=warmups,samples=samples,gpu_events=False,cpu_events=False,host_clock=False) for l in sorted(LABELS)]
    if profile=='boundaries':
        for row in expected:row['profile']='dispatch_boundaries_v1'
    actual=[json.loads(s) for s in (case/'reference_control.jsonl').read_text().splitlines()]
    if sorted(actual,key=lambda r:r['label'])!=expected:raise ValueError('missing/wrong reference control execution markers')
    if any((case/name).exists() for name in ('event_trace.jsonl','reference_correctness.json','gpu_stages.jsonl','urma_timeline.jsonl','verbs_timeline.jsonl')):raise ValueError('control unexpectedly enabled tracing')
    return True


def correctness_passed(case,profile='legacy',tokens=4096):
    try:
        data=json.loads((Path(case)/'reference_correctness.json').read_text())
        events=range(12 if tokens==4096 else 10) if profile=='boundaries' else range(3)
        return profile in ('legacy','boundaries') and data['verified'] is True and data['version']==1 and data.get('profile')==('dispatch_boundaries_v1' if profile=='boundaries' else None) and all(data['counts'][e]>0 for e in events)
    except (OSError,ValueError,KeyError,TypeError,IndexError):return False


def validate(case,samples,profile='legacy',tokens=4096):
    case=Path(case)
    if profile not in ('legacy','boundaries'):raise ValueError('unknown reference profile')
    data=[json.loads(s) for s in (case/'event_trace.jsonl').read_text().splitlines()]
    if len(data)!=len(LABELS)*samples or {(r['label'],r['index']) for r in data}!={(l,i) for l in LABELS for i in range(samples)}:raise ValueError('missing reference event windows')
    sample_file=case/'api_samples.jsonl'
    api={(r['label'],r['index']):r['us'] for r in map(json.loads,sample_file.read_text().splitlines())} if sample_file.exists() else None
    for row in data:
        if api is not None and api.get((row['label'],row['index']))!=row['us']:raise ValueError('reference event/API sample mismatch')
        gpu=row['gpu'];cpu=row['cpu'];required=set(range(12 if tokens==4096 else 10)) if profile=='boundaries' else ({0,1,2} if row['label'].startswith('dispatch') else {3,4,5})
        names=BOUNDARY_NAMES if profile=='boundaries' else NAMES
        if gpu.get('profile')!=('dispatch_boundaries_v1' if profile=='boundaries' else None) or len(gpu['counts'])!=len(names):raise ValueError('reference profile/ABI mismatch')
        if profile=='boundaries' and cpu.get('heartbeat_stride')!=128:raise ValueError('boundary CPU heartbeat configuration missing')
        if gpu['version']!=1 or gpu['clock']!='CLOCK_MONOTONIC_RAW' or cpu.get('clock')!='CLOCK_MONOTONIC_RAW' or gpu['dropped'] or cpu['dropped']:raise ValueError('wrong/truncated reference events')
        if not math.isfinite(row['us']) or row['us']<=0 or not gpu['clock_stats']['updates']:raise ValueError('invalid reference measurement/clock')
        seen=set();slots=set();counts=collections.Counter()
        for r in gpu['records']:
            key=(r['event'],r['slot'])
            if key in slots or not 0<=r['slot']<CAPACITY or not 0<=r['block']<128 or not 0<=r['warp']<32 or r['event'] not in required or r['name']!=names[r['event']] or r['epoch']!=gpu['epoch'] or r['host_ns']<=0 or r['device_after']<r['device_before']:raise ValueError('invalid GPU reference record')
            slots.add(key);counts[r['event']]+=1
            seen.add(r['event'])
        if seen!=required or any(gpu['counts'][e]>CAPACITY or gpu['counts'][e]!=counts[e] for e in range(len(names))):raise ValueError('incomplete GPU reference coverage')
        if profile=='boundaries':
            by=collections.Counter((r['block']//2,r['event']) for r in gpu['records'])
            for r in gpu['records']:
                event=r['event']
                odd=event in (0,1,3,4,9,10,11)
                warp=0 if event==0 or not odd else (8 if event==9 else 7)
                if r['block']>=24 or r['block']%2!=int(odd) or r['warp']!=warp:raise ValueError('boundary role/channel mismatch')
            for channel in range(12):
                for event in required:
                    count=by[channel,event]
                    valid=1<=count<=64 if event==5 else count==1
                    if not valid:raise ValueError('incomplete/duplicate boundary channel coverage')
        stages={r['stage'] for r in cpu['events']}
        if not {'collect','prepare','post_begin','post_end','cqe','ack_slot'}.issubset(stages):raise ValueError('missing CPU request stages')
        groups=collections.defaultdict(set)
        for r in cpu['events']:
            if r['proxy']<0 or r['ns']<cpu['origin_ns']:raise ValueError('invalid CPU identity or clock')
            if r['stage'] in ('post_end','cqe') and r['value']!=0:raise ValueError('failed CPU post/completion in reference window')
            groups[r['proxy'],r['wr_id']].add(r['stage'])
        if not any({'poll','decoded','prepare','post_begin','post_end','cqe','ack_slot'}.issubset(s) for s in groups.values()):raise ValueError('no complete CPU FIFO chain')
        if profile=='boundaries':
            posted={r['proxy'] for r in cpu['events'] if r['stage']=='post_begin'}
            heartbeat={r['proxy'] for r in cpu['events'] if r['stage']=='poll_heartbeat'}
            if not posted.issubset(heartbeat):raise ValueError('missing boundary Proxy heartbeat coverage')
    return True
