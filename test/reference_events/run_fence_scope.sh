#!/usr/bin/env bash
set -euo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
RANK= BUILD= JOB= PART=smoke MASTER=141.61.17.202 PORT=33480
ROOT="${URMA_DIAG_HOME:-${HOME:?}}/metax_dev/reference_event_results";DEST="$HERE/results"
while [ "$#" -gt 0 ];do
 case "$1" in
 --node-rank) RANK=${2:?};shift 2;; --build-dir) BUILD=${2:?};shift 2;;
 --job) JOB=${2:?};shift 2;; --part) PART=${2:?};shift 2;;
 --master-addr) MASTER=${2:?};shift 2;; --port) PORT=${2:?};shift 2;;
 --output) ROOT=${2:?};shift 2;; --destination) DEST=${2:?};shift 2;;
 *) echo "unknown argument $1" >&2;exit 2;;esac
done
[[ "$RANK" == 0 || "$RANK" == 1 ]] && [[ "$JOB" =~ ^[a-zA-Z0-9_-]+$ ]] && [ -n "$BUILD" ] || exit 2
[[ "$PART" == smoke || "$PART" == performance ]] || exit 2
[[ "$PORT" =~ ^[0-9]+$ ]] && ((PORT>=1024 && PORT<65534)) || exit 2
PYTHON=${URMA_DIAG_PYTHON:-python3}
run_one(){
 local suffix=$1 plan=$2 tokens=$3 warm=$4 samples=$5 name="${JOB}_${1}" rc=0 collected=0
 local console="$ROOT/$name/console/node$RANK"
 [ ! -e "$ROOT/$name/node$RANK" ] || { echo 'result exists; use new job' >&2;return 2; }
 mkdir -p "$console"
 local common=(--backend urma --mode normal --phase-suite --api-only --dispatch-focus
 --reference-events paced --reference-profile legacy --normal-fadd-fence-plan "$plan" --require-normal-atomic-roles
 --normal-write-order default --normal-completion all --normal-window-plan default --normal-submit-schedule lane-mixed
 --node-rank "$RANK" --master-addr "$MASTER" --port "$PORT" --expected-kernel 6.6.0-gdr_f00955680_diag
 --normal-tokens "$tokens" --warmups "$warm" --samples "$samples" --build-dir "$BUILD" --output "$ROOT" --job "$name")
 bash "$HERE/../urma_mode_parity/run.sh" "${common[@]}" --action check 2>&1 | tee "$console/check.log" || rc=$?
 if [ "$rc" = 0 ];then bash "$HERE/../urma_mode_parity/run.sh" "${common[@]}" --action run 2>&1 | tee "$console/run.log" || rc=$?;fi
 "$PYTHON" "$HERE/../gpu_stage_recovery/collect.py" "$ROOT/$name" --node-rank "$RANK" --trace-build "$BUILD" --destination "$DEST/$name" || collected=$?
 [ "$rc" = 0 ] || return "$rc";return "$collected"
}
if [ "$PART" = smoke ];then
 run_one smoke_all peer-off 128 1 3
 run_one smoke_tail peer-tail-off 128 1 3
else
 "$PYTHON" - "$ROOT" "$JOB" "$RANK" "$BUILD" <<'PY'
import json,sys
from pathlib import Path
root,job,rank,build=Path(sys.argv[1]),sys.argv[2],int(sys.argv[3]),Path(sys.argv[4])
read=lambda p:json.loads(p.read_text())
for suffix,plan in [('smoke_all','peer-off'),('smoke_tail','peer-tail-off')]:
 node=root/(job+'_'+suffix)/('node%d'%rank);status=read(node/'normal/status.json');runtime=read(node/'normal/runtime.json')
 setup=read(node/'setup.json')
 assert status['rc']==0 and all(status['checks'].values()) and status['checks']['normal_atomic_roles'],'smoke failed'
 assert setup['error'] is None and setup['fingerprint']['normal_fadd_fence_plan']==plan and setup['fingerprint']['shape']==[128,128,7168,256,8],'wrong smoke'
 assert runtime['normal_atomic_role_version']==1 and runtime['module_sha']==read(build/'manifest.json')['module_sha'],'wrong role capability/module'
 assert runtime['normal_fadd_fence']==(('tail-off' if plan=='peer-tail-off' else 'off') if rank else 'on'),'wrong effective smoke fence'
 assert read(node/'host.json')['boot_id']==Path('/proc/sys/kernel/random/boot_id').read_text().strip(),'boot changed'
PY
 run_one r01_all_on all-on 4096 5 10
 run_one r01_peer_off peer-off 4096 5 10
 run_one r01_peer_tail_off peer-tail-off 4096 5 10
 run_one r02_peer_tail_off peer-tail-off 4096 5 10
 run_one r02_peer_off peer-off 4096 5 10
 run_one r02_all_on all-on 4096 5 10
fi
echo "FENCE_SCOPE_PASSED part=$PART rank=$RANK job=$JOB"
