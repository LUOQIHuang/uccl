# URMA 阶段时延与稳定性测试：phase_v1

只定位Normal Dispatch主kernel时，请使用[两后端统一打桩文档](../dispatch_diag/README.zh-CN.md)，不直接运行旧全量阶段套件。

本轮只使用已验证的 URMA 内核环境，不切换到 verbs 内核。目标是复测当前实现的稳定性，并把整体耗时与 GPU 阶段、Proxy 工作量和等待关联。保持 Normal 8 数据+1控制通道、LL 单通道、Normal RELAX / LL STRONG、原子 fence 和对齐修复不变。

## 1. 测什么，怎样比较

每台服务器使用相同的新模块。默认做 3 轮，每轮先 baseline（关闭阶段埋点），再 instrumented（开启阶段埋点），两种模式依次 Normal、LL。共 6 次独立双机程序运行，每种模式各 3 次 baseline、3 次 instrumented。

baseline 与 instrumented 使用相同输入、预热、采样和 LL hook 工作负载。两端是同一次通信的参与者，不当作独立重复合并。首次 instrumented 保存 GPU trace，后续只记录耗时和统计，以控制文件体积。

每个完整调用测点：50 次预热、50 次 CUDA-event 计时，保存全部 50 次原始值，丢弃首个后以 49 个样本计算均值、中位数、p90、p95、标准差、CV 和 min/max。独立运行之间另取各轮均值的中位数，不把所有采样混成一个大样本。

Normal 保持 tokens=4096、hidden=7168、experts=256、topk=8、SM=24、NVL chunk=4，测 FP8/BF16 Dispatch chunk=32、Combine chunk=12/16。额外的 layout 测点单列，不当作 dispatch。

LL 保持 tokens=128、hidden=7168、experts=256、topk=8。主基线仍是不带 hook 的 Dispatch+Combine 完整调用。本轮的 hook profiler 改为发送后立即调用接收 hook，**不插入大 GEMM**，另外记录这种路径的完整调用耗时。它是诊断工作负载，不能直接与历史带 GEMM 的 hook 分段数值算加速比。正确性矩阵及 kernel 使用原 benchmark，不改算法。

## 2. 本次阶段埋点的范围

| 指标 | 实际含义 |
| --- | --- |
| `collect_ready_commands` | 非空 ring 中已就绪命令的收集时间；不含 GPU 发布前等待，不含空队列轮询时间 |
| `queue_depth_observation` | 有新命令时观察到的 head-tail；items/calls 是这些观察的平均深度，不是全程时间加权占用 |
| `proxy_batch` | 一批 URMA 命令的组织、提交及控制处理的总 CPU 墙钟范围 |
| `proxy_dispatch_batch / proxy_combine_batch` | 依据命令标记分类的批次范围；混合批次只进入 proxy_batch，控制命令可能带相同标记 |
| `group_prepare` | 每通道扫描、分组和 WR 构造；items 是扫描的请求数，Normal 同批请求会被多次扫描 |
| `sdk_post` | 一次链式 SDK 提交及错误检查；items 是此次链中的 WR 数；items/calls 可看平均批次大小 |
| `cq_poll_reclaim` | Proxy 在有未完成请求时轮询 CQ、处理和回收的 CPU 时间；items 是回收的 CQE 数 |
| `empty_cq_poll` | 上述轮询返回 0 的次数 |
| `capacity_wait` | 容量不足时等待、轮询和重试的范围，包含其内部 CQ 回收时间 |
| `quiet_barrier_drain` | quiet/barrier 排空及控制处理，包含容量等待 |
| `gpu_ack_update` | CPU 更新命令确认和 ring tail 的时间，不等于 GPU 观察确认的延迟 |
| `write_requests` | calls 为构造的 WRITE 数，items 为逻辑 WRITE 字节总数 |
| `fadd_requests` | 构造的 FADD 数，包含控制屏障的 FADD |
| `submit_to_observed_cqe_sampled` | 每线程 WRITE、FADD 各自每64个抽样一次，SDK 提交前至 CQE 被软件观察到的时间；另有 write/fadd 两份分类型分布 |

最后一项包含队列等待、SDK 调用、设备执行及软件轮询发现延迟，**不是纯网络或单向链路时延**。抽样分布使用以微秒计的 log2 桶，p95 为桶上界估计。

阶段计时采用本机单调时钟；快照是累积原子计数的差值，边界上可能有少量跨窗口操作。按线程分别保存，汇总时给出跨线程总工作量。`proxy_batch`、group、post、capacity、CQ 等存在嵌套，多个 Proxy 和 GPU/DMA 也重叠：不能将这些时间求和当作完整调用时延，不能直接画成占比加总100%的饼图。

计数记录里的 rank 沿用 transport 的本地上下文索引，不当作 torch 全局 rank；两端用结果目录的 node0/node1 和 host.json 区分，proxy 字段用于区分本机4个线程。

GPU trace 用于观察 kernel 时间线与 send/recv 分段；它不提供 GPU kernel 内部每种自旋条件的精确等待时间。当前没有 GPU 发布命令与远端可见性的逐 WR 时间戳，不跨服务器相减时钟。若本轮将问题缩小到 GPU 内部等待，再添加针对该处的低频埋点。

## 3. 两端准备环境并构建

沿用已经通过 v2_03 的内核、设备和私有 SDK。在仓库根目录的 Bash 终端执行；202 用 RANK=0，204 用 RANK=1：

```bash
cd /home/h00918771/uccl-ep-xpu-integration-new
set -o pipefail
export URMA_DIAG_HOME=/home/h00918771
export URMA_DIAG_PYTHON=/home/h00918771/metax_dev/venv-maca-py312/bin/python
export URMA_DIAG_LIBRARY_DIR=/home/h00918771/metax_dev/urma_after_kernel_20261029/diag/umdk_private_618957c3/lib
export URMA_DIAG_INCLUDE_DIR=/home/h00918771/metax_dev/urma_sdk_private_include
RANK=0  # 204 改成 1
BUILD_DIR=/home/h00918771/metax_dev/urma_mode_parity_build/urma_phase_v1
CONSOLE=/home/h00918771/metax_dev/urma_stage_console/urma_phase_v1
mkdir -p "$CONSOLE"

bash test/urma_mode_parity/run.sh --action build \
  --node-rank "$RANK" --job urma_phase_v1 --build-dir "$BUILD_DIR" \
  2>&1 | tee "$CONSOLE/build_node${RANK}.log"

bash test/urma_mode_parity/run.sh --action check \
  --node-rank "$RANK" --job urma_phase_v1 --build-dir "$BUILD_DIR" \
  --phase-suite \
  2>&1 | tee "$CONSOLE/check_node${RANK}.log"
```

两端分别出现 `BUILD_PASSED` 与 `MODE_PARITY_CHECK_PASSED` 后再开始。`--phase-suite` 检查模块是否含新诊断接口，不能使用旧 v2_03 二进制。两端源码、SDK 和参数仍会握手核对。

## 4. 两端启动完整阶段测试

确保 GPU 空闲，在 202 启动后 180 秒内启动 204。两端都执行：

```bash
bash test/urma_mode_parity/run_stages.sh \
  --node-rank "$RANK" --master-addr 141.61.17.202 \
  --job urma_phase_v1 --build-dir "$BUILD_DIR" \
  --repeats 3 --mode both
```

程序会依次运行：

```text
urma_phase_v1_r01_baseline      Normal -> LL
urma_phase_v1_r01_instrumented  Normal -> LL，保存 GPU trace
urma_phase_v1_r02_baseline      Normal -> LL
urma_phase_v1_r02_instrumented  Normal -> LL
urma_phase_v1_r03_baseline      Normal -> LL
urma_phase_v1_r03_instrumented  Normal -> LL
```

不需要每项手工启动 torchrun，也不需要切内核。baseline 和 instrumented 都重新做正确性检查；任意一项失败立即停止后续作业。每个底层 run 成功输出 `URMA_MODE_PARITY_ALL_PASSED`，整个脚本最终输出 `STAGE_SUITE_ALL_PASSED`。

默认结果：`/home/h00918771/metax_dev/urma_stage_results/urma_phase_v1/<上述job>/node<RANK>/`。终端日志自动保存到前面 CONSOLE 目录。每个 case 仍有 1800 秒上限，可向 wrapper 传 `--case-timeout`。

如果只想先测 Normal，两端同改 `--mode normal`；只测 LL 则同改 `--mode low_latency`。第一轮不建议同时修改 shapes、顺序标志或通道数。

结果目录不覆盖。中途失败后，先提供日志，重跑用新的 `--job urma_phase_v1_retry01`；源码未变可复用 BUILD_DIR。首次只测1轮可以用 `--repeats 1`，但不用于宣称稳定收益。

## 5. 本机汇总

完成后每台各自执行（不需要对端同时执行；不运行 GPU）：

```bash
"$URMA_DIAG_PYTHON" test/urma_mode_parity/summarize_phases.py \
  /home/h00918771/metax_dev/urma_stage_results/urma_phase_v1 \
  --output /home/h00918771/metax_dev/urma_stage_summary/urma_phase_v1
```

生成：

- `runs.csv`：每次独立运行、每测点的 mean/median/p95/CV 等。
- `overhead.csv`：同机同配置 baseline 与 instrumented 各轮均值的中位数及变化；也保留各轮均值的 min/max。
- `stages.csv`：按窗口汇总的 CPU 阶段工作量、平均调用耗时、批次大小及抽样 CQ 延迟。

先查看 baseline 各轮是否稳定，再查看埋点开销。若 instrumented 明显慢于 baseline（例如超过10%），需先降低埋点强度或缩小测量范围，不能直接把有埋点的耗时解释为原程序性能。汇总遇到失败或未完成 case 会拒绝输出，应先分析原错误，不删除 status 绕过检查。

## 6. 需要提供的结果

两端提供：

1. `urma_stage_results/urma_phase_v1/` 中每个 job 对应 node 目录的文本/JSON 文件，保留相对目录结构。
2. 重点文件：`api_samples.jsonl`、`sample_summary.jsonl`、`phases.jsonl`、`metrics.jsonl`、完整 `bench.log`、`status.json`、`runtime.json`、配置和主机信息、内核快照及计数器。
3. `r01_instrumented` 的 `gpu_trace_*.json`。Normal 4份、LL 2份／节点；体积可能较大，可与其余小日志分开提供。
4. `urma_stage_summary/urma_phase_v1/` 中3份 CSV，以及 CONSOLE 中的终端日志。
5. 构建失败时提供该构建目录的 `host_tests.log`、`build.log`、`build_request.json`。

不需要 `.so`、`objects/`、`cache/`、`tmpdir/`、虚拟环境或张量数据。本手册不要求执行任何传输命令。

## 本地验证范围

已增加 CPU 测试覆盖并发计数/快照、禁用埋点、抽样完成统计、累积快照差值、原始样本与丢首样本规则、LL 私有副本变换，以及独立运行汇总与失败拒绝。真实 SDK 编译和 GPU/MCCL/URMA 运行需要在上述服务器完成，本地模拟不替代硬件验收。
