# 下一轮URMA实验：实际挡住第6块和末块的请求在等什么

**`normal_blockers_04` 已完成并通过采集扰动检查。** 204最晚第6块9/9次均由第2条WRITE挡在队首。共享CQ原始快照复算显示，满额且CQE不可读的等待下界中位数为671.90 μs。  [实测分析](results/normal_blockers_04/ANALYSIS.zh-CN.md)

**`normal_blockers_01` 已在 pre 阶段停止。** 两端构建和导入检查均通过，202 在 `torch.cuda.set_device(0)` 初始化时报错。202同一次开机内绕开Torch复测，原生MACA `mcGetDeviceCount` 返回3。204正常分配了GPU张量，双机报连接拒绝发生在202退出后。本轮没有有效Dispatch或探针样本。先按恢复步骤检查202的设备访问，再用新JOB复用已构建模块。首次构建修复已包含在`f141df5e`。 [失败日志](results/normal_blockers_01/diagnostics/BLOCKERS_FAILURE_REPORT.md) · [202原生MACA检查](results/normal_blockers_01/diagnostics/gpu_check_202_retry.log) · [恢复步骤](#202-gpu恢复与本次续跑)

先前的FIFO记录显示，204第6块被第1～2条WRITE挡住，末块被第2～7条WRITE挡住。但那轮采集扰动过大，CQ探针又只看了第6条WRITE。当前要验证的具体问题是 **命令已经在FIFO等待时，前序WRITE的哪个阶段占住了名额，以及这个现象是否出现在最晚被Proxy收走的通道**。本实验只需当前URMA环境。 [旧数据复算](../reference_events/NORMAL_BLOCKER_REVIEW.zh-CN.md)

## 实际采集和判定

Proxy发现窗口满时，对下一条FIFO命令做有间隔的只读检查。记录首次确认命令已到的时间和WR ID，并记录12个活跃通道的收取和名额释放。每个Proxy的ring0用已验证的只读CQ owner探针跟踪前7条payload WRITE。以实际JFC消费位置关联同一WR。两个观测共用本端`CLOCK_MONOTONIC_RAW`，每次调用后导出。

```text
下一条命令已在FIFO，软件窗口满
    → 看这段时间里实际排在软件队首的WR
       → 队首WRITE尚未开始调用SDK / SDK正在提交
       → 已提交，CQE仍无效 / CQE已可读但SDK尚未取走
       → SDK已取到，软件名额仍未释放
    → 窗口出现名额 → Proxy收走等待中的命令
```

首次确认命令已到可能晚于GPU发布，所以观测等待是下界。两次CQ快照之间无法判断的时间计为未知。没提前确认命令已到的样本保留为未覆盖。CQ探针覆盖12个活跃通道中的4个，其余通道仍有FIFO时间和实际队首身份。汇总另外标出每次调用里最后一个被Proxy收走第6块或末块的通道。并行通道的时间可以重叠。

| 数据结果 | 排查方向 |
| --- | --- |
| 已到FIFO、窗口满的毫秒级等待形成在晚收取通道，同时大部分有证据的时间处于前序WRITE的CQE仍无效 | 按实际WR优先查设备提交后到完成发布，继续索取对应的设备执行/确认点 |
| 主要时间处于CQE可读之后或SDK已返回之后 | 查CPU轮询及有序名额回收的具体阻挡者 |
| 已到且满额的等待很短，或主要出现在较早结束的通道 | 降低队首WRITE作为主瓶颈的优先级，回查仍在延后的通道或接收端 |
| 主要慢通道未覆盖，或控制组漂移/采集影响/组内CV超原5%门槛 | 留下观测和覆盖数。先确定缺的是哪一条边界，再安排任何后续采集 |

这是对当前URMA完整Dispatch中的因果链观测。对URMA与verbs剩余差距的量化还需要对应verbs证据或一个能改变整体时延的干预。设备内部排队、数据传输、ACK和设备完成发布时间仍需设备事件才能完全拆开。

## 两端构建及运行

两端先同步本分支到同一提交并进入 `/home/h00918771/uccl-ep-xpu-integration-new`。固定URMA内核 `6.6.0-gdr_f00955680_diag`、当前MACA3.9和归档私有UDMA库，202为`udmac1d1e2`、204为`udmac0d1e2`，GPU PCI默认`0000:21:00.0`。接口默认沿用202的`enp40s0`和204的`enp34s0f5u1u4`。

新增两个CPU测点，需要一次新模块构建。`setup`创建隔离源码树并构建，可提前在两端独立执行，无需对端同时在线，也无需运行双机benchmark。

```bash
# 202
bash test/normal_completion/run.sh --study blockers --action setup --node-rank 0

# 204
bash test/normal_completion/run.sh --study blockers --action setup --node-rank 1
```

默认源码为`/home/h00918771/metax_dev/normal_blockers_src_v1`，模块为`/home/h00918771/metax_dev/normal_blockers_build/urma_v1`。构建及预检通过时看到`NORMAL_COMPLETION_SETUP_PASSED`。脚本会校验私有provider、CQ布局和已构建模块。

两端均通过后，依次启动以下命令，每端各执行一次。默认使用master `141.61.17.202`、端口33780及JOB `normal_blockers_01`。

```bash
# 202
bash test/normal_completion/run.sh --study blockers --action run --node-rank 0

# 204
bash test/normal_completion/run.sh --study blockers --action run --node-rank 1
```

流程为预检 → `pre`关闭探针 → `probe`开启探针 → `post`关闭探针。固定4096 tokens/rank、BF16 Dispatch、chunk32、窗口8、ordered、202全开fence和204仅tail-off。每项5次预热、10次计时，分析保留后9次。结果和失败日志自动归档。保持正常的功能正确性检查。

## 回传和复算

回传 `test/normal_completion/results/normal_blockers_01/` 中两端的全部记录。重点是 `overlay/setup/request`、`build_manifest`、`status/runtime`、`bench.log`、`api_samples.jsonl` 和 `probe` 下的 `fadd_path.jsonl`。该文件沿用旧文件名，本轮存的是WRITE目标和FIFO观测。各项启动失败时保留对应`console`。模块二进制不必回传。

汇总可在结果到齐后离线执行。

```bash
python3 test/normal_completion/analyze.py --study blockers \
  --root test/normal_completion/results/normal_blockers_01 \
  --output test/normal_completion/results/normal_blockers_01/summary
```

`fifo_head_intervals.csv`包含每个已观测区间的队首WR及互斥时间段，`fifo_wait_summary.csv`列覆盖和数值，`latest_collected_channel.csv`列每窗最晚收取的位置。`controls.csv`和`decision.json`是归因前的控制检查。

## 202 GPU恢复与本次续跑

1. 两端均用平时运行benchmark的账号，在GPU空闲时各执行一次单机检查。它使用benchmark的MetaX运行库和Python，检查GPU0的设备初始化、分配、一个小算子以及同步。Torch失败时使用独立子进程直接检查MACA的设备枚举、设备设置和小块显存分配。

   ```bash
   set -o pipefail
   bash test/reference_events/check_blockers_gpu.sh 2>&1 | tee /tmp/normal_blockers_gpu_check.log
   ```

   两端都出现`BLOCKERS_GPU_READY`再启动双机任务。202的最新失败记录使用UID 1210，`BOOT_ID=b3a99bc0-bf1f-45b3-aa23-c1ef52496efb`，与首次失败的快照相同。旧快照中的DRM节点为`root:video 0660`。这还不能判断权限是否为原因。新输出包含实际组、`/dev/mxcd`和DRM节点的读写访问状态，以及MACA可提供的错误描述。

   - 若202普通账号显示节点访问失败，或设备维护账号能用同一套MACA和Python通过同一条检查，优先由管理员核对账号组、设备ACL和运行环境，再用正式账号验收。
   - 若设备维护账号也在同一套运行时下遇到`mcGetDeviceCount`失败，交回日志并由共享机器的管理员安排恢复。记录显示FLR和驱动重载之后故障仍在，维护窗口可考虑在相同URMA内核下重启202。重启后核对boot ID变化并重新执行单机检查。
   - 若出现`torch=failed native=passed`，优先对比Torch版本、库路径和加载的运行时。

2. 两端分别设置`RANK=0`（202）及`RANK=1`（204），用未使用的新任务名刷新本次启动的预检记录。旧`_01`保留失败日志。下面的`setup`会检查已有源码、SDK、构建标志和模块SHA，匹配时输出`BUILD_ALREADY_PASSED`并复用模块。成功后再执行双机`run`。重启时此步骤会记录新的boot ID。

   ```bash
   JOB=normal_blockers_02
   bash test/normal_completion/run.sh --study blockers --action setup --node-rank "$RANK" --job "$JOB"
   ```

   两端均看到`NORMAL_COMPLETION_SETUP_PASSED`后，分别执行：

   ```bash
   bash test/normal_completion/run.sh --study blockers --action run --node-rank "$RANK" --job "$JOB"
   ```

3. 交回两端完整的`test/normal_completion/results/normal_blockers_02/`。如果单机GPU检查失败，提供当端`/tmp/normal_blockers_gpu_check.log`即可，双机采集等GPU恢复后再启动。离线汇总时将原命令输入和输出中的`normal_blockers_01`替换为实际的新JOB。
