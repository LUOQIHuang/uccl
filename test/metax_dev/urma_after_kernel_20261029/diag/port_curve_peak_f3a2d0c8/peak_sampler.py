"""Sample both physical ports, and stop our test if another opener appears."""
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

run, benchmark_pid, output, finished = sys.argv[1:]
benchmark_pid = int(benchmark_pid)
out = Path(output)
done = Path(finished)
ib = Path('/sys/class/infiniband/rocep40s0f0/ports/1/counters')


def openers():
    paths = ['/dev/mxcd', '/dev/uburma/udmac0d1e2']
    paths.extend(str(p) for p in Path('/dev/infiniband').glob('uverbs*'))
    paths = [p for p in paths if Path(p).exists()]
    result = subprocess.run(['fuser', *paths], capture_output=True, timeout=5)
    if result.returncode not in (0, 1):
        raise RuntimeError(f'fuser failed: {result.returncode}')
    pids = {int(x) for x in result.stdout.decode().split() if x.isdigit()}
    foreign = []
    for pid in pids:
        try:
            environment = Path(f'/proc/{pid}/environ').read_bytes().split(b'\0')
        except FileNotFoundError:
            continue
        if f'UCCL_CURVE_RUN={run}'.encode() not in environment:
            foreign.append(pid)
    return foreign


def sample(index):
    hca_start = time.monotonic_ns()
    tx = int((ib / 'port_xmit_data').read_text())
    rx = int((ib / 'port_rcv_data').read_text())
    errors = {name: int((ib / name).read_text()) for name in
              ('port_rcv_errors', 'port_xmit_discards')}
    hca_end = time.monotonic_ns()
    ub_start = time.monotonic_ns()
    result = subprocess.run(['ubctl', '-c', '0', '-d', '1', '-m', 'dl', '-p', '1',
                             '-f', 'pkt_stats'], capture_output=True, timeout=5, check=True)
    ub_end = time.monotonic_ns()
    counters = {}
    for line in result.stdout.decode(errors='replace').splitlines():
        match = re.match(r'([^:]+):\s*(0x[0-9a-fA-F]+)\s*$', line)
        if match and match[1] in {'tx_busi_flit_num', 'rx_busi_flit_num', 'crc_error_sum', 'retry_req_sum'}:
            counters[match[1]] = int(match[2], 16)
    if not {'tx_busi_flit_num', 'rx_busi_flit_num'} <= counters.keys():
        raise RuntimeError('UB counters unavailable')
    return {'index': index, 'hca_t0_ns': hca_start, 'hca_t1_ns': hca_end,
            'hca_tx': tx, 'hca_rx': rx, 'hca_errors': errors,
            'ub_t0_ns': ub_start, 'ub_t1_ns': ub_end,
            'ub': counters}


with out.open('w') as stream:
    for i in range(120):
        foreign = openers()
        if foreign:
            stream.write(json.dumps({'foreign_openers': foreign, 't_ns': time.monotonic_ns()}) + '\n')
            stream.flush()
            try:
                # The orchestrator starts only its own process in a new session.
                if os.getpgid(benchmark_pid) == benchmark_pid:
                    os.killpg(benchmark_pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            raise SystemExit('stopped: another GPU or network user appeared')
        stream.write(json.dumps(sample(i)) + '\n')
        stream.flush()
        if done.exists():
            break
        time.sleep(1)
    else:
        raise SystemExit('sampling deadline exceeded')
