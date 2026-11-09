# RDMA / verbs 固定配置基线与 GPU 阶段分析

当前verbs窗口请先按 **[Normal三组测试一体运行文档](NORMAL_CAMPAIGN.zh-CN.md)** 操作：新构建纯verbs模块，统一运行完整Normal、源码Proxy诊断和WRITE方向/并发对照；不测LL。本页保留历史基线流程供查阅。

本轮用于当前可用的 **RDMA 内核环境**。不切换 URMA 内核，不要求 URMA 设备或私有 SDK。建议先完成必测的历史 verbs 基线；时间允许时，再测当前源码构建的纯 verbs 后端。两类结果分别标识，不覆盖既有数据。

## 1. 本轮测试内容和次数

| 模式 | 固定输入 | 测点 |
| --- | --- | --- |
| Normal / `test_internode.py` | 每 rank 4096 tokens，hidden=7168，experts=256，top-k=8，SM=24，NVL chunk=4 | FP8 Dispatch chunk=32；BF16 Dispatch chunk=32；Combine chunk=12、16；layout 另列 |
| LL / `test_low_latency.py` | 每 rank 128 tokens，hidden=7168，experts=256，top-k=8 | 无 hook 的完整 Dispatch+Combine；诊断时分别观察 Dispatch、Combine 及立即调用 receive hook 的 send/recv 分段 |

默认两端各运行以下 **8 个双机 case**，按同一顺序配对：

1. 第1轮 Normal、LL 基线。
2. 第2轮 Normal、LL 基线。
3. 第3轮 Normal、LL 基线。
4. Normal、LL 各一次独立 GPU profiler。

每个完整调用测点预热50次，计时50次；保存全部原始数据，首个计时样本标记 `used=false`，剩余49次计算均值、中位数、P95、标准差、CV、min/max。三轮程序分别启动；最终对各轮均值再取中位数，并保留轮间范围和CV。两端是一次通信的参与者，不合并成6次独立重复。

**基线不启动 Kineto，也不执行 LL 的 hook profiler 工作负载。** GPU profiler 单独运行，结果不能混入基线均值。诊断 case 也保留完整调用采样，但汇总程序将其排除在基线之外。

LL profiler 的 hook 工作负载仅立即调用 hook，不插入原 benchmark 的8192×8192 GEMM；它是诊断测点，不能直接与历史带 GEMM 的 hook 分段做加速比。profiler 原有的 period 前平衡工作仍保留。GPU kernel 的持续时间包含 kernel 内等待，并非纯网络耗时；不同阶段可能重叠，不能直接相加成总时延。这一版不包含 verbs 内部 post/CQ/write/atomic 的 CPU 插桩。

## 2. 模块和 benchmark 来源

默认 `--variant original` 使用此前 `verbs_diag_home_01` 已验证的历史私有模块：

```text
/home/h00918771/metax_dev/urma_after_kernel_20261029/diag/ep_host_atomic_4ed19a6b/module/ep.abi3.so
SHA256: 144f1fca126eb8971a5278c3baf0b44e1a29fb7b03ce51c928feb7abc19af798
```

只接受上述 SHA 的二进制；同一模块可以通过 `--module` 指定其他路径。脚本从仓库提交 `41443b64f4fa1a689fb974f205f756f311efd4c3` 导出兼容的 `test_internode.py`、`test_low_latency.py`、`buffer.py` 和 `utils.py` 到结果目录，不修改工作区文件。Normal benchmark、Buffer 和 utils 的原始哈希与之前脚本的固定哈希核对；保存所有输入和生成文件的哈希。

历史模块的完整构建源码和编译记录目前不齐，因此 `module_manifest.json` 明确将其源码 revision 标记为未知；**benchmark 的提交号不代表这个历史二进制的构建提交号**。它是可复现的历史 verbs 二进制基线。

固定使用 verbs、DMA-BUF、关闭 Proxy 绑核、关闭 adaptive sleep。历史模块保留之前 verbs 基线使用的 `UCCL_METAX_URMA_HOST_ATOMICS=1`；这个兼容开关和可能链接的 `liburma` 不等于选择 URMA 传输。实际后端强制为 verbs，检查 Proxy 模式、DMA-BUF 注册日志；可选的新构建则使用 `USE_URMA=0`，加载时若发现 liburma 会拒绝。

脚本不固定 GID=1。沿用 EP 实际设备选择逻辑，保留实际初始化日志、`ibv_devinfo.txt`、GID/类型/netdev表。先前 EP 日志为自动选择 GID=3；不要把独立 perftest 的 GID=1、单QP配置当成本次 EP 配置。

## 3. 两端准备

两端都在 Bash 中进入已更新的仓库根目录，确认当前是协调好的 RDMA 内核。本次不使用 URMA 的 guard、设备检查或内核切换命令。

202：master，`141.61.17.202`，`RANK=0`。

204：worker1，`141.61.17.204`，`RANK=1`。

两端分别执行以下准备命令；只修改 `RANK`：

```bash
cd /home/h00918771/uccl-ep-xpu-integration-new
set -o pipefail
export VERBS_DIAG_HOME=/home/h00918771
export VERBS_DIAG_PYTHON=/home/h00918771/metax_dev/venv-maca-py312/bin/python
export MACA_PATH=/opt/maca-3.7.0

RANK=0  # 204 上必须改为 1
JOB=verbs_baseline_01
RESULTS=/home/h00918771/metax_dev/verbs_baseline_results
MODULE=/home/h00918771/metax_dev/urma_after_kernel_20261029/diag/ep_host_atomic_4ed19a6b/module/ep.abi3.so
CONSOLE=/home/h00918771/metax_dev/verbs_baseline_console
mkdir -p "$CONSOLE"
```

运行原版基线不需要重新编译。如果模块在其他位置，只修改 `MODULE`，其 SHA 必须一致。不要使用 URMA v2 的 `.so` 替代它。

先在每端检查，检查无需另一端同时执行：

```bash
bash test/verbs_baseline/run.sh --action check \
  --variant original --module "$MODULE" \
  --node-rank "$RANK" --master-addr 141.61.17.202 \
  --job "$JOB" --output "$RESULTS" \
  2>&1 | tee "$CONSOLE/${JOB}_check_node${RANK}.log"
```

两端均出现 `VERBS_CHECK_PASSED` 后开始正式测试。检查包含：模块 SHA、Python/Torch/扩展导入、兼容 benchmark 导出、所选 HCA 是否存在、GPU和设备是否空闲、是否存在未处理的共享 barrier。检查通过不证明全部运行时 ABI 已兼容，最终以双机正确性和退出状态为准。

如果浅克隆缺少上述历史提交，先在仓库根目录补取历史对象，再重新检查：

```bash
git fetch https://github.com/LUOQIHuang/uccl.git 41443b64f4fa1a689fb974f205f756f311efd4c3
```

如报 Torch 缺失，检查 `VERBS_DIAG_PYTHON`；如动态库缺失，可按之前环境设置 `VERBS_DIAG_EXTRA_LIBRARY_DIRS` 为实际所需目录。原始历史二进制可能仍链接 liburma，需要满足它的动态链接依赖，但不需要 URMA 硬件可用，也不做私有库 SHA 强制检查。

## 4. 必测：历史 verbs 基线和一次 profiler

先在202执行下面命令，随后在204执行相同命令，确保各自 `RANK` 正确；两端启动间隔控制在180秒以内。两端 `JOB`、参数和代码必须一致：

```bash
bash test/verbs_baseline/run.sh --action run \
  --variant original --module "$MODULE" \
  --node-rank "$RANK" --master-addr 141.61.17.202 \
  --job "$JOB" --output "$RESULTS" \
  --repeats 3 --warmups 50 --samples 50 \
  2>&1 | tee "$CONSOLE/${JOB}_run_node${RANK}.log"
```

默认已包含 Normal、LL 和最后单独的一轮 profiler，无需再执行旧 `verbs_diag` 或 URMA `run_stages.sh`。每个 case 开始会打印完整 `bench.log` 路径；正常结束依次出现 `VERBS_CASE_END ... rc=0`，最后两端均出现 `VERBS_BASELINE_ALL_PASSED`。

默认控制端口31380，torchrun端口31381；需要换端口时两端同时添加相同的 `--port`。单case超时默认1800秒，可按需添加 `--case-timeout`，不提前承诺整个测试所需时间。

输出结构如下：

```text
$RESULTS/$JOB/node0/                 # 204 为 node1
  setup.json                        # 参数、源码/脚本指纹、环境匹配信息
  module_manifest.json              # 历史模块 SHA 或当前源码构建记录
  runtime.json, host.json, rdma_port.json, ibv_devinfo.txt, lscpu.txt
  benchmark/                        # 此次实际运行的冻结 Python 文件
  r01_baseline/{normal,low_latency}/
  r02_baseline/{normal,low_latency}/
  r03_baseline/{normal,low_latency}/
  gpu_profile/{normal,low_latency}/
```

每个case包含 `bench.log`、`status.json`、`config.json`、`runtime.json`、`api_samples.jsonl`、`sample_summary.jsonl`、测试前后计数器和内核日志。profiler case另含 `gpu_stages.jsonl`、Normal四份/LL两份 `gpu_trace_*.json`。

**正式基线以 `sample_summary.jsonl` / `api_samples.jsonl` 为准。** 为使原 benchmark 的最优配置选择继续执行，基线中 Normal `bench_kineto` 回调返回完整调用耗时和一个极小非零占位 notify 值。因此基线 `bench.log` 中原有的 `[tuning] transmit/notify` 字段不是 kernel 分段；它们不可用于阶段分析。真正 GPU 分段只看 `gpu_profile/.../gpu_stages.jsonl` 和 trace。

发生任何case失败，脚本停止，不继续后面的测试。先保留失败目录、两端console和 `status.json`，不要删除其他同事的共享内存或强行终止其进程。同一个 `JOB/nodeRANK` 禁止覆盖；重跑使用新JOB。如果两端指纹不同，在 `setup.json` 中检查具体参数、代码、内核、Torch和所选动态库哈希。

资源临时收紧时可以两端一起添加 `--no-profile`，先完成三轮基线；之后使用**新JOB**和 `--profile-only` 单独补采GPU诊断。仅测一个模式可两端添加 `--mode normal` 或 `--mode low_latency`。以上参数必须两端一致。

## 5. 可选：当前源码的纯 verbs 后端

这一步用于检查当前公共代码和当前构建下的 verbs 表现，优先级低于上面的历史基线。它不是 URMA 测试，仍在当前RDMA内核完成。两端从同一份源码构建，使用独立目录，不修改已安装模块：

```bash
CURRENT_BUILD=/home/h00918771/metax_dev/verbs_baseline_build/current_v1

bash test/verbs_baseline/run.sh --action build --variant current \
  --build-dir "$CURRENT_BUILD" --build-jobs 4 \
  --node-rank "$RANK" --job verbs_current_build \
  2>&1 | tee "$CONSOLE/verbs_current_build_node${RANK}.log"
```

构建参数固定为 `USE_METAX=1 USE_URMA=0 USE_DMABUF=1 MAX_NUM_GPUS=1 NUM_MAX_NVL_PEERS=1 PER_EXPERT_BATCHING=0`，并关闭CXI及旧 `USE_URMA_METAX`。无需私有URMA头文件。需要目标机器已有的编译工具、MetaX Torch、nanobind和verbs开发依赖；构建日志保存在 `$CURRENT_BUILD/build.log`。构建成功输出 `VERBS_BUILD_PASSED`；相同构建输入可复用，源码变化后使用新构建目录。

然后两端分别检查：

```bash
bash test/verbs_baseline/run.sh --action check --variant current \
  --build-dir "$CURRENT_BUILD" \
  --node-rank "$RANK" --job verbs_current_check
```

两端检查通过后，执行当前verbs版本的三轮纯基线。本轮可以省去第二套profiler，减少占用：

```bash
CURRENT_JOB=verbs_current_01
bash test/verbs_baseline/run.sh --action run --variant current \
  --build-dir "$CURRENT_BUILD" \
  --node-rank "$RANK" --master-addr 141.61.17.202 \
  --job "$CURRENT_JOB" --output "$RESULTS" \
  --repeats 3 --warmups 50 --samples 50 --no-profile \
  2>&1 | tee "$CONSOLE/${CURRENT_JOB}_run_node${RANK}.log"
```

当前版本使用冻结的当前benchmark文件，历史版本使用兼容历史文件。两组分别保存输入哈希和模块来源，比较的是两套明确记录的verbs实现；由于历史完整构建信息不齐，不能仅凭这组差异断定某一处公共代码改动的因果关系。

## 6. 整理结果，以及需要提供哪些文件

每端执行以下命令，将选定的结果整理到仓库下；这只是本地筛选复制，不执行网络传输：

```bash
bash test/verbs_baseline/run.sh --action collect \
  --node-rank "$RANK" --job "$JOB" --output "$RESULTS"

"$VERBS_DIAG_PYTHON" test/verbs_baseline/summarize.py \
  "test/verbs_baseline/results/$JOB" \
  --output "test/verbs_baseline/results/$JOB/summary_node${RANK}"
```

如果做了当前版本，另行整理：

```bash
bash test/verbs_baseline/run.sh --action collect \
  --node-rank "$RANK" --job "$CURRENT_JOB" --output "$RESULTS"
```

需要提供：

- `test/verbs_baseline/results/$JOB/node0/` 和 `node1/` 的全部已筛选文件，尤其是逐次采样、汇总、GPU trace、bench/status/config、两端运行环境、源码和模块来源、计数器与内核日志。
- 生成的 `summary_node*/`（如已生成）。两端结果集中后可以对完整JOB重新汇总到一个新目录。
- 两端 `$CONSOLE/${JOB}_check_nodeRANK.log`、`${JOB}_run_nodeRANK.log`。可放到结果JOB下的 `console/` 一起提供。
- 可选当前版本的对应结果，以及 `$CURRENT_BUILD/build.log`、`manifest.json` 和两端构建console。
- 如果失败：已产生的失败结果和两端console全部保留，不需要等全部成功才提供。

不需要上传 `.so`、编译objects、venv、临时cache/tmpdir。collect自动排除这些内容。结果目录的JSON/JSONL/CSV/log已配置为允许Git跟踪。

汇总程序拒绝失败或缺失的case，并校验采样完整性；输出 `runs.csv`（逐case分布）、`baseline_summary.csv`（三轮基线聚合）和 `gpu_stages.csv`（单独GPU诊断）。原始P95是单轮49次采样的分位数；三轮聚合使用各轮均值中位数，不把两端或profiler混入。

本地已做CPU模拟、来源校验和脚本检查；开发设备不能运行MetaX GPU benchmark，真实ABI兼容性、正确性和性能仍需以上双机测试确认。
