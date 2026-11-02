# 快速上手：对比 verbs/RDMA 与 URMA

推荐顺序：**单机检查 → 两机微基准 → 两机真实 EP 测试**。下面的脚本会自动编译程序、运行测试并打包日志，无需单独启动测试二进制。

## 1. 下载代码

在 Linux 服务器上执行；两机测试时，两台机器都执行：

```bash
git clone --branch codex/urma-proxy-baseline \
  https://github.com/LUOQIHuang/uccl.git uccl-urma
cd uccl-urma/urma_validation/baseline
```

后续所有命令都在 `urma_validation/baseline` 目录执行。已经下载本分支的用户，可在该目录执行 `git pull --ff-only` 更新。

## 2. 配置服务器环境

微基准需要 Python 3.9+、C 编译器、libibverbs 开发库，以及 URMA SDK、驱动和可用网络设备，**不需要 GPU 或 PyTorch**。

先查看实际设备：

```bash
ibv_devinfo
urma_admin show
```

配置设备和 SDK。**以下名称、GID 和路径均为示例，必须按实际环境修改；两台机器分别使用各自的设备配置。**

```bash
export IBV_DEV_NAME=rocep40s0f0
export IBV_GID_IDX=2
export URMA_DEV_NAME=udmac0d1e2
export URMA_INCLUDE_DIR=/opt/umdk/include
export URMA_LIBRARY_DIR=/opt/umdk/lib64
export LD_LIBRARY_PATH="$URMA_LIBRARY_DIR:${LD_LIBRARY_PATH:-}"
```

`URMA_INCLUDE_DIR` 应指向包含 `urma_api.h` 的目录；系统默认头文件路径也支持 `ub/umdk/urma/urma_api.h` 布局。SDK 的头文件与动态库需要匹配。

## 3. 先做单机检查

```bash
bash run.sh --sizes 8 --windows 1 --repeats 1 --output quick-check
cat quick-check/REPORT.md
```

脚本会编译两个后端，并自动启动本机双进程测试。看到 case 的 `PASS` 且报告总状态为 `PASS` 后，再扩大规模：

```bash
bash run.sh --output baseline-local
```

默认比较 verbs/URMA，WRITE 吞吐的消息大小为 8 B、4 KB、32 KB，窗口为 1、32，各三轮；也检查 WRITE/FADD 延迟、原子旧值、数据正确性、BARRIER 和 QUIET。

单机结果是 loopback 初筛。微基准使用主机内存；WRITE/FADD 延迟是本地提交到完成的时间，不代表网络 RTT、远端 GPU 可见延迟或完整 CPU Proxy 的耗时。

## 4. 两机微基准

先在机器 A 启动 server：

```bash
bash run.sh --mode server --sizes 8 --windows 1 --repeats 1 --output micro-A
```

再在机器 B 启动 client。将 `192.0.2.10` 替换为机器 A 可达的 IPv4 地址：

```bash
bash run.sh --mode client --peer 192.0.2.10 \
  --sizes 8 --windows 1 --repeats 1 --output micro-B
```

这组最小配置使用 TCP 18717、18718 做启动连接；完整默认矩阵使用 18717～18752。两机需要这些端口可达，数据传输走各自选择的网络设备。

两端的后端顺序、消息大小、窗口、重复数、操作数和端口参数必须相同。server 的 PASS 需结合 client 的校验结果判断。某端编译失败时先停止两端，修复后整体重跑。

最小配置通过后，两端都去掉 `--sizes 8 --windows 1 --repeats 1`，换新输出目录，运行完整矩阵。建议交换 A/B 角色再测一次。

## 5. 两机真实 EP / CPU Proxy 测试

这一层运行生产 Dispatch/Combine kernel、FIFO 和 CPU Proxy，并在同一份 EP 扩展上切换 verbs/URMA。**两机都需要 GPU**，以及匹配厂商的 PyTorch、GPU 工具链、Ninja、nanobind、numpy、verbs/libnl/NUMA 开发库和支持 GPU P2P 的 URMA SDK/驱动。

先按 [EP 构建说明](../../ep/README.md) 准备 CUDA 或 MetaX 环境。MetaX 必须使用兼容 cucc 的厂商 PyTorch，按实际 SDK 设置 `USE_METAX=1`、`CUCC_TARGETS` 等；完整 MetaX/URMA 路径仍待目标设备验证。若 verbs 使用 DMA-BUF，还需设置 `USE_DMABUF=1`、`UCCL_EP_VERBS_REG=dmabuf`。

两机各自补充下面的配置，`eth0` 替换为启动通信所用网卡：

```bash
export UCCL_URMA_DEV_NAME="$URMA_DEV_NAME"
export UCCL_IB_GID_INDEX="$IBV_GID_IDX"
export UCCL_SOCKET_IFNAME=eth0
export NCCL_SOCKET_IFNAME=eth0
```

在机器 A 执行，master 地址替换为 A 的实际 IPv4：

```bash
bash run.sh --suite ep --nnodes 2 --node-rank 0 \
  --master-addr 192.0.2.10 --gpus 1 --repeats 1 \
  --timeout 900 --output ep-A
```

在机器 B 执行：

```bash
bash run.sh --suite ep --nnodes 2 --node-rank 1 \
  --master-addr 192.0.2.10 --gpus 1 --repeats 1 \
  --timeout 900 --output ep-B
```

脚本会从当前仓库自动找到 EP 源码，编译扩展、检查 import，再依次运行两个后端的 Normal/LL 测试。构建最长默认 1800 秒，每个运行阶段最长 900 秒；EP master 端口默认 18716，其他分布式通信也需正常可达。某端失败后，两端收集日志并整体重跑。

## 6. 看结果和反馈错误

每次 `--output` 必须指定尚不存在的目录，避免覆盖旧结果。例如 `quick-check` 运行结束后：

| 文件 | 用途 |
|---|---|
| `quick-check/REPORT.md` | 总状态、失败详情、有效微基准的中位数和后端比值 |
| `quick-check/metrics.csv` | 微基准原始指标与有效性标记 |
| `quick-check/build-*.log` | 编译日志 |
| `quick-check/*-client.log`、`*-server.log` | 双进程运行日志 |
| `quick-check/ep-*.log` | 使用 EP 模式时的基准输出 |
| `quick-check/config.json`、`env-*.log` | 配置及环境诊断 |
| `quick-check.tar.gz` | 本次完整报告包 |

遇到编译失败、运行报错或超时，**把两台机器各自产生的 `.tar.gz` 一并提供**，并说明运行的命令。只测单机时提供该机的报告包即可。报告包含主机/网络信息，分享前按需要检查。

常见问题：缺头文件查 `build-*.log` 和 SDK 路径；设备创建失败查设备名、GID、权限和驱动；连接超时查 peer IP、端口和两端参数。脚本把编译失败、非零退出码、校验错误和超时均标记为失败，不要只看二进制最后是否打印 `ALL_OK`。

更多参数与测试边界见 [详细说明](README.md)，代码调用链见 [CPU Proxy 完整流程](CPU_PROXY_FLOW.md)。
