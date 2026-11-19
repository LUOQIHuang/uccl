# 用当前 RDMA 窗口补三队列深度对照

此前 verbs 串行和完整 Normal 都已采集。本次补上中间负载。同样的 12 对请求、三条队列和两个方向，比较每条队列深度 1 与深度 4，观察 WRITE→FADD 完成间隔是否拉长，以及整批耗时怎样变化。采完可在下一次 URMA 实验后离线比较。

每次迭代总共 12 条 WRITE（每条 459264 字节）和 12 条 FADD（每条加 32）。深度 1 分四批，每批三队列各一对，整批完成再发下一批。深度 4 在三个队列各链式提交四条 WRITE、四条 FADD 后轮询完成。verbs 使用三个 RC QP 和一个共用 CQ，GPU 显存注册走 DMA-BUF，每条请求有完成，FADD 保留 verbs 的提交标志，不加 `IBV_SEND_FENCE`。链式批处理是本微基准的共同配方。与完整 Normal 的 FIFO、GPU 消费和 head 反馈行为分别解释。

## RDMA 窗口操作

更新仓库后在根目录操作。两端使用已验证的 verbs 内核 `6.6.0-145.3.34.165.oe2403sp3.aarch64`、MACA 3.9 和 GID3。202 用 `RANK=0`，204 用 `RANK=1`，两端都用 `CPU=192`，与计划中的 URMA 窗口对齐。运行时会检查 GPU NUMA 和实际绑核。两个内核的 CPU 编号拓扑有差异，CPU 编号相同仅表示对齐逻辑编号。

以下使用当前 v4 源码树。如已经准备好 v3，可继续使用原提交 `026522c9` 中的入口和目录。

```bash
export VERBS_DIAG_HOME=/home/h00918771
export URMA_DIAG_HOME="$VERBS_DIAG_HOME"
export VERBS_DIAG_PYTHON="$VERBS_DIAG_HOME/metax_dev/venv-maca-py312/bin/python"
export URMA_PAIR_LOAD_PYTHON="$VERBS_DIAG_PYTHON"
export MACA_PATH=/opt/maca-3.9.0
RANK=0 CPU=192 # 204改为 RANK=1 CPU=192
JOB=urma_pair_depth12_01
TREE="$VERBS_DIAG_HOME/metax_dev/urma_pair_load_src_v4"
BUILD="$VERBS_DIAG_HOME/metax_dev/urma_pair_load_build/verbs_v4"
ROOT="$VERBS_DIAG_HOME/metax_dev/urma_pair_load_results"
DEST="$PWD/test/urma_pair_load/results"

verbs_depth() {
  bash test/urma_pair_load/run.sh --action "$1" \
    --backend verbs --suite depth12 --node-rank "$RANK" --job "$JOB" \
    --master-addr 141.61.17.202 --port 34480 --gid-index 3 \
    --worker-cpu "$CPU" --rounds 3 --warmups 20 --samples 101 \
    --tree "$TREE" --build-dir "$BUILD" --output "$ROOT" --destination "$DEST"
}
verbs_depth prepare && verbs_depth build && verbs_depth check
```

prepare 只准备独立源码，可提前做。build 和 check 各节点独立完成，使用现有的设备、内核、加载和版本校验。通过后，202 先执行，204 在 180 秒内执行同一命令。

```bash
verbs_depth run
```

一次覆盖 2 个深度 × 2 个发送方向 × 3 轮。每个点预热 20 次，采样 101 次并丢掉第一个计时样本，合计 1200 个有效发送迭代，14400 个有效请求对。采集逐对 SDK 提交/完成、相同批次及同队列 WRITE 边界。每次迭代做 GPU payload 和计数器正确性检查。

两端应显示 `URMA_PAIR_LOAD_RUN_PASSED`，且日志末尾有 `URMA_PAIR_LOAD_ALL_PASSED`。入口沿用原目录和完成标志，`--backend verbs` 选择 RDMA。结果自动收集，中断后可用 `verbs_depth collect`。保留 `test/urma_pair_load/results/urma_pair_depth12_01/verbs/` 下两个节点的完整目录。完整原始记录可离线分析，在窗口内上传即可。

```bash
"$VERBS_DIAG_PYTHON" test/urma_pair_load/analyze.py --backend verbs \
  --root "$DEST/$JOB" --output "$DEST/$JOB/summary_verbs"
```

## 两种环境的覆盖安排

| 环境 | 本窗口必测 | 有余量可提前准备 | 可直接使用的结果 |
| --- | --- | --- | --- |
| RDMA | 当前文档的 depth12，两种深度、双方向、三轮一次完成 | 汇总留给离线处理 | 最小串行、完整 Normal 的 BF16 阶段采集及 FADD 专项 |
| URMA | [完整 Normal 的密集 FADD 轮询采集](../fadd_path/DENSE.zh-CN.md) | [固定深度的链内顺序对照](ORDER12.zh-CN.md) | 三队列深度对照、最小串行、四对负载、完整 Normal 阶段采集和 fence 干预 |

密集采集解决的是完整 Normal 中“返回值已经能读到后，为何还没取得完成记录”的问题。仓库已有稀疏采样的两后端结果，尚缺密集采样结果。它和 depth12 回答不同问题，不能用一个代替另一个。窗口紧张时先拿齐 depth12 的双端原始文件与正确性结果。
