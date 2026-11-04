# v2_01 注册失败：证据、改动与下一步操作

## 已确认与尚未确认

两端 `bench.log` 均显示 `URMA MetaX P2P segment registration failed`，随后 Proxy 线程异常导致进程 abort。失败发生在注册阶段，还没有进入 dispatch/combine 正确性或性能测量；因此这轮不能产生性能结论。

上传报告中有几处判断需要收窄：

- 同一条异常同时用于主数据段和 GPU 原子控制段，原日志不能区分究竟哪一段失败。
- `register_segment()` 已用 `g_resource_mutex` 串行化注册，并缓存同 context、地址、长度和权限的注册；不能根据失败线程号不同断定“四线程同时注册导致竞态”。
- `pin OK` 只能证明相应 pin 回调成功，不能排除后续内核路径失败；SDK 头文件有同名字段、两端 SHA 一致，也不能独立证明与私有库 ABI 匹配。
- `mode=... wire=2` 是本地 init 日志，不代表对端已完成连接描述交换或 GPU 段注册。

现有数据不足以确定应修改哪一个生产参数。当前提交修复诊断信息缺失，并提供单机隔离入口；不擅自换 host fallback、反复重试注册或缩减生产 Normal 通道数来掩盖错误。真实设备上的故障尚待复测定位。

## 本次增加的能力

每次实际注册输出 `[URMA_REG] stage=begin/success/failed`，共享注册输出 `cache_hit`。记录地址、长度、GPU/host、atomic 权限、线程、SDK 结构大小和 GPU 标志字段偏移。失败时先保存 errno，再清理 token；errno 为 0 时明确注明 provider 未设置，不能当作成功。

新增 `--action probe`：在单机、单进程中调用生产 URMA transport，分配主数据 GPU 段及逻辑长度 81960B 的 GPU 控制段，保留 1 或 4 个 transport，验证注册/缓存/资源回收。它不启动 MCCL、不需要对端、不提交网络 WR，不能替代双机正确性验收。默认主数据长度 117506432B 来自上传报告中的后续重试，不保证是首次失败的精确长度；可用 `--probe-bytes` 指定实际长度。

probe 以顺序方式创建 transport，检查保留资源量的影响，不模拟所有 Proxy 初始化并发；probe 通过而 benchmark 失败时仍需检查实际地址、长度、分配来源和初始化时序。

probe 和后续 run 自动保存最近 400 行内核日志到 `kernel_before.txt / kernel_after.txt`。读取失败只保存错误，不覆盖原故障。

## 1. 两端重新构建

先拉取本次代码。沿用 [运行手册](RUNBOOK.zh-CN.md) 中已经检查通过的 Python、私有库和真实 SDK 环境。在仓库根目录、同一 Bash 终端设置：

```bash
cd /home/h00918771/uccl-ep-xpu-integration-new
set -o pipefail
export URMA_DIAG_HOME=/home/h00918771
export URMA_DIAG_PYTHON=/home/h00918771/metax_dev/venv-maca-py312/bin/python
export URMA_DIAG_LIBRARY_DIR=/home/h00918771/metax_dev/urma_after_kernel_20261029/diag/umdk_private_618957c3/lib
export URMA_DIAG_INCLUDE_DIR=/home/h00918771/metax_dev/urma_sdk_private_include
RANK=0  # 202 用 0；204 改为 1
BUILD_DIR=/home/h00918771/metax_dev/urma_mode_parity_build/urma_mode_v2_02
LOG_DIR=/home/h00918771/metax_dev/urma_mode_parity_console/urma_mode_v2_02
mkdir -p "$LOG_DIR"

bash test/urma_mode_parity/run.sh --action build \
  --node-rank "$RANK" --job urma_mode_v2_02 --build-dir "$BUILD_DIR" \
  2>&1 | tee "$LOG_DIR/build_node${RANK}.log"
```

预期 `BUILD_PASSED`。私有 SDK 路径取自上传报告，如果服务器实际路径不同，应选择实际匹配的 SDK。旧模块没有探针入口，不能跳过重新构建。

## 2. 两端各自运行四个单机探针

两台可独立执行，不需要在 180 秒内配对。每台内部依次执行，等待前一项退出后再执行下一项；不要并行。每个结果 job 是新目录，已存在时改新编号。

```bash
# A：LL 单通道，1 个 transport。
bash test/urma_mode_parity/run.sh --action probe \
  --node-rank "$RANK" --job reg02_ll1 --build-dir "$BUILD_DIR" \
  --mode low_latency --probe-transports 1 \
  2>&1 | tee "$LOG_DIR/probe_ll1_node${RANK}.log"

# B：Normal 8 数据+1控制通道，1 个 transport。
bash test/urma_mode_parity/run.sh --action probe \
  --node-rank "$RANK" --job reg02_n8_1 --build-dir "$BUILD_DIR" \
  --mode normal --probe-channels 8 --probe-transports 1 \
  2>&1 | tee "$LOG_DIR/probe_n8_1_node${RANK}.log"

# C：Normal 1 数据+1控制通道，保留 4 个 transport（仅隔离资源量）。
bash test/urma_mode_parity/run.sh --action probe \
  --node-rank "$RANK" --job reg02_n1_4 --build-dir "$BUILD_DIR" \
  --mode normal --probe-channels 1 --probe-transports 4 \
  2>&1 | tee "$LOG_DIR/probe_n1_4_node${RANK}.log"

# D：Normal 8 数据+1控制通道，保留 4 个 transport。
bash test/urma_mode_parity/run.sh --action probe \
  --node-rank "$RANK" --job reg02_n8_4 --build-dir "$BUILD_DIR" \
  --mode normal --probe-channels 8 --probe-transports 4 \
  2>&1 | tee "$LOG_DIR/probe_n8_4_node${RANK}.log"
```

每项成功输出 `REGISTRATION_PROBE_PASSED`。失败看对应结果中的 `probe.log`，尤其是首条 `stage=failed` 及其前面的 `stage=begin`：

- `kind=gpu atomic=0`：主数据段。
- `kind=gpu atomic=1`：原子控制段。
- `kind=host`：本地原子结果或屏障缓冲区。
- 只有多 transport/多通道失败：资源量相关性上升，仍需结合 errno 与内核日志判断，不能直接判定资源耗尽。

如果硬件检查或内核队列健康检查失败，先保留日志处理该问题，不跳过检查继续。某项注册失败后可检查其日志和设备状态再决定是否继续后续探针。

## 3. 探针通过后，双机只跑 Normal

两端均通过 D 项且设备空闲后，先 202、再 204，在 180 秒内分别执行：

```bash
bash test/urma_mode_parity/run.sh --action run \
  --node-rank "$RANK" --master-addr 141.61.17.202 \
  --job urma_normal_v2_02 --build-dir "$BUILD_DIR" \
  --backend urma --mode normal \
  2>&1 | tee "$LOG_DIR/normal_run_node${RANK}.log"
```

此处生产 Normal 仍使用 8 条数据通道。若失败，提供两端新增 `[URMA_REG]`、内核快照和下面的日志清单，不必再跑完整性能对照。若成功，再按运行手册使用新的 job 单独 `--mode low_latency` 验收 LL，之后做 verbs 对照。

## 需要提供的文件

两端分别提供实际生成的文件，不需要传输步骤：

- `metax_dev/urma_mode_parity_results/reg02_ll1/node<RANK>/`
- `metax_dev/urma_mode_parity_results/reg02_n8_1/node<RANK>/`
- `metax_dev/urma_mode_parity_results/reg02_n1_4/node<RANK>/`
- `metax_dev/urma_mode_parity_results/reg02_n8_4/node<RANK>/`
- 若执行双机 Normal：`metax_dev/urma_mode_parity_results/urma_normal_v2_02/node<RANK>/`
- 本次 `metax_dev/urma_mode_parity_console/urma_mode_v2_02/` 中的终端日志。
- 本次构建目录的 `manifest.json`、`host_tests.log`、`build.log`（若构建失败，另含 `build_request.json`）。

结果中保留 `probe.log / bench.log`、`preflight.json`、`build_manifest.json`、`status.json`、`kernel_before.txt / kernel_after.txt`、主机信息，以及失败前已生成的其他文本/JSON。不要只截最后一条错误。不需要 `.so`、`objects/`、`cache/`、`tmpdir/` 或虚拟环境。
