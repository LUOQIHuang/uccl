#!/usr/bin/env bash
set -euo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
JOB= RANK= BUILD= ROOT= PART=all ACTION=run VARIANT=archived
MASTER=141.61.17.202 PORT=32680 GID=3 CPUS=auto
PLAN="$HERE/../urma_mode_parity/results/urma_notify_05/urma_notify_05_micro_r01/node0/micro/plan.json"
while [ "$#" -gt 0 ];do
 case "$1" in
 --job) JOB=${2:?};shift 2;; --node-rank) RANK=${2:?};shift 2;; --build-dir) BUILD=${2:?};shift 2;;
 --output) ROOT=${2:?};shift 2;; --part) PART=${2:?};shift 2;; --action) ACTION=${2:?};shift 2;;
 --variant) VARIANT=${2:?};shift 2;; --master-addr) MASTER=${2:?};shift 2;; --port) PORT=${2:?};shift 2;;
 --gid-index) GID=${2:?};shift 2;; --micro-cpus) CPUS=${2:?};shift 2;; --matched-plan) PLAN=${2:?};shift 2;;
 *) echo "unknown Normal focus argument: $1" >&2;exit 2;;
 esac
done
[[ "$JOB" =~ ^[a-zA-Z0-9_-]+$ ]] && [[ "$RANK" == 0 || "$RANK" == 1 ]] && [ -n "$BUILD" ] || { echo 'valid job/rank/build required' >&2;exit 2; }
[[ "$VARIANT" == archived || "$VARIANT" == current ]] || { echo 'variant must be archived or current' >&2;exit 2; }
case "$PART" in all|notify|normal);;*) echo 'part must be all/notify/normal' >&2;exit 2;;esac
case "$ACTION" in check|run);;*) echo 'action must be check/run (no automatic compilation)' >&2;exit 2;;esac
[ -f "$PLAN" ] || { echo "matched plan missing: $PLAN" >&2;exit 2; }
TASK_ROOT=${VERBS_DIAG_HOME:-${HOME:?}}
ROOT=${ROOT:-"$TASK_ROOT/metax_dev/verbs_normal_focus_results/$JOB"}
CONSOLE="$ROOT/$JOB/console/node$RANK";mkdir -p "$CONSOLE"
COMMON=(--variant "$VARIANT" --mode normal --node-rank "$RANK" --master-addr "$MASTER" --build-dir "$BUILD" --output "$ROOT" --port "$PORT" --gid-index "$GID" --expected-kernel 6.6.0-145.3.34.165.oe2403sp3.aarch64 --no-profile --repeats 1 --notify-check --campaign-check)
one(){ local tag=$1;shift;bash "$HERE/run.sh" "${COMMON[@]}" --job "${JOB}_${tag}" "$@" 2>&1 | tee "$CONSOLE/${tag}.log"; }
one check --action check
[ "$ACTION" = run ] || { echo 'NORMAL_FOCUS_CHECK_PASSED';exit 0; }
if [ "$PART" != normal ];then
 for repeat in 1 2 3;do
  one "notify_r0${repeat}" --action micro --micro-suite notify --micro-repeat "$repeat" --micro-plan "$PLAN" --micro-threads 4 --micro-cpus "$CPUS" --warmups 20 --samples 50 --case-timeout 900
 done
fi
if [ "$PART" != notify ];then
 one baseline --action run --warmups 50 --samples 50
 one cpu --action run --cpu-diagnostics --cpu-light --warmups 50 --samples 50
fi
echo "NORMAL_FOCUS_ALL_PASSED part=$PART output=$ROOT"
