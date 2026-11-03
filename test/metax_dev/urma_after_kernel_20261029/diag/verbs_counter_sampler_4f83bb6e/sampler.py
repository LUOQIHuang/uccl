"""Read HCA and netdev port counters while matched verbs cases run."""

import json
import sys
import time
from pathlib import Path


COUNTERS = Path('/sys/class/infiniband/rocep40s0f0/ports/1/counters')
NET = Path('/sys/class/net/enp40s0f0np0/statistics')
NAMES = ('port_xmit_data', 'port_rcv_data', 'port_xmit_packets', 'port_rcv_packets')
NET_NAMES = ('tx_bytes', 'rx_bytes', 'tx_packets', 'rx_packets')
MARKERS = ('Start experiment', 'All correctness tests passed', '[tuning]',
           'Dispatch + combine bandwidth:', 'Dispatch bandwidth:',
           'Dispatch send/recv time:', 'BENCH_SCRIPT_DONE', 'Traceback',
           'AssertionError', 'ChildFailedError', 'DistNetworkError')


def read_counters(path: Path, names: tuple[str, ...]) -> dict[str, int | None]:
    values = {}
    for name in names:
        try:
            values[name] = int((path / name).read_text().strip())
        except (OSError, ValueError):
            values[name] = None
    return values


def main() -> None:
    root, output, stop = (Path(value) for value in sys.argv[1:])
    offsets: dict[str, int] = {}
    with (output / 'samples.jsonl').open('w') as samples, \
         (output / 'events.jsonl').open('w') as events:
        while True:
            active = []
            for run in sorted(root.glob('verbs_*')):
                log = run / 'bench.log'
                if not log.is_file():
                    continue
                old = offsets.get(run.name, 0)
                with log.open('rb') as stream:
                    stream.seek(old)
                    chunk = stream.read()
                    offsets[run.name] = stream.tell()
                for line in chunk.decode(errors='replace').splitlines():
                    if any(marker in line for marker in MARKERS):
                        events.write(json.dumps({'case': run.name, 'monotonic_ns': time.monotonic_ns(),
                                                 'initial_backlog': old == 0, 'line': line[:700]}) + '\n')
                if not (run / 'status').exists():
                    active.append(run.name)
            events.flush()
            start = time.monotonic_ns()
            port = read_counters(COUNTERS, NAMES)
            net = read_counters(NET, NET_NAMES)
            samples.write(json.dumps({'cases': active, 'start_monotonic_ns': start,
                                      'end_monotonic_ns': time.monotonic_ns(),
                                      'wall_ns': time.time_ns(), 'port': port, 'net': net}) + '\n')
            samples.flush()
            if stop.exists():
                break
            time.sleep(1)


if __name__ == '__main__':
    main()
