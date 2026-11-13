#!/usr/bin/env bash
# One directional fence intervention; no WRITE placement/window sweep.
set -euo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
RANK= BUILD= JOB= MASTER=141.61.17.202 PORT=33280 PART=smoke
ROOT="${URMA_DIAG_HOME:-${HOME:?}}/metax_dev/reference_event_results"
DEST="$HERE/results"
while [ "$#" -gt 0 ];do
 case "$1" in
 --node-rank) RANK=${2:?};shift 2;; --build-dir) BUILD=${2:?};shift 2;;
 --job) JOB=${2:?};shift 2;; --part) PART=${2:?};shift 2;;
 --master-addr) MASTER=${2:?};shift 2;; --port) PORT=${2:?};shift 2;;
 --output) ROOT=${2:?};shift 2;; --destination) DEST=${2:?};shift 2;;
 *) echo "unknown argument $1" >&2;exit 2;;
 esac
done
[[ "$RANK" == 0 || "$RANK" == 1 ]] && [[ "$JOB" =~ ^[a-zA-Z0-9_-]+$ ]] && [ -n "$BUILD" ] || exit 2
[[ "$PART" == smoke || "$PART" == performance || "$PART" == observe-smoke || "$PART" == observe || "$PART" == observe-a || "$PART" == observe-b ]] || exit 2
[[ "$PORT" =~ ^[0-9]+$ ]] && ((PORT>=1024 && PORT<65534)) || exit 2
PYTHON=${URMA_DIAG_PYTHON:-${VERBS_DIAG_PYTHON:-python3}}
ENTRY="$HERE/../urma_mode_parity/run.sh"
run_one(){
 local suffix=$1 plan=$2 tokens=$3 warmups=$4 samples=$5 name="${JOB}_${1}" rc=0 collected=0
 local profile=${6:-legacy} events=${7:-paced}
 local console="$ROOT/$name/console/node$RANK"
 [ ! -e "$ROOT/$name/node$RANK" ] || { echo 'result exists; use a new job' >&2;return 2; }
 mkdir -p "$console"
 local common=(--backend urma --node-rank "$RANK" --mode normal --phase-suite --api-only --dispatch-focus
   --reference-events "$events" --reference-profile "$profile" --normal-fadd-fence-plan "$plan"
   --normal-window-plan default --normal-submit-schedule lane-mixed --normal-completion all
   --expected-kernel 6.6.0-gdr_f00955680_diag --master-addr "$MASTER" --port "$PORT"
   --normal-tokens "$tokens" --warmups "$warmups" --samples "$samples"
   --build-dir "$BUILD" --output "$ROOT" --job "$name")
 bash "$ENTRY" "${common[@]}" --action check 2>&1 | tee "$console/check.log" || rc=$?
 if [ "$rc" = 0 ];then bash "$ENTRY" "${common[@]}" --action run 2>&1 | tee "$console/run.log" || rc=$?;fi
 "$PYTHON" "$HERE/../gpu_stage_recovery/collect.py" "$ROOT/$name" --node-rank "$RANK" --trace-build "$BUILD" --destination "$DEST/$name" || collected=$?
 [ "$rc" = 0 ] || return "$rc"
 return "$collected"
}
if [ "$PART" = smoke ];then
 run_one smoke128 peer-off 128 1 3
elif [ "$PART" = observe-smoke ];then
 run_one observe_smoke128 peer-off 128 1 3 boundaries on
else
 SMOKE=smoke128;PROFILE=legacy
 if [[ "$PART" == observe* ]];then SMOKE=observe_smoke128;PROFILE=boundaries;fi
 # The new dependency setting must pass correctness on this same module/boot.
 "$PYTHON" - "$ROOT/${JOB}_${SMOKE}/node$RANK" "$BUILD" "$RANK" "$PROFILE" <<'PY'
import json,sys
from pathlib import Path
node,build,rank=Path(sys.argv[1]),Path(sys.argv[2]),int(sys.argv[3])
profile=sys.argv[4]
read=lambda p:json.loads(p.read_text())
status=read(node/'normal/status.json');runtime=read(node/'normal/runtime.json')
setup=read(node/'setup.json');host=read(node/'host.json');manifest=read(build/'manifest.json')
assert status['rc']==0 and all(status['checks'].values()),'smoke not accepted'
assert setup['error'] is None and setup['fingerprint']['normal_fadd_fence_plan']=='peer-off','wrong smoke plan'
assert setup['fingerprint']['shape']==[128,128,7168,256,8],'wrong smoke shape'
assert runtime['module_sha']==manifest['module_sha'],'smoke module changed'
assert runtime['normal_fadd_fence_version']==1 and runtime['normal_fadd_fence']==('off' if rank else 'on'),'wrong smoke fence'
assert host['boot_id']==Path('/proc/sys/kernel/random/boot_id').read_text().strip(),'boot changed since smoke'
if profile=='boundaries':
    assert setup['fingerprint']['reference_profile']==profile and setup['fingerprint']['reference_events']=='on','wrong observer smoke'
    assert runtime['reference_boundary_version']==1 and runtime['reference_profile']==profile,'missing boundary capability'
PY
 if [[ "$PART" == observe* ]];then
  if [ "$PART" = observe-b ];then
   "$PYTHON" - "$ROOT" "$JOB" "$RANK" "$BUILD" <<'PY'
import json,sys
from pathlib import Path
root,job,rank,build=Path(sys.argv[1]),sys.argv[2],int(sys.argv[3]),Path(sys.argv[4])
read=lambda p:json.loads(p.read_text())
for step in ('paced','trace','post'):
    node=root/(job+'_all_on_'+step)/('node%d'%rank)
    status=read(node/'normal/status.json');runtime=read(node/'normal/runtime.json')
    setup=read(node/'setup.json')['fingerprint']
    assert status['rc']==0 and all(status['checks'].values()),'A did not pass; do not start B'
    assert setup['normal_fadd_fence_plan']=='all-on' and setup['reference_profile']=='boundaries','wrong A plan/profile'
    assert setup['reference_events']==('on' if step=='trace' else 'paced'),'wrong A sampling mode'
    assert runtime['module_sha']==read(build/'manifest.json')['module_sha'],'A module changed'
    assert read(node/'host.json')['boot_id']==Path('/proc/sys/kernel/random/boot_id').read_text().strip(),'A boot changed'
PY
  fi
  if [ "$PART" != observe-b ];then
   run_one all_on_paced all-on 4096 5 10 boundaries paced
   run_one all_on_trace all-on 4096 5 10 boundaries on
   run_one all_on_post all-on 4096 5 10 boundaries paced
  fi
  if [ "$PART" != observe-a ];then
   run_one peer_off_paced peer-off 4096 5 10 boundaries paced
   run_one peer_off_trace peer-off 4096 5 10 boundaries on
   run_one peer_off_post peer-off 4096 5 10 boundaries paced
  fi
 else
  run_one r01_all_on all-on 4096 5 10
  run_one r01_peer_off peer-off 4096 5 10
  run_one r02_peer_off peer-off 4096 5 10
  run_one r02_all_on all-on 4096 5 10
 fi
fi
echo "FADD_FENCE_SUITE_PASSED part=$PART rank=$RANK job=$JOB destination=$DEST"
