"""Sample one UB port while the paired LL benchmark cases are running."""

import json
import re
import subprocess
import sys
import time
from pathlib import Path


CASES = (
    'll_tokens_64', 'll_tokens_128', 'll_tokens_256', 'll_tokens_512',
    'll_tokens_1024', 'll_hidden_2048', 'll_hidden_4096', 'll_hidden_6144',
    'll_hidden_8192', 'll_topk_6', 'll_topk_8', 'll_topk_10',
)
COUNTERS = {'tx_busi_flit_num', 'rx_busi_flit_num', 'tx_busi_pkt_num',
            'rx_busi_pkt_num', 'retry_req_sum', 'crc_error_sum'}
EVENT_MARKERS = ('Start experiment', 'All correctness tests passed',
                 'Dispatch + combine bandwidth:', 'Dispatch bandwidth:',
                 'Dispatch send/recv time:', 'BENCH_SCRIPT_DONE',
                 'Traceback', 'AssertionError', 'ChildFailedError')


def main() -> None:
    case_root, output, stop = (Path(value) for value in sys.argv[1:])
    offsets: dict[str, int] = {}
    with (output / 'samples.jsonl').open('w') as samples, \
         (output / 'events.jsonl').open('w') as events:
        while True:
            active = []
            for name in CASES:
                run = case_root / name
                log = run / 'bench.log'
                if not log.is_file():
                    continue
                previous = offsets.get(name, 0)
                with log.open('rb') as stream:
                    stream.seek(previous)
                    chunk = stream.read()
                    offsets[name] = stream.tell()
                for line in chunk.decode(errors='replace').splitlines():
                    if any(marker in line for marker in EVENT_MARKERS):
                        item = {'case': name, 'observed_monotonic_ns': time.monotonic_ns(),
                                'initial_backlog': previous == 0, 'log_line': line[:700]}
                        events.write(json.dumps(item) + '\n')
                if not (run / 'status').exists():
                    active.append(name)
            events.flush()
            if active:
                start = time.monotonic_ns()
                try:
                    result = subprocess.run(
                        ['ubctl', '-c', '0', '-d', '1', '-m', 'dl', '-p', '1',
                         '-f', 'pkt_stats'], stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE, timeout=5, check=False)
                except subprocess.TimeoutExpired:
                    item = {'cases': active, 'start_monotonic_ns': start,
                            'end_monotonic_ns': time.monotonic_ns(), 'rc': 124,
                            'counters': {}}
                else:
                    values = {}
                    for line in result.stdout.decode(errors='replace').splitlines():
                        match = re.match(r'([^:]+):\s*(0x[0-9a-fA-F]+)\s*$', line)
                        if match and match[1] in COUNTERS:
                            values[match[1]] = int(match[2], 16)
                    item = {'cases': active, 'start_monotonic_ns': start,
                            'end_monotonic_ns': time.monotonic_ns(),
                            'rc': result.returncode, 'counters': values}
                samples.write(json.dumps(item) + '\n')
                samples.flush()
            if stop.exists():
                break
            time.sleep(1)


if __name__ == '__main__':
    main()
