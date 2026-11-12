#!/usr/bin/env bash
# One causal variable: coalesced per-lane submission vs a separate WRITE stage.
set -euo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
RANK= BUILD= JOB= MASTER=141.61.17.202 PORT=32980 PART=performance
ROOT="${URMA_DIAG_HOME:-${HOME:?}}/metax_dev/reference_event_results"
PYTHON=${URMA_DIAG_PYTHON:-${VERBS_DIAG_PYTHON:-python3}}
DEST="$HERE/results"
while [ "$#" -gt 0 ]; do
 case "$1" in
 --node-rank) RANK=${2:?};shift 2;; --build-dir) BUILD=${2:?};shift 2;;
 --job) JOB=${2:?};shift 2;; --master-addr) MASTER=${2:?};shift 2;;
 --port) PORT=${2:?};shift 2;; --output) ROOT=${2:?};shift 2;;
 --destination) DEST=${2:?};shift 2;; --part) PART=${2:?};shift 2;;
 *) echo "unknown argument $1" >&2;exit 2;;
 esac
done
[[ "$RANK" == 0 || "$RANK" == 1 ]] && [[ "$JOB" =~ ^[a-zA-Z0-9_-]+$ ]] && [ -n "$BUILD" ] || { echo 'rank/build/job required' >&2;exit 2; }
[[ "$PART" == performance || "$PART" == trace ]] || { echo 'part must be performance|trace' >&2;exit 2; }
[[ "$PORT" =~ ^[0-9]+$ ]] && ((PORT>=1024 && PORT<65535)) || exit 2
run_one() {
 local tag=$1 schedule=$2 mode=$3 name rc=0 collect_rc=0
 name="${JOB}_${tag}_${schedule//-/_}"
 local console="$ROOT/$name/console/node$RANK"
 [ ! -e "$ROOT/$name/node$RANK" ] || { echo "result exists: $name; use a new job" >&2;return 2; }
 mkdir -p "$console"
 local common=(--backend urma --mode normal --phase-suite --api-only --dispatch-focus
   --reference-events "$mode" --normal-submit-schedule "$schedule"
   --normal-write-order default --normal-completion all
   --node-rank "$RANK" --master-addr "$MASTER" --port "$PORT"
   --normal-tokens 4096 --warmups 5 --samples 10
   --build-dir "$BUILD" --job "$name" --output "$ROOT")
 # Collect whatever exists on failure; never run the next case after failure.
 bash "$HERE/../urma_mode_parity/run.sh" "${common[@]}" --action check 2>&1 | tee "$console/check.log" || rc=$?
 if [ "$rc" = 0 ]; then
   bash "$HERE/../urma_mode_parity/run.sh" "${common[@]}" --action run 2>&1 | tee "$console/run.log" || rc=$?
 fi
 "$PYTHON" "$HERE/../gpu_stage_recovery/collect.py" "$ROOT/$name" --node-rank "$RANK" --trace-build "$BUILD" --destination "$DEST/$name" || collect_rc=$?
 [ "$rc" = 0 ] || return "$rc"
 return "$collect_rc"
}
if [ "$PART" = performance ]; then
 # ABBA, fixed in advance; both nodes run identical case names and order.
 run_one r01 lane-mixed paced
 run_one r01 writes-first paced
 run_one r02 writes-first paced
 run_one r02 lane-mixed paced
else
 # Run only after the performance result supports this mechanism, or to audit
 # whether the switch actually changed the SDK chains. Not a repeat campaign.
 run_one trace lane-mixed on
 run_one trace writes-first on
fi
echo "SUBMIT_AB_PASSED part=$PART rank=$RANK destination=$DEST job=$JOB"
