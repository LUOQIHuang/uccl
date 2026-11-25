# 现在运行这一项：验证8个软件名额是否限制Normal流水线

这是当前URMA环境的下一项实验。两端各运行下面一条命令，自动完成8→32→32→8四项。复用刚成功的扩展，不重新编译，不切内核，不运行阶段打点、LL或tokens扫描。

## 要回答什么

当前日志中，末块命令已发布，但202/204还分别等约1.920/3.427ms才被CPU读出；这段等待几乎一直由软件pending窗口已满、队头FADD/大WRITE尚无完成观测占据。每条数据通道单次记录最多26条collect命令，原上限只有8。具体请求与统计边界见[FIFO重放](FIFO_CANDIDATES_20261125.zh-CN.md)。这些带打点数据只提供候选，不用作本次收益基线。

**只检验一个问题：把两端每通道软件未完成命令额度提高到32，能否让更多已准备命令提前提交，改善关闭采集的完整Dispatch？** 32高于现有记录中单次每通道的26条命令；不保证连续运行/其他输入永远不满，也不等于硬件同时执行32条WRITE。

上次8→4检验的是“减少提交压力会不会改善”；这次检验“放宽软件准入是否改善”。此前约1%的ready-retire实验只回收已完成请求，这次允许更多尚未完成的请求同时占名额，机制不同。不是重跑旧实验，也不展开4/16/32/64扫描。

| 项目 | A | B |
| --- | --- | --- |
| 202/204软件pending上限 | 8 / 8 | 32 / 32 |
| 设备 | 202 c1、204 c0 | 相同 |
| 数据FADD fence | 202全开、204仅tail-off | 相同 |
| WRITE / FADD顺序 | RELAX / STRONG | 相同 |
| completion / 回收 | all / ordered | 相同 |
| GPU/CPU阶段采集、host clock | 关闭 | 关闭 |
| 计时节奏 | 连续batch | 相同 |

它改变实际准入并发和自然批量大小；若有收益，先归于这项准入策略，不宣称硬件原子或单WRITE突然变快，也不直接归因URMA相比RDMA的全部差额。

## 1. 两端准备

适用当前 `6.6.0-gdr_f00955680_diag` 内核、MACA3.9环境，设备沿用已通过组合。不要修改PCI开关、设备绑定或驱动。

两端进入同一仓库、拉取同一提交：

```bash
cd /home/h00918771/uccl-ep-xpu-integration-new
git pull --ff-only https://github.com/LUOQIHuang/uccl.git codex/metax-urma-integration
git rev-parse HEAD
```

两端HEAD应一致。默认路径与刚成功的device_pair测试相同：

```text
Python  /home/h00918771/metax_dev/venv-maca-py312/bin/python
MACA    /opt/maca-3.9.0
UMDK    /home/h00918771/metax_dev/urma_after_kernel_20261029/diag/umdk_private_618957c3/lib
BUILD   /home/h00918771/metax_dev/normal_scope_build/urma_v1
```

本次仅修改诊断Python/shell和离线汇总，生产C++/GPU编译输入不变。现有 `get_normal_max_inflight` 已读取环境参数，驱动同时校验实际运行值及Proxy日志中的limit。若构建实际在其他目录，两端命令追加各自 `--build-dir /已成功构建目录`，不要手改manifest或拷贝.so。

默认网卡202=`enp40s0`、204=`enp34s0f5u1u4`，GPU PCI=`0000:21:00.0`。若上一轮实际已用不同值，沿用已成功值：分别设置 `URMA_DIAG_IFNAME`、`URMA_DIAG_GPU_PCI`。其他路径可沿用环境变量 `URMA_DIAG_HOME`、`URMA_DIAG_PYTHON`、`MACA_PATH`、`URMA_DIAG_LIBRARY_DIR`。无需手动设置窗口或fence变量，驱动强制A/B对应值；runner在A/B都清除外加字节限额，使用原始默认值。

## 2. 两端执行

确认没有自己上次遗留的benchmark，并协调两端独占本轮测试；不要同时启动其他测试。33680及33681端口应空闲。202先启动，204尽量在60秒内启动对应命令。

202：

```bash
bash test/reference_events/run_credit_ab.sh --node-rank 0
```

204：

```bash
bash test/reference_events/run_credit_ab.sh --node-rank 1
```

脚本自动选择202=`udmac1d1e2`、204=`udmac0d1e2`，四个任务如下：

| 顺序 | 任务名 | 两端窗口 |
| --- | --- | ---: |
| 1 | urma_credit32_01_r01_w8 | 8 |
| 2 | urma_credit32_01_r01_w32 | 32 |
| 3 | urma_credit32_01_r02_w32 | 32 |
| 4 | urma_credit32_01_r02_w8 | 8 |

每项先check再run；固定每端4096 tokens、hidden7168、topk8、experts256，沿用原Normal正确性检查与SM24、Dispatch chunk32。每点5预热、10计时、丢首值后保留9条。保留现有harness的FP8/Layout/Combine控制项，但主判断只看BF16 Dispatch，不将约133ms Combine与历史约7ms数值拼接。

没有额外128门禁：本次没有新增GPU路径，每项完整Normal正确性检查在性能调优之前执行；检查失败即停止，不带病进入后续配置。已有同名任务会拒绝覆盖；确需重跑时两端加同一新 `--job urma_credit32_02`，无需重测此前coverage。

成功需要两端各出现四次 `CREDIT_CASE_END ... rc=0`，最终：

```text
CREDIT_AB_PASSED rank=0 ...
CREDIT_AB_PASSED rank=1 ...
```

`MODE_PARITY_CHECK_PASSED`只代表单项预检查通过，不代表整个实验完成。

## 3. 失败怎么处理

任一check/run失败，脚本自动收集已有日志并停止。不要重复connect、设备扫描或前面全部测试；只提供失败时已有结果。不要为了通过而取消STRONG、fence、quiet/barrier、错误检查或SHA校验。超时只清理本任务进程组，runner不复位共享GPU或驱动。

这次扩大的是软件额度，现有代码申请JFS深度2048、transport上下文容量1024，不改其配置。quiet/barrier仍保留控制边界，回收仍须观察到完成记录。完整正确性通过仅覆盖当前固定负载，不将32自动写成生产默认值。

## 4. 提供哪些日志

提供自动收集的整个目录：

```text
test/reference_events/results/urma_credit32_01/
  urma_credit32_01_r01_w8/
  urma_credit32_01_r01_w32/
  urma_credit32_01_r02_w32/
  urma_credit32_01_r02_w8/
```

每项需要两端node0/node1以及console；保留setup/preflight、host、build_manifest、runtime、status、bench、api_samples、sample_summary、reference_control、measurement_boundary和设备/内核快照。无需.so、objects或额外源码。不要仅提供终端均值截图。

结果到齐后由开发侧汇总，不必额外占服务器机时；也可在任一Python环境运行：

```bash
python3 tools/reference_events/summarize_credit_ab.py \
  --root test/reference_events/results/urma_credit32_01 \
  --job urma_credit32_01 \
  --output test/reference_events/results/urma_credit32_01/summary
```

输出目录必须不存在。输出per_node、paired_calls、rounds、decision、context；保留全部9条有效样本，不挑最优值。工具核对同节点四项的源码/模块/boot/库/负载和实际策略，只允许窗口计划和值变化。

## 5. 预先固定如何判断

主指标为同序号两端本地API耗时较大值的均值，不是统一跨机时钟的makespan。另检查两端均值/CV。所有正确性检查通过是前提。

| 结果 | 得到什么，下一步如何收敛 |
| --- | --- |
| 两配对均改善≥5%，CV≤5%，A和B各自轮间漂移≤3% | 支持软件准入策略限制当前流水线，保留候选；再针对新配置验证正确性与推进变化，不先追加窗口扫描 |
| 两配对变化绝对值均<3%，同样稳定 | 关闭“简单提高额度是主要优化点”方向；保持8，转向提交后的完成推进，不以满队列本身继续做额度微调 |
| 稳定变慢 | 提高并发没有净收益，保持8；不能因理论上命令可提前就声称优化成功 |
| 3%～5%、方向相反或稳定性未通过 | 证据不足；先审查现有样本，不自动重跑整套或追加多参数扫描 |

无论结果如何，不会单凭本实验宣布CPU/设备/网络/provider中的某一项已独占全部性能差距，也不能替代后续可用时的同口径RDMA对照。它的用途是以一组有限的无打点干预，判断是否继续投入软件准入方向。
