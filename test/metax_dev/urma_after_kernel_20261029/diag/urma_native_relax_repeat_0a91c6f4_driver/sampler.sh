#!/usr/bin/env bash
set -eu
ulimit -c 0
base=/home/f00955680/metax_dev/urma_after_kernel_20261029/diag
bench="$base/bench_pair_2274e1bc/urma_native_relax_repeat_0a91c6f4/normal/relax_repeat_normal_4096"
out="$base/bw_native_relax_repeat_0a91c6f4"
[ ! -e "$out" ]
mkdir -m 700 "$out" "$out/raw"
printf 'HOST=%s BENCH=%s OUT=%s\n' "$(hostname -s)" "$bench" "$out"
exec nice -n 19 python3 - "$bench" "$out" <<'PY'
import json,re,subprocess,sys,time
from pathlib import Path
bench,out=map(Path,sys.argv[1:]); log=bench/'bench.log'; status=bench/'status'
limit=time.monotonic()+900
while not log.exists():
 if status.exists(): raise SystemExit('BENCH_ENDED_BEFORE_LOG')
 if time.monotonic()>limit: raise SystemExit('BENCH_LOG_NOT_AVAILABLE')
 time.sleep(1)
wanted={'tx_busi_flit_num','rx_busi_flit_num','tx_ctrl_pkt_flit_num','rx_ctrl_pkt_flit_num','tx_busi_pkt_num','rx_busi_pkt_num','crc_error_sum','retry_req_sum'}
samples=out/'samples.jsonl';events=out/'events.jsonl';offset=0
print('INITIAL_LOG_BYTES',log.stat().st_size,'STATUS_ALREADY_PRESENT',status.exists(),flush=True)
with samples.open('w') as sm,events.open('w') as ev:
 for i in range(2700):
  with log.open('rb') as lf:
   lf.seek(offset); chunk=lf.read(); offset=lf.tell()
  for line in chunk.decode(errors='replace').splitlines():
   if any(k in line for k in ['PHASE=','[testing]', '[tuning]', '[layout]', 'BENCH_', 'Traceback','Error']):
    item={'observed_monotonic_ns':time.monotonic_ns(),'observed_wall_ns':time.time_ns(),'initial_backlog':i==0,'log_line':line[:700]};ev.write(json.dumps(item)+'\n');ev.flush()
  for chip in (0,1):
   begin=time.monotonic_ns();wall=time.time_ns()
   try:
    p=subprocess.run(['ubctl','-c',str(chip),'-d','1','-m','dl','-p','1','-f','pkt_stats'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=5); rc=p.returncode;raw=p.stdout;err=p.stderr
   except subprocess.TimeoutExpired as e:
    rc=124;raw=e.stdout or b'';err=e.stderr or b''
   end=time.monotonic_ns();(out/'raw'/f'{i:04d}_{chip}.stdout').write_bytes(raw)
   if err: (out/'raw'/f'{i:04d}_{chip}.stderr').write_bytes(err)
   values={}
   for line in raw.decode(errors='replace').splitlines():
    m=re.match(r'([^:]+):\s*(0x[0-9a-fA-F]+)\s*$',line)
    if m and m.group(1) in wanted: values[m.group(1)]=int(m.group(2),16)
   item=dict(index=i,chip=chip,die=1,port=1,wall_ns=wall,start_monotonic_ns=begin,end_monotonic_ns=end,rc=rc,counters=values)
   sm.write(json.dumps(item)+'\n');sm.flush()
  if status.exists(): print('BENCH_STATUS',status.read_text().strip(),flush=True);break
  time.sleep(1)
 else: print('SAMPLER_LIMIT_REACHED',flush=True)
print('SAMPLE_PATH',samples,'EVENT_PATH',events,'SAMPLES',i+1,flush=True)
PY
printf 'FINAL_SAMPLES\n'
tail -8 "$out/samples.jsonl"
