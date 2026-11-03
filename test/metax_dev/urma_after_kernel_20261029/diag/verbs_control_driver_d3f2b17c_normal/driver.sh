#!/usr/bin/env bash
set -u
ulimit -c 0
export LC_ALL=C
base=/home/f00955680/metax_dev/urma_after_kernel_20261029/diag
driver="$base/verbs_matched_d3f2b17c_normal_driver"
[ ! -e "$driver" ] || { echo DRIVER_EXISTS; exit 70; }
mkdir -m 700 "$driver" || exit 71
cp "/home/f00955680/metax_dev/urma_after_kernel_20261029/diag/verbs_control_node_d3f2b17c/node.sh" "$driver/node.sh"
printf '%s  %s\n' 'c4a36626b7245db3cc1a488f885cd518a90583d8a8106fae99902140faf2e2c8' "$driver/node.sh" | sha256sum -c - || exit 72
case "$(hostname -s)" in master) rank=0;; worker1) rank=1;; *) exit 73;; esac
printf 'DRIVER_HOST=%s RANK=%s MODE=normal\n' "$(hostname -s)" "$rank"
index=90
while read -r case_id tokens hidden topk; do
 [ -n "$case_id" ] || continue
 index=$((index+1))
 printf 'CASE_BEGIN id=%s tokens=%s hidden=%s topk=%s index=%s\n' "$case_id" "$tokens" "$hidden" "$topk" "$index"
 local_rc=0
 bash "$driver/node.sh" normal "$case_id" "$tokens" "$hidden" "$topk" "$index" > "$driver/$case_id.log" 2>&1 || local_rc=$?
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
verbs_normal_main_1 4096 7168 8
verbs_normal_main_2 4096 7168 8
CASES
printf 'VERBS_NORMAL_CONTROLS_PASSED\n'
