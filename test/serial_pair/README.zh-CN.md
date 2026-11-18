# RDMA与URMA最小串行WRITE/FADD对照：直接运行说明

## 1. 本轮要回答什么

完整Normal4096负载中，WRITE完成观测→通知FADD完成观测的中位数，202为verbs4.165μs/URMA682.990μs，204为verbs0μs/URMA427.350μs。URMA204至少34.71%的所采间隔发生在原子返回数据已本端可见之后。

本轮只问：**没有多通道、没有多个请求对竞争、没有原GPU→CPU FIFO和完整Proxy循环时，这种长间隔是否仍存在？** 不重复完整Normal，不扩大参数矩阵。

## 2. 固定测试内容

| 项目 | 本轮设置 |
| --- | --- |
| GPU | 每端MetaX C500 GPU0 |
| 工作线程 | 每端一个调用线程；在本端选定的单个CPU上执行 |
| 活跃数据队列 | 每端一条。verbs创建1个RC数据QP；URMA配置1条数据lane，另有1个必需但不承载本轮数据的控制端点 |
| 方向 | 202→204、204→202分别运行，非双向同时发送 |
| 在途请求对 | 最多1对；这一对的WRITE/FADD两条完成都取到后，才能发下一对 |
| WRITE | 459264B，等效原Normal BF16的32-token满chunk；源与目标均为GPU显存，地址模4096均为0 |
| 通知FADD | 更新远端GPU中的64位计数器，加32；返回旧值到本端主机内存 |
| verbs顺序 | 保留已有原生Normal通知微基准的signaled WRITE/FADD，FADD不额外加IBV_SEND_FENCE |
| URMA顺序 | 生产UrmaTransport，Normal WRITE RELAX、FADD STRONG＋原fence，completion all；按原默认同lane链提交 |
| 32/64 | 每迭代分别发送1对/2对，64的第二对在第一对全部完成后才发；WRITE大小保持不变 |
| 轮次 | 默认3轮，交替正反点顺序；每点每轮20次预热、101次计时，丢第一个计时值后100个有效迭代 |

32/64是该迭代单向发送的数据量的token等效数，不是两端各运行32/64-token的完整Dispatch，也没有专家路由、GPU打包和12通道。64使用两个数据存储槽分别校验，**不是两对同时在途**。

流程严格为：

```text
提交WRITE₁、紧接提交FADD₁ → 轮询到两条完成
                                  ↓
                         才能提交WRITE₂、FADD₂
```

不在WRITE完成后才提交FADD。每次迭代的两端TCP就绪/完成同步、GPU逐字节payload和计数器校验、JSON写文件均在测量之外。双端校验结束后才复用槽，避免上次数据冒充本次。不会启动持续运行的GPU观察kernel。

## 3. 与正在运行任务隔离

新入口创建仓库外的detached工作区，固定基线`fed2a437`，仅在该工作区扩展已有micro session，并添加本端SDK打点。当前仓库的Normal/LL GPU kernel、生产传输和旧测试驱动不修改。

两后端使用同一套叠加源码，分别编译各自模块。原来的.so、fadd_dense、tokens扫描模块不能用于本轮；本轮模块也不用于旧任务。

实际插入涉及：urma_micro.hpp/.cpp、verbs_write_micro.hpp/.cpp、uccl_ep.cc、urma_transport.cpp，以及新serial_pair.hpp。URMA仍调用生产UrmaTransport；verbs复用已有DMA-BUF/native micro的注册和WR构造，保留Normal关键标志，不调用完整Proxy提交循环。两个后端都绕过GPU命令算子和FIFO。

## 4. 两端设置公共变量

先拉取包含本目录的新代码。两端分别执行，202设RANK=0、204设RANK=1。共享机器上选定空闲CPU后可用`--worker-cpu`指定；默认auto取GPU所在NUMA节点内当前允许的最小CPU编号，只保证亲和性合法，不保证该CPU独占。

```bash
cd /home/h00918771/uccl-ep-xpu-integration-new
set -euo pipefail
export VERBS_DIAG_HOME=/home/h00918771
export URMA_DIAG_HOME="$VERBS_DIAG_HOME"
export VERBS_DIAG_PYTHON="$VERBS_DIAG_HOME/metax_dev/venv-maca-py312/bin/python"
export URMA_DIAG_PYTHON="$VERBS_DIAG_PYTHON"
export SERIAL_PAIR_PYTHON="$VERBS_DIAG_PYTHON"
export MACA_PATH=/opt/maca-3.9.0
export PYTHONDONTWRITEBYTECODE=1
RANK=0                   # 204改为1
BACKEND=verbs            # RDMA先用verbs；切到URMA时改为urma
JOB=serial_pair_01
TREE="$VERBS_DIAG_HOME/metax_dev/serial_pair_src_v1"
BUILD="$VERBS_DIAG_HOME/metax_dev/serial_pair_build/${BACKEND}_v1"
ROOT="$VERBS_DIAG_HOME/metax_dev/serial_pair_results"
DEST="$PWD/test/serial_pair/results"
CPU=auto                 # 可替换为本端选定的CPU编号，如0；两端可不同
uname -r
mx-smi
```

verbs要求当前已验证内核`6.6.0-145.3.34.165.oe2403sp3.aarch64`、设备rocep40s0f0、port1、RoCEv2 GID3。这里GID3沿用近期成功日志。构建使用USE_DMABUF，禁止退回host/peermem路径。连接MTU取两端设备当前active MTU的较小值，实际值写入path.json，不把历史perftest的1024B当作已固定值。

URMA要求当前已验证内核`6.6.0-gdr_f00955680_diag`及设备udmac0d1e2。正式运行前沿用上轮已成功的私有SDK、GPU PCIe atomic及设备配置；脚本只检查，不切内核或改PCI配置。

## 5. URMA额外变量

如果本轮BACKEND=urma，两端再执行。目录必须是本机真实、已验证的私有SDK位置；若迁移过，用正确路径替换。

```bash
export URMA_DIAG_LIBRARY_DIR="$URMA_DIAG_HOME/metax_dev/urma_after_kernel_20261029/diag/umdk_private_618957c3/lib"
export VERBS_DIAG_LIBRARY_DIR="$URMA_DIAG_LIBRARY_DIR"
export URMA_DIAG_INCLUDE_DIR="$URMA_DIAG_HOME/metax_dev/urma_sdk_private_include"
test -f "$URMA_DIAG_INCLUDE_DIR/urma_api.h"
test -d "$URMA_DIAG_LIBRARY_DIR"
```

额外MACA运行库设置沿用上轮成功环境，避免混入其他版本。

## 6. 定义入口、准备独立源码

两端执行以下函数定义。每次新登录终端都需要重新设置前面的变量和函数。

```bash
serial_pair() {
  bash test/serial_pair/run.sh --action "$1" \
    --backend "$BACKEND" --node-rank "$RANK" --job "$JOB" \
    --master-addr 141.61.17.202 --port 34380 --gid-index 3 \
    --worker-cpu "$CPU" --rounds 3 --warmups 20 --samples 101 \
    --tree "$TREE" --build-dir "$BUILD" --output "$ROOT" --destination "$DEST"
}
serial_pair prepare
```

应看到`SERIAL_PAIR_PREPARE_PASSED`。TREE必须在当前仓库外，且首次使用时不存在。相同代码重复prepare只验证来源，不覆盖源码。若提示来源变化，保留旧目录，改新的TREE/BUILD路径，不手改旧树来凑通过。

两种后端可以使用同一个TREE，因叠加源码相同；BUILD必须按backend分开。prepare不运行GPU测试，可在等待内核环境时完成。

## 7. 构建与检查

在对应后端可运行环境中，两端分别执行，不用同时启动：

```bash
serial_pair build
serial_pair check
```

依次应看到`SERIAL_PAIR_BUILD_PASSED`、`SERIAL_PAIR_CHECK_PASSED`。构建并行数1，沿用原host tests和编译检查。检查失败停止，提供已有日志，不跳过GPU/SDK/设备预检查。

## 8. 正式测试

确认上一项GPU测试已结束。202先执行，180秒内204执行相同命令（RANK不同）：

```bash
serial_pair run
```

run会再做一次只读预检查，然后只运行上述串行micro，**不会执行完整Normal/LL benchmark**。默认每端12个点（4种方向/数据量×3轮）。每次迭代都做GPU数据和计数器校验；两端最终应看到`SERIAL_PAIR_RUN_PASSED`，bench.log末尾有`SERIAL_PAIR_ALL_PASSED`。

结果目录已有时不覆盖，使用新的JOB。失败停止并收集已有文件，不自动重试。若手动中断后需补收：

```bash
serial_pair collect
```

## 9. 换另一后端

待机器切换到对应内核后，重新执行公共变量设置：切换BACKEND，并重算BUILD，保留相同JOB/TREE/ROOT/DEST，按需设置URMA额外变量，再定义函数。然后运行：

```bash
serial_pair build
serial_pair check
serial_pair run
```

原后端结果保持在独立子目录下，不要求两种后端同时运行。若代码有更新导致TREE来源检查失败，使用新的TREE和两后端BUILD；不要把不同叠加源码拼成对照。

## 10. 分析与需要提供的日志

只需要完整提供以下目录，对应后端的两端文件都保留：

```text
test/serial_pair/results/serial_pair_01/verbs/
test/serial_pair/results/serial_pair_01/urma/
```

内容包括overlay_node0/1、cases中的build/check/run_gate/run、console及构建日志。run内最重要的文件：samples.jsonl、status.json、driver_status.json、request/worker_request、manifest、preflight/runtime、affinity、path、plan、bench.log和host/kernel快照。无需提供.so、object、cache、临时目录。

两端文件齐全后，每个后端分别分析，无需GPU：

```bash
"$SERIAL_PAIR_PYTHON" test/serial_pair/analyze.py \
  --root "$DEST/$JOB" --backend "$BACKEND" \
  --output "$DEST/$JOB/summary_${BACKEND}"
```

两后端summary都生成后：

```bash
"$SERIAL_PAIR_PYTHON" test/serial_pair/compare.py \
  --root "$DEST/$JOB" --output "$DEST/$JOB/comparison"
```

输出目录必须不存在。summary与comparison也一并保留。若只测完一个后端，可先提供，不需要等另一个后端。

## 11. 读数如何解释

- **WRITE时延**：WRITE所在SDK提交开始→本端SDK返回其完成。
- **整对时延**：首次SDK提交→该对两条完成均被本端取得。
- **W→A间隔**：对应FADD完成观测减WRITE完成观测；同批返回可为0，反向观测保留负数，不计算以0为分母的倍数。
- **32/64总SDK跨度**：第一对首次SDK提交→最后一对全部完成。64包含两对之间的实际CPU推进间隙。
- **loop_api**：本次C++循环内部，包含构造/封装、提交、轮询；不含线程创建、TCP、GPU校验和JSON输出。

verbs保留WRITE与FADD分开提交；URMA保留原同lane链提交。分别记录各自实际SDK边界，不强行改造成同一种提交方式。软件时间戳有少量观测开销，不等于设备物理完成时间。接收GPU在计时后校验，因此本轮不测GPU何时首次看见数据。

正常结果每方向、每数据量有3轮×100个有效迭代。32每方向300对，64每方向600对。分位数来自相关采样，不等于独立实验置信区间。稳定门槛：每轮总SDK跨度CV≤5%，轮均值跨度/中位数≤5%。未通过时保留结果，只报告波动，不自动追加重测矩阵。

| 结果 | 能推进的结论 |
| --- | --- |
| 单队列串行仍复现URMA数百μs间隔 | 多通道、原FIFO/Proxy不是复现的必要条件，重点查单队列SDK/设备/顺序依赖与完成交付 |
| W→A间隔短，但URMA整对仍明显慢 | 检查SDK提交耗时、WRITE完成时延，不能只看W→A判定差异消失 |
| 两者都快，长间隔消失 | 问题依赖原并行负载、Proxy服务、注册规模/布局等条件；不能只认定并发是唯一原因 |
| 仅某个方向/轮次明显慢 | 先检查对应节点和稳定性记录，不用所有样本混合掩盖差异 |
| 校验或状态失败 | 停止性能归因，提供已有日志 |

本轮不是完整Normal性能替代，也不直接预测优化后能省多少毫秒。注册区域约46MB且布局更简单，地址/并发/GPU消费行为与完整程序不同；结论必须保持这个适用范围。

## 12. 开发端验证范围

已检查源码叠加位置、严格串行和同批/反向完成观测、完整报告/缺样本拒绝，以及两后端真实TCP双端worker流程。C++公共计时器与Python解码联测通过；两套实际串行session在模拟NIC/GPU边界下均跑通32/64和两个方向，各12次迭代，并校验数据/计数器。原verbs/URMA host编译与模拟回归通过，9段文档Bash命令语法通过。

URMA开发端使用参考SDK头文件和测试占位GPU字段，模拟GPU内存判别；这不验证实际私有SDK ABI。开发机没有MetaX GPU，本轮仍需服务器构建、真实SDK检查及实机正确性验证，不预先保证实机通过。
