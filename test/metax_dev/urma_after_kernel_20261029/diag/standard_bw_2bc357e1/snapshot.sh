#!/bin/bash
set -u
label=$1
echo SNAPSHOT="$label" HOST="$(hostname -s)" TIME="$(date -Ins)"
root=/sys/class/infiniband/rocep40s0f0/ports/1
for n in port_xmit_data port_rcv_data port_xmit_packets port_rcv_packets port_rcv_errors port_xmit_discards; do
 [ -r "$root/counters/$n" ] || { echo COUNTER_MISSING="$n"; exit 70; }
 printf 'HCA_%s=%s\n' "$n" "$(cat "$root/counters/$n")"
done
for n in req_cqe_error resp_cqe_error roce_adp_retrans packet_seq_err; do
 if [ -r "$root/hw_counters/$n" ]; then printf 'HCA_%s=%s\n' "$n" "$(cat "$root/hw_counters/$n")"; fi
done
tmp="${TMPDIR:?}/ub_${label}.txt"
timeout -k 1s 8s ubctl -c 0 -d 1 -m dl -p 1 -f pkt_stats > "$tmp" 2>&1 || { cat "$tmp"; exit 71; }
for n in tx_busi_flit_num rx_busi_flit_num retry_req_sum crc_error_sum; do
 value=$(awk -v n="$n:" '$1==n {print $2; exit}' "$tmp")
 [ -n "$value" ] || { echo UB_COUNTER_MISSING="$n"; exit 72; }
 printf 'UB_%s=%s\n' "$n" "$value"
done
