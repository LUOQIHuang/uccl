#!/usr/bin/env bash
set -u
ulimit -c 0
export LC_ALL=C
base=/home/f00955680/metax_dev/urma_after_kernel_20261029/diag
driver="$base/urma_sweep_driver_4ed19a6b_ll"
[ ! -e "$driver" ] || { echo DRIVER_EXISTS; exit 70; }
mkdir -m 700 "$driver" || exit 71
printf '%s' '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' | base64 -d | gzip -d > "$driver/node.sh"
printf '%s  %s\n' 'd77938cb82435b4c2677db9cc40dff33cde980cd3f79913aa2581653c00d734c' "$driver/node.sh" | sha256sum -c - || exit 72
case "$(hostname -s)" in master) rank=0;; worker1) rank=1;; *) exit 73;; esac
printf 'DRIVER_HOST=%s RANK=%s MODE=ll\n' "$(hostname -s)" "$rank"
index=0
while read -r case_id tokens hidden topk; do
 [ -n "$case_id" ] || continue
 index=$((index+1))
 printf 'CASE_BEGIN id=%s tokens=%s hidden=%s topk=%s index=%s\n' "$case_id" "$tokens" "$hidden" "$topk" "$index"
 local_rc=0
 bash "$driver/node.sh" ll "$case_id" "$tokens" "$hidden" "$topk" "$index" > "$driver/$case_id.log" 2>&1 || local_rc=$?
 cat "$driver/$case_id.log"
 printf 'CASE_LOCAL_END id=%s rc=%s\n' "$case_id" "$local_rc"
 # Each node reports its final status; advance only after both have finished.
 sync_rc=0
 timeout -k 2s 190s python3 - "$rank" "$((30300+index))" "$case_id" "$local_rc" <<'PYPAIR' || sync_rc=$?
import socket,sys,time
rank,port=int(sys.argv[1]),int(sys.argv[2]); name,rc=sys.argv[3],int(sys.argv[4]); msg=f'{name} {rc}\n'.encode()
if rank==0:
 s=socket.socket();s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);s.settimeout(180);s.bind(('0.0.0.0',port));s.listen(1);conn,_=s.accept();s.close()
else:
 end=time.monotonic()+180
 while True:
  try:conn=socket.create_connection(('141.61.17.202',port),timeout=3);break
  except OSError:
   if time.monotonic()>end:raise
   time.sleep(0.5)
conn.settimeout(10);conn.sendall(msg);peer=conn.makefile('rb').readline(128).decode().strip();conn.close()
print('CASE_PAIR_STATUS',name,'local',rc,'peer',peer,flush=True)
if peer!=f'{name} 0' or rc: raise SystemExit(1)
PYPAIR
 if [ "$local_rc" -ne 0 ] || [ "$sync_rc" -ne 0 ]; then
  printf 'SWEEP_STOP case=%s local_rc=%s pair_rc=%s\n' "$case_id" "$local_rc" "$sync_rc"; exit 74
 fi
 printf 'CASE_PAIR_PASS=%s\n' "$case_id"
done <<'CASES'
ll_tokens_64 64 7168 8
ll_tokens_128 128 7168 8
ll_tokens_256 256 7168 8
ll_tokens_512 512 7168 8
ll_tokens_1024 1024 7168 8
ll_hidden_2048 128 2048 8
ll_hidden_4096 128 4096 8
ll_hidden_6144 128 6144 8
ll_hidden_8192 128 8192 8
ll_topk_6 128 7168 6
ll_topk_8 128 7168 8
ll_topk_10 128 7168 10
CASES
printf 'SWEEP_12_CASES_PASSED\n'
