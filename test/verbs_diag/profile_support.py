"""Focused configuration and optional CPU-only perf sampling helpers."""
import json
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time

def plan(suite):
    if suite not in ('scan','focused','boundaries'):
        raise ValueError(f'unknown suite: {suite}')
    dispatch = range(4,33,4) if suite=='scan' else (32,)
    combine = range(12,33,4) if suite=='scan' else (12,16) if suite=='boundaries' else (16,)
    return [(op,chunk) for op in ('dispatch_fp8','dispatch_bf16') for chunk in dispatch] + [('combine',chunk) for chunk in combine]

def prepare_benchmark(source, suite):
    changes = [('for nvl_chunk_size in range(4, 45, 4):','for nvl_chunk_size in (4,):'), ('for nvl_chunk_size in range(1, 8, 1):','for nvl_chunk_size in (4,):')]
    if suite in ('focused','boundaries'):
        combine='(12, 16)' if suite=='boundaries' else '(16,)'
        changes += [('for rdma_chunk_size in range(4, 33, 4):','for rdma_chunk_size in (32,):'), ('for rdma_chunk_size in range(12 if num_nodes == 2 else 8, 33, 4):',f'for rdma_chunk_size in {combine}:')]
    for old,new in changes:
        if source.count(old)!=1:
            raise ValueError(f'unexpected tuning loop: {old}')
        source=source.replace(old,new)
    return source

def callgraph_args(mode):
    return [] if mode=='none' else ['--call-graph', 'dwarf,8192' if mode=='dwarf' else 'fp']

def capability_check(mode):
    if not shutil.which('perf'):
        raise RuntimeError('PERF_MISSING: install/use perf for this kernel before requesting --profile-cpu')
    version=subprocess.run(['perf','--version'],capture_output=True,text=True,timeout=10)
    with tempfile.TemporaryDirectory(prefix='uccl_perf_probe_') as temporary:
        output=Path(temporary)/'probe.data'
        command=['perf','record','-q','-e','cpu-clock','-F','99','-o',str(output)] + callgraph_args(mode) + ['--',sys.executable,'-c','import time\nend=time.monotonic()+0.3\nwhile time.monotonic()<end: pass']
        probe=subprocess.run(command,capture_output=True,text=True,timeout=20)
        if probe.returncode or not output.is_file() or output.stat().st_size==0:
            raise RuntimeError(f'PERF_UNAVAILABLE: {probe.stdout} {probe.stderr}')
        report=subprocess.run(report_command(output),capture_output=True,text=True,timeout=20)
        if not valid_report(report):
            raise RuntimeError(f'PERF_REPORT_UNAVAILABLE: {report.stdout} {report.stderr}')
        if mode!='none':
            graph=subprocess.run(callgraph_command(output),capture_output=True,text=True,timeout=20)
            if graph.returncode:
                raise RuntimeError(f'PERF_CALLGRAPH_UNAVAILABLE: {graph.stderr}; try --perf-callgraph none on both nodes')
    return dict(version=version.stdout.strip(),event='cpu-clock',frequency=99,callgraph=mode)

def report_command(path):
    return ['perf','report','--stdio','--no-children','--sort','dso,symbol','--percent-limit','0.5','--call-graph','none','-i',str(path)]

def valid_report(report):
    return report.returncode==0 and bool(re.search(r'^\s*\d+\.\d+%',report.stdout,re.M))

def callgraph_command(path):
    return ['perf','report','--stdio','--children','--sort','dso,symbol','--percent-limit','1','--call-graph','graph,1,caller','-i',str(path)]

class ProxyProfile:
    def __init__(self, case, tids, mode):
        self.case,self.tids,self.mode=Path(case),sorted(set(tids)),mode
        self.process=None
        self.stream=None

    def start(self):
        if not self.tids:
            raise RuntimeError('no native Proxy TIDs for perf')
        self.stream=(self.case/'perf.stderr.log').open('w')
        command=['perf','record','-e','cpu-clock','-F','99','--no-inherit','-t',','.join(map(str,self.tids)),'-o',str(self.case/'perf.data')] + callgraph_args(self.mode)
        self.command=command
        # Stay in the benchmark's process group so driver timeout/interrupt
        # also stops perf. No helper child or detached recorder is needed.
        try:
            self.process=subprocess.Popen(command,stdout=subprocess.DEVNULL,stderr=self.stream)
        except Exception:
            self.stream.close()
            raise
        time.sleep(0.1)
        if self.process.poll() is not None:
            self.stream.close()
            raise RuntimeError('PERF_START_FAILED: see perf.stderr.log')
        print('DIAG_PERF_BEGIN tids='+','.join(map(str,self.tids)),flush=True)

    def stop(self):
        if self.process is None:
            return
        ended_early=self.process.poll() is not None
        if not ended_early:
            self.process.send_signal(signal.SIGINT)
            try:
                self.process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        self.stream.close()
        command=report_command(self.case/'perf.data')
        report=subprocess.run(command,capture_output=True,text=True,timeout=60)
        (self.case/'perf.report.txt').write_text(report.stdout+report.stderr)
        valid=valid_report(report) and not ended_early
        graph_rc=None
        if self.mode!='none':
            graph_command=callgraph_command(self.case/'perf.data')
            graph=subprocess.run(graph_command,capture_output=True,text=True,timeout=60)
            graph_rc=graph.returncode
            (self.case/'perf.callgraph.txt').write_text(graph.stdout+graph.stderr)
            valid=valid and graph_rc==0
        (self.case/'perf.status.json').write_text(json.dumps(dict(ok=valid,ended_early=ended_early,record_rc=self.process.returncode,report_rc=report.returncode,callgraph_rc=graph_rc,tids=self.tids,command=self.command,phase='dispatch_bf16',iterations_per_profiler_period=200),indent=2))
        if not valid:
            raise RuntimeError('PERF_REPORT_EMPTY_OR_FAILED: see perf.report.txt/perf.stderr.log')
        print('DIAG_PERF_DONE',flush=True)
