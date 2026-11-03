"""Sample the 204 UB port while guarded Normal factor cases execute."""

import json
import logging
import re
import subprocess
import sys
import time
from pathlib import Path


LOGGER = logging.getLogger('uccl.normal_port_sampler')
COUNTERS = {
    'tx_busi_flit_num', 'rx_busi_flit_num', 'tx_busi_pkt_num',
    'rx_busi_pkt_num', 'tx_ctrl_pkt_flit_num', 'rx_ctrl_pkt_flit_num',
    'retry_req_sum', 'crc_error_sum',
}
EVENT_MARKERS = ('[testing]', '[tuning]', 'BENCH_SCRIPT_DONE',
                 'Traceback', 'AssertionError', 'ChildFailedError', 'DistNetworkError')


def parse_counters(output: bytes) -> dict[str, int]:
    values: dict[str, int] = {}
    for line in output.decode(errors='replace').splitlines():
        match = re.match(r'([^:]+):\s*(0x[0-9a-fA-F]+)\s*$', line)
        if match and match[1] in COUNTERS:
            values[match[1]] = int(match[2], 16)
    return values


def main() -> None:
    case_root, output, stop = (Path(value) for value in sys.argv[1:])
    offsets: dict[str, int] = {}
    started = time.monotonic()
    last_active = started
    count = 0
    with (output / 'samples.jsonl').open('w') as samples, \
         (output / 'events.jsonl').open('w') as events:
        while time.monotonic() - started < 10800:
            active = []
            if case_root.is_dir():
                for run in sorted(case_root.glob('normal_*')):
                    log = run / 'bench.log'
                    if not log.is_file():
                        continue
                    previous = offsets.get(run.name, 0)
                    with log.open('rb') as stream:
                        stream.seek(previous)
                        chunk = stream.read()
                        offsets[run.name] = stream.tell()
                    for line in chunk.decode(errors='replace').splitlines():
                        if any(marker in line for marker in EVENT_MARKERS):
                            event = {'case': run.name, 'observed_monotonic_ns': time.monotonic_ns(),
                                     'initial_backlog': previous == 0, 'log_line': line[:700]}
                            events.write(json.dumps(event) + '\n')
                    if not (run / 'status').exists():
                        active.append(run.name)
            events.flush()
            if active:
                last_active = time.monotonic()
                start_ns = time.monotonic_ns()
                try:
                    result = subprocess.run(
                        ['ubctl', '-c', '0', '-d', '1', '-m', 'dl', '-p', '1',
                         '-f', 'pkt_stats'], stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE, timeout=5, check=False)
                except subprocess.TimeoutExpired:
                    rc, counters = 124, {}
                else:
                    rc, counters = result.returncode, parse_counters(result.stdout)
                sample = {'cases': active, 'start_monotonic_ns': start_ns,
                          'end_monotonic_ns': time.monotonic_ns(), 'rc': rc,
                          'counters': counters}
                samples.write(json.dumps(sample) + '\n')
                samples.flush()
                count += 1
            if stop.exists() or time.monotonic() - last_active > 180:
                break
            if (case_root / 'normal_topk_16' / 'status').exists():
                break
            time.sleep(1)
    LOGGER.warning('NORMAL_SAMPLER_DONE samples=%d output=%s', count, output)


if __name__ == '__main__':
    logging.basicConfig(level=logging.WARNING, format='%(message)s')
    main()
