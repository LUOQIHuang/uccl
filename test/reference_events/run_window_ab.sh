#!/usr/bin/env bash
set -euo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
BACKEND= RANK= BUILD= JOB= MASTER=141.61.17.202 PORT=33080 GID=3 PART=performance
ROOT="${URMA_DIAG_HOME:-${VERBS_DIAG_HOME:-${HOME:?}}}/metax_dev/reference_event_results"
DEST="$HERE/results"
while [ "$#" -gt 0 ]; do
 case "$1" in
 --backend) BACKEND=${2:?};shift 2;; --node-rank) RANK=${2:?};shift 2;;
 --build-dir) BUILD=${2:?};shift 2;; --job) JOB=${2:?};shift 2;;
 --master-addr) MASTER=${2:?};shift 2;; --port) PORT=${2:?};shift 2;;
 --output) ROOT=${2:?};shift 2;; --destination) DEST=${2:?};shift 2;;
 --gid-index) GID=${2:?};shift 2;; --part) PART=${2:?};shift 2;;
 *) echo "unknown argument $1" >&2;exit 2;;
 esac
done
[[ "$BACKEND" == verbs || "$BACKEND" == urma ]] && [[ "$RANK" == 0 || "$RANK" == 1 ]] && [[ "$JOB" =~ ^[a-zA-Z0-9_-]+$ ]] && [ -n "$BUILD" ] || exit 2
[[ "$PART" == performance || "$PART" == trace ]] || exit 2
[[ "$PORT" =~ ^[0-9]+$ ]] && ((PORT>=1024 && PORT<65534)) || exit 2
if [ "$BACKEND" = verbs ]; then
 ENTRY="$HERE/../verbs_baseline/run.sh";PYTHON=${VERBS_DIAG_PYTHON:-python3}
 EXTRA=(--variant current --no-profile --repeats 1 --gid-index "$GID" --expected-kernel 6.6.0-145.3.34.165.oe2403sp3.aarch64)
else
 ENTRY="$HERE/../urma_mode_parity/run.sh";PYTHON=${URMA_DIAG_PYTHON:-${VERBS_DIAG_PYTHON:-python3}}
 EXTRA=(--backend urma --phase-suite --api-only --expected-kernel 6.6.0-gdr_f00955680_diag --normal-submit-schedule lane-mixed --normal-completion all)
fi
run_one() {
 local tag=$1 plan=$2 mode=$3 name="${JOB}_${1}_${2}" rc=0 collected=0
 local console="$ROOT/$name/console/node$RANK"
 [ ! -e "$ROOT/$name/node$RANK" ] || { echo 'result exists; use a new job' >&2;return 2; }
 mkdir -p "$console"
 local common=(--node-rank "$RANK" --mode normal --dispatch-focus --reference-events "$mode"
   --normal-window-plan "$plan" --master-addr "$MASTER" --port "$PORT"
   --normal-tokens 4096 --warmups 5 --samples 10 --build-dir "$BUILD" --output "$ROOT" --job "$name")
 bash "$ENTRY" "${EXTRA[@]}" "${common[@]}" --action check 2>&1 | tee "$console/check.log" || rc=$?
 if [ "$rc" = 0 ]; then
   bash "$ENTRY" "${EXTRA[@]}" "${common[@]}" --action run 2>&1 | tee "$console/run.log" || rc=$?
 fi
 "$PYTHON" "$HERE/../gpu_stage_recovery/collect.py" "$ROOT/$name" --node-rank "$RANK" --trace-build "$BUILD" --destination "$DEST/$name" || collected=$?
 [ "$rc" = 0 ] || return "$rc"
 return "$collected"
}
if [ "$PART" = performance ]; then
 run_one r01 default paced
 run_one r01 peer4 paced
 run_one r02 peer4 paced
 run_one r02 default paced
else
 run_one trace default on
 run_one trace peer4 on
fi
echo "WINDOW_AB_PASSED backend=$BACKEND part=$PART rank=$RANK job=$JOB destination=$DEST"
