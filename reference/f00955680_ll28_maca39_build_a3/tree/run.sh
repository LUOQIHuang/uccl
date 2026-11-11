set -e
rank=$1
hidden=$2
source /home/f00955680/metax_dev/activate-uccl.sh
if [ "$rank" = 0 ]; then
  source /home/f00955680/metax_dev/native-libs-3.7.0.38-G1NK1l/activate.sh
fi
unset UMDK_HOME PYTHONPATH
export PYTHONPATH="/home/f00955680/metax_dev/current-test/ll-stride256/module:/home/f00955680/uccl-ep-xpu-integration/ep/bench"
export URMA_LIBRARY_DIR=/home/g00953922/umdk_metax_isolated/lib
export LD_LIBRARY_PATH="$URMA_LIBRARY_DIR${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export CUDA_VISIBLE_DEVICES=0 PYTHONUNBUFFERED=1 DIAG_HIDDEN="$hidden"
export UCCL_EP_TRANSPORT=verbs UCCL_EP_VERBS_REG=dmabuf
export UCCL_IB_HCA=rocep40s0f0
export MCCL_IB_DISABLE=1 MCCL_SOCKET_IFNAME=enp33s0 MCCL_DEBUG=WARN
cd /home/f00955680/uccl-ep-xpu-integration/ep/bench
set +e
timeout -k 10s 300s torchrun \
  --nnodes=2 --nproc_per_node=1 --node_rank="$rank" \
  --master_addr=141.61.17.202 --master_port=29573 \
  --no-python python -u \
  /home/f00955680/metax_dev/current-test/ll-stride256/test.py
rc=$?
echo "DIAG_HIDDEN=${hidden}_RANK=${rank}_RC=$rc"
exit "$rc"
