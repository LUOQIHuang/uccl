# verbs/RDMA 与 URMA 初始基线包

目标系统：Linux，x86_64 或 AArch64，Python 3.9+、C 编译器，以及各后端的开发头文件、库、驱动和可用网络设备。微基准不依赖 GPU/PyTorch。无需 root；无法读取的 dmesg、缺少的诊断命令记录为诊断缺失，不掩盖测试失败。

## 测试分层和公平性

| 层 | 运行内容 | 回答的问题 |
|---|---|---|
| micro | 两个独立进程，host memory，原始 verbs/URMA API | WRITE/FADD 提交与完成、吞吐、屏障式原子同步、混合操作排空的成本 |
| ep | 真实生产 GPU Dispatch/Combine + FIFO + CPU Proxy | 两个后端对实际 EP 正确性及端到端延迟/带宽的影响 |

micro 默认同机双进程，仅作为 loopback 初筛；必须使用两机 server/client 才能评估跨机网络。WRITE/FADD latency 测的是本地 post 到本地完成的时间，不是 ping-pong RTT，也不是远端 GPU 可见延迟。micro 的 BARRIER/QUIET 是操作级模型，不是生产 Proxy 的完整状态机。

EP 默认编译同一份 URMA 分支代码（`USE_URMA=1`），运行时切换 `UCCL_EP_TRANSPORT=verbs/urma`。这样避免把上游两份仓库的不同优化混入后端对比。若另用上游仓库做比较，需单独标记版本与构建，不能称为只改变后端的实验。

保持两端 SDK/ABI、编译 flags、CPU 亲和性、GPU/NIC 拓扑、MTU、消息大小、窗口和操作数一致。两种 NIC 若不是相同物理路径，应把结果解释为平台组合对比。脚本在重复轮次交替后端顺序。不要并发跑两种后端，不要边跑边改频率或网络设置。

## 一键编译并运行：单机初筛

把本目录复制到目标设备；micro 所需源码全部在本目录。先指定实际设备（以下名称仅示例）：

```bash
export IBV_DEV_NAME=rocep40s0f0
export IBV_GID_IDX=2
export URMA_DEV_NAME=udmac0d1e2
# 非系统 SDK 按实际路径配置；动态库也必须可加载。
export URMA_INCLUDE_DIR=/opt/umdk/include
export URMA_LIBRARY_DIR=/opt/umdk/lib64
bash run.sh --sizes 8 --windows 1 --repeats 1 --output quick-check
```

完整初始矩阵：

```bash
bash run.sh --output baseline-local
```

默认 sizes=8/4096/32768，windows=1/32，各后端三轮，每个 WRITE bandwidth case 8192 次；延迟每种大小预热100次、测量1000次；FADD throughput 4096次。每个进程默认180秒超时。窗口限制为1..32，单次 WRITE size <=32768，避免超出固定分配范围。扩充规模应先修改并验证缓冲区布局。

## 两机网络基线

两台机器复制相同目录，配置各自的设备变量，使用相同 Python 参数。先在 A 启动 server，再在 B 启动 client：

```bash
# A：替换路径后，在本目录运行
bash run.sh --mode server --output baseline-A
# B：把 192.0.2.10 替换为 A 可达的 IPv4
bash run.sh --mode client --peer 192.0.2.10 --output baseline-B
```

默认监听全部 IPv4 地址，使用 18717 起的连续 TCP bootstrap 端口（默认36个）；数据走实际 NIC。需要这些端口可达。server/client 必须具有相同后端顺序、重复数、sizes/windows、port、iters。client 连接最多重试60秒，外层仍有进程超时。双端各自保存报告；server-only PASS 为暂定，最终以双方日志和 client 校验为准。换方向再跑一次，交换 server/client。

只测某后端：`--backends verbs` 或 `--backends urma`。SDK 缺失会记录编译错误，继续收集另一后端结果，但总状态为 FAIL。两机某端编译失败时应停止两端、修复后重新跑，不要拿错位的 case 做比较。

## 两机真实 EP 基线

需要完整仓库（含实际依赖源码/已初始化的 submodule）、匹配厂商的 PyTorch、Ninja、nanobind、numpy、verbs/libnl/NUMA 开发库、GPU runtime 和支持 GPU P2P 的 URMA SDK/驱动。MetaX 按仓库 `ep/README.md` 先激活 MACA/cu-bridge，设置 `USE_METAX=1`、`CUCC_TARGETS`；verbs DMA-BUF 需要 `USE_DMABUF=1`、`UCCL_EP_VERBS_REG=dmabuf`。NVIDIA 按设备设置 `TORCH_CUDA_ARCH_LIST`。脚本不会安装软件或伪造 nvcc。

两端同一完整 checkout、同一套环境。每机一 GPU 的起步命令：

```bash
# A（替换 repo 和 master IP）
bash run.sh --suite ep --repo /data/uccl-ep-urma --nnodes 2 --node-rank 0 \
  --master-addr 192.0.2.10 --gpus 1 --repeats 1 --timeout 900 --output ep-A
# B
bash run.sh --suite ep --repo /data/uccl-ep-urma --nnodes 2 --node-rank 1 \
  --master-addr 192.0.2.10 --gpus 1 --repeats 1 --timeout 900 --output ep-B
```

脚本执行 `setup.py build_ext --inplace`（默认最长1800秒），把生成的 extension 放进报告目录内私有 uccl 包，验证 import 路径，然后按相同顺序运行两后端的 `test_internode.py` 和 `test_low_latency.py`。不安装或覆盖当前 Python 环境的 uccl 包；构建会写指定 checkout 的 ep/build 和扩展文件，请每设备用独立 checkout。

默认 Normal tokens=4096、LL tokens=128、hidden=7168、topk=8、experts=32。可用 `--tokens --ll-tokens --hidden --topk --experts --gpus` 修改；experts 必须被 world size 整除，具体 kernel 还有额外限制，以错误报告为准。初筛后再扩至每节点8GPU和3轮。EP 需要网络启动、NCCL/MCCL 等 bootstrap 正常；单节点运行不能验证跨机 URMA，故脚本要求至少两节点。

EP 某端失败后不会继续下一个 case，以免各节点进入不同测试；另一端最多等待 --timeout。收集两端报告后整体重跑。测试含数值断言；LL 源程序部分 nondeterminism 只打印不 assert，runner 将其视为失败。本方案尚未提供真实 Proxy 内部各阶段计时，请勿将 micro 时间减去 EP 时间当作 Proxy 开销。

## 报告与调试

每次输出目录必须不存在，防止覆盖旧证据。结束（包括编译失败、运行异常、超时和 Ctrl-C）生成：

- `REPORT.md`：总状态、失败命令、退出码、校验错误、超时；
- `results.json`：每阶段命令、耗时、case 参数和结果；
- `metrics.csv`：micro 指标原始行、重复轮次、size/window、有效标记；
- `config.json`：运行参数和通信/构建相关白名单环境，不导出完整环境；
- `sources.json`：测试源码 SHA256；
- `build-*.log`、`*-client.log`、`*-server.log`、`ep-*.log`：完整 stdout/stderr；
- `env-*.log`：系统、CPU/NUMA、网络、NIC、GPU、工具链、驱动输出、git revision；
- 同级 `输出目录.tar.gz`：上述全部文件和本次构建的二进制/EP扩展。

把两端 tar.gz 带回即可定位：头文件/链接问题、设备选择/权限问题、TCP连接问题、CE/WC失败、数值校验错误或卡住超时。不声称自动生成完整 core dump/线程栈；dmesg 可能因权限不可读。SIGKILL、断电、磁盘写满等外层无法处理的情况可能来不及生成报告。诊断包含机器/网络地址，分享前按实际需要检查。

FAIL 判定同时检查进程退出码、错误关键词、WRITE 数据校验、FADD old-value 顺序/总和校验和 ALL_OK；不能只看旧程序最后的 ALL_OK。仅 `valid=True` 的 micro 行可用于对比，建议对匹配 case 计算三轮 median，并分别比较 p50/p99。EP 保留完整基准输出，不假设不同版本日志格式相同。

## 测试源码来源与改动

`verbs_bench.c` / `urma_bench.c` 基于本仓库 `urma_validation/bench` 的同名程序复制，保留原 API 操作路径。新增：可配 peer/port、客户端重试、监听就绪标记、bootstrap exact IO/长度校验、URMA 两种头文件布局、统一 bandwidth iterations。修复 barrier 发完本地操作后仍调用阻塞 poll_n(1) 的进度问题，改为非阻塞 poll_once；修复一个格式参数类型。保留现有非阻塞 control loop，外层 watchdog 负责长时间停顿。

现有微基准并非长期稳定吞吐压测：WRITE throughput 可能较短；`--iters 262144` 可延长。FADD吞吐固定4096次，需进一步增加源码常量才能研究稳态。旧 server RESULT 的 expect 是固定汇总公式，不能当作所有配置下的总数断言；以独立验证阶段为准。

本地验证命令：`python3 -m unittest -v test_runner.py`、`bash -n run.sh`、实际 SDK 编译和硬件运行。无硬件测试不能证明网络或 GPU 路径正确。
