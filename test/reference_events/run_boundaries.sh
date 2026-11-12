#!/usr/bin/env bash
# Keep smoke separate from the three formal cases; never change transport knobs.
set -euo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
BACKEND=urma RANK= BUILD= JOB= PART=smoke MASTER=141.61.17.202 PORT=33180 GID=3
ROOT="${URMA_DIAG_HOME:-${VERBS_DIAG_HOME:-${HOME:?}}}/metax_dev/reference_event_results"
DEST="$HERE/results"
while [ "$#" -gt 0 ];do
 case "$1" in
 --backend) BACKEND=${2:?};shift 2;; --node-rank) RANK=${2:?};shift 2;;
 --build-dir) BUILD=${2:?};shift 2;; --job) JOB=${2:?};shift 2;; --part) PART=${2:?};shift 2;;
 --master-addr) MASTER=${2:?};shift 2;; --port) PORT=${2:?};shift 2;; --gid-index) GID=${2:?};shift 2;;
 --output) ROOT=${2:?};shift 2;; --destination) DEST=${2:?};shift 2;;
 *) echo "unknown argument $1" >&2;exit 2;;
 esac
done
[[ "$BACKEND" == urma || "$BACKEND" == verbs ]] && [[ "$RANK" == 0 || "$RANK" == 1 ]] && [[ "$JOB" =~ ^[a-zA-Z0-9_-]+$ ]] && [ -n "$BUILD" ] || exit 2
[[ "$PART" == smoke || "$PART" == formal ]] || exit 2
if [ "$BACKEND" = urma ];then PYTHON=${URMA_DIAG_PYTHON:-${VERBS_DIAG_PYTHON:-python3}};CASE=normal
else PYTHON=${VERBS_DIAG_PYTHON:-python3};CASE=r01_baseline/normal;fi
run_one(){
 local suffix=$1 step=$2 tokens=$3 name="${JOB}_${1}" rc=0 collected=0
 bash "$HERE/run.sh" --backend "$BACKEND" --action run --reference-profile boundaries \
   --step "$step" --tokens "$tokens" --node-rank "$RANK" --master-addr "$MASTER" \
   --build-dir "$BUILD" --job "$name" --output "$ROOT" --port "$PORT" --gid-index "$GID" || rc=$?
 "$PYTHON" "$HERE/../gpu_stage_recovery/collect.py" "$ROOT/$name" --node-rank "$RANK" --trace-build "$BUILD" --destination "$DEST/$name" || collected=$?
 [ "$rc" = 0 ] || return "$rc"
 return "$collected"
}
if [ "$PART" = smoke ];then
 run_one smoke128 trace 128
else
 "$PYTHON" -c 'import json,sys;d=json.load(open(sys.argv[1]));assert d["rc"]==0 and all(d["checks"].values()),"smoke not accepted"' "$ROOT/${JOB}_smoke128/node$RANK/$CASE/status.json"
 run_one paced paced 4096
 run_one trace trace 4096
 run_one post paced 4096
fi
echo "BOUNDARY_SUITE_PASSED backend=$BACKEND part=$PART rank=$RANK job=$JOB destination=$DEST"
