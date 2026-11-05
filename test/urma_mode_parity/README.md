# URMA Normal / Low-latency 实现对齐与验收

最新结果见 **[verbs与URMA阶段复测分析](results/urma_phase_v1/ANALYSIS.zh-CN.md)**；下一轮入口为 **[LL WRITE排序A/B与Normal纯基线](ORDER_AB.zh-CN.md)**：同一新模块对照STRONG/RELAX，保留原子fence，正式计时跳过GPU profiler。仅在已协调到URMA环境时运行。

此前的 **[阶段时延与稳定性测试（phase_v1）](STAGE_TESTS.zh-CN.md)** 保留，用于理解已有GPU trace和Proxy阶段数据。

`v2_02` 已确认主数据段成功、未对齐的 GPU 原子控制段失败。本次修复和下一轮操作见 **[原子控制段对齐修复与 v2_03 复测](ATOMIC_ALIGNMENT_FIX.zh-CN.md)**；probe 默认已改为生产控制段分配路径。

`v2_01` GPU 段注册失败的后续操作见 **[注册失败分析与单机隔离步骤](REGISTRATION_FAILURE.zh-CN.md)**。当前证据不能确定是主数据段还是原子控制段失败，也不能认定闭源库竞态；先用新增日志和探针定位。

服务器按步骤操作请先读 **[双机运行手册（含完整命令与所需日志）](RUNBOOK.zh-CN.md)**，覆盖 URMA 构建验收及同环境 verbs 对照。

本轮目标是修改生产实现，基准为当前 MetaX 上的非 EFA、原生原子 verbs 路径。此前 `urma_diag` 用于诊断旧私有模块，本入口用于**重新构建和验收当前源码**，两者不要混用。

## 已实现的对应关系

| 环节 | Normal | Low-latency |
| --- | --- | --- |
| GPU 算子选择 | 继续使用 `put_nbi_warp<true>` 与对应 Normal kernel | 继续使用默认 `<false>` 与 LL kernel；不把 LL metadata 当成 Normal 原子字段 |
| 地址解码 | 上层按 4B 粒度解包 | 上层按 16B 粒度解包 |
| 数据通道 | 每 Proxy、peer 按 ring 选择 8 个数据 Jetty，另有 1 个控制 Jetty | 每 Proxy、peer 单 Jetty，与 verbs fast-mode 的目标粒度一致 |
| WRITE / ATOMIC | 同 ring 使用同一个数据 Jetty；保留原子值 | 同一 peer 的 WRITE/FADD 使用同一 Jetty；Combine 完成通知值归一为 1 |
| 提交 | 按 peer/ring 构造链表批量提交；支持跨容量分批 | 按 peer 构造链表批量提交 |
| 完成通知 | 每个逻辑 WR 请求 CQE，不擅自减少通知 | 同左 |
| 完成回收 | 固定槽池、代次:index token，O(1) 查找及原子 scratch 分配；一次 progress 可取多批 SDK CQE | 同左 |
| quiet / barrier | quiet 排空全部数据/控制队列；barrier 在排空后走独立控制 Jetty，沿用分布式状态机 | 数据仍通过原生 FADD 通知；不引入 Normal 全局 barrier |
| 调度 | 已知 GPU NUMA 时复用原 pinning 策略；未知时用 CPU pinning fallback；可用原开关关闭 | 同左 |
| 空闲路径 | 没有 verbs CQ/事件通道时，不进入 verbs 休眠通知路径 | 同左 |

Normal 的 WRITE 默认使用已在旧私有模块测试过的 RELAX；LL 保留 STRONG。通知 FADD 保留 STRONG + fence，并与对应 WRITE 保持同队列，不删除同步语义。原始操作与控制消息所需的 scratch/屏障 host 段现使用整页对齐分配，纳入此前私有 `host_alignment.patch` 的必要条件。

`PER_EXPERT_BATCHING` 是两后端共用的 GPU LL Dispatch 优化，仍由同一构建开关控制，默认 0；可用 `--per-expert-batching 1` 另建一份模块验证，不把它误归为 verbs 独有功能。它会改变请求数量和 GPU 拷贝成本，需要单独比较。

不同设备 API 的优化不能按标志位机械复制。EFA/软件原子的 IMM 与接收端 CPU 仿真没有搬进 URMA；URMA 使用硬件 FADD/fence 保证通知顺序。此处对齐的是当前测试目标的操作、分流和批处理策略，不宣称不同设备内部执行机制一致或性能已追平。

## 连接版本与 benchmark

连接描述升级到 **wire v2**，交换模式、通道数和各 Jetty 描述符，并拒绝不一致的对端。两端都必须重新编译，不能复用历史 `ep_native_relax_* / ep_host_atomic_*` 模块。模块新增 `get_urma_mode_version()==2`。

`test_internode.py` 显式验证 Buffer/Proxy 为 Normal；`test_low_latency.py` 验证为 LL。URMA 后端额外要求 v2 扩展；成功输出 `TRANSPORT_MODE_OK`。GPU kernel 和 Python 接口不额外创建一套名字相似但未使用的算子，而是沿实际模式参数进入相应生产路径。

## 当前服务器：两节点、每节点一个 C500

以下脚本构建 `MAX_NUM_GPUS=1 / NUM_MAX_NVL_PEERS=1`，适配当前每节点一个 GPU 的配置；库自身 Normal channel 分流不依赖只用一个 GPU。若要验收其他拓扑，应按实际每节点进程数重建和配置 torchrun，不使用这里的单 GPU 入口。

两端先拉取最新分支，在仓库根目录执行。202 设 `RANK=0`，204 设 `RANK=1`：

```bash
export URMA_DIAG_HOME=/home/h00918771
export URMA_DIAG_PYTHON=/home/h00918771/metax_dev/venv-maca-py312/bin/python
RANK=0  # 204 改成 1
JOB=urma_mode_v2_01
```

保留已经通过的私有库目录设置。额外需要匹配该库、包含 `is_gpu_seg` 的真实 SDK 头文件，以及 MetaX 编译环境的 torch/nanobind/ninja/主机编译器。脚本自动找常见 include 位置；若没有，设置：

```bash
export URMA_DIAG_INCLUDE_DIR=/实际匹配的SDK/include
```

### 1. 各自构建

```bash
bash test/urma_mode_parity/run.sh --action build \
  --node-rank "$RANK" --job "$JOB"
```

先用真实 SDK 运行 CPU 模拟测试，再编译当前源码。产物在 `/home/h00918771/metax_dev/urma_mode_parity_build/$JOB`，不覆盖已安装模块或历史私有模块。构建日志为其中的 `host_tests.log`、`build.log`；完成输出 `BUILD_PASSED`。默认 4 个编译 job，可传 `--build-jobs`。已完成的构建不覆盖；失败后输入未变可重试并追加日志，源码/SDK/编译选项变化则换新 job/build-dir。

构建记录源码、SDK 头文件和选项及本机 ELF SHA。两端独立构建的 ELF 不要求字节一致，但各自运行前必须匹配自己的构建 SHA，双端握手则比较源码、SDK、选项和 torch/内核版本。修改编译源码后需要重新构建；仅更新验收工具不要求重新编译 GPU。

### 2. 各自检查导入与环境

```bash
bash test/urma_mode_parity/run.sh --action check \
  --node-rank "$RANK" --job "$JOB"
```

无需同时启动，不运行 benchmark；预期 `MODE_PARITY_CHECK_PASSED`。仍检查当前 URMA 内核、设备、GPU 原子上游控制、私有库实际映射路径。构建完成本身不证明 GPU/URMA 运行正确。

### 3. 两端在 180 秒内启动

```bash
bash test/urma_mode_parity/run.sh --action run \
  --node-rank "$RANK" --master-addr 141.61.17.202 --job "$JOB"
```

默认先 Normal 后 LL，每端各两个 case。Normal 保留原 24 组正确性检查，固定 SM=24、NVL chunk=4，仅调优 FP8/BF16 Dispatch chunk=32 与 Combine chunk=12/16，并额外记录与此前同方法的完整调用 CUDA-event 时间。LL 运行原正确性矩阵和 benchmark，保留带/不带 recv hook 的测量。默认 Normal tokens=4096、LL tokens=128、hidden=7168、experts=256、topk=8，可显式调整。

本次验收统一关闭 CPU affinity 和 adaptive sleep，以延续已有对照条件；生产默认 pinning 支持仍然保留。MCCL 使用 Socket；不加载旧的诊断 interposer 或 perf。每个 case 前检查设备空闲和已有共享屏障，不自动删除其他作业资源。

验收要求：正确性通过、v2 模式标志正确、Normal 实际初始化为 8 数据+1控制 endpoint、LL 为1 endpoint、profiler 结果完整、双端正常退出确认。最后输出 `URMA_MODE_PARITY_ALL_PASSED`。任一端失败则停止后续 case。第一次通过只能证明这组输入运行正确，不能替代更多形状/长时间稳定性或性能结论。

如当前开机环境也支持 verbs，可复用同一模块再用新结果 job：

```bash
BUILD=/home/h00918771/metax_dev/urma_mode_parity_build/urma_mode_v2_01
bash test/urma_mode_parity/run.sh --action run \
  --node-rank "$RANK" --master-addr 141.61.17.202 \
  --job verbs_mode_v2_01 --build-dir "$BUILD" --backend verbs
```

这样两种后端使用相同源码构建、同一内核和 benchmark，避免拼接旧的不同环境数据。

### 4. 需要提供的日志

提供两端 `metax_dev/urma_mode_parity_results/$JOB/node$RANK` 中的构建清单、环境、两种模式的原始日志、结构化指标、status 与计数器。构建失败时另提供 `build.log / host_tests.log`，检查失败时提供完整终端输出。具体文件清单与失败场景见 [运行手册第 9 节](RUNBOOK.zh-CN.md#9-需要提供哪些日志)。不需要二进制或 GPU 数据。

## 本地验证的范围

主机测试覆盖：Normal/LL 分流、最大 8 数据通道描述交换、WRITE/FADD 配对、内部 WRITE sentinel 的 ring 保留、链表提交、容量分批、乱序完成、代次复用、过期 CQE、部分提交失败、注册/绑定/创建失败的资源回收，以及 benchmark 模式误配/旧模块拒绝。

本地没有 MetaX/URMA 设备，也没有服务器私有 SDK。使用上游参考 SDK 类型时，测试入口的 `--reference-sdk-fixture` 只给 CPU 模拟补一个 `is_gpu_seg` 占位字段；它不代表私有 ABI 一致，**生产构建入口不允许这种替代**。实际私有 SDK 编译和 GPU 正确性/性能由上述服务器流程完成。
