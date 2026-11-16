# Normal不同tokens数量：verbs / URMA同口径对比

## 1. 这轮回答什么

确定Normal Dispatch的URMA/verbs时延差距是否随输入tokens变化，特别是小负载和持续多批次发送时是否表现不同。保留Combine作为同轮对照。

固定测试 **64、128、256、512、1024、2048、4096 tokens/rank**，不是遍历其中每一个整数。每端1个rank、1张GPU0，所以两端合计输入为128～8192 tokens；它不等同跨机实际发送token数，路由及本地处理会影响远端数据量。

这是总调用时延的规模扫描，不采集内部阶段、CPU请求事件或硬件时间戳。它不能单独证明fence/FIFO就是根因；结果用于判断差异是否随规模改变，再选择需要深入拆分的形状。**不要把历史128-token smoke与4096-token正式结果拼成曲线。两个后端都运行本轮脚本。**

## 2. 两后端完全相同的负载与采样

| 项目 | 固定值 |
| --- | --- |
| 程序 | 原Normal `test_internode.py`的冻结副本；不运行LL |
| 形状 | hidden7168、experts256、topk8、每端GPU0一张C500 |
| 测点 | BF16/FP8 Dispatch chunk32；Combine chunk12/16；另保留layout |
| SM / NVL chunk | 24 / 4；Normal数据通道12 |
| Proxy | 每端4线程；关闭UCCL显式绑核，继承启动线程CPU允许集合 |
| 软件窗口 | 每FIFO 8个未回收命令，WRITE和FADD共同占用 |
| URMA配置 | WRITE RELAX、FADD STRONG且fence on、completion all、lane-mixed、ordered回收 |
| verbs配置 | 当前同一源码编译的verbs后端，DMA-BUF；rocep40s0f0，默认GID3 |
| Dispatch计时 | inactive paced：逐样本同步，CUDA Event测完整调用；采集/主机时钟关闭 |
| Combine/layout计时 | 沿用两后端共同的批量CUDA Event方式，与Dispatch分别解释 |
| 每规模 | 3轮独立启动；每轮10次预热、31次计时，丢首值后30个有效样本 |

每个后端共21个正式任务，**64/128也使用完整10/31配置，不走旧smoke的1/3采样**。每项运行原有24组正确性矩阵；首个64-token任务同时验证本轮入口，不额外安排smoke。记录能力编入模块以复用已经验证的paced计时入口，但运行中事件、时钟采集和旧GPU细分打点均关闭。

三轮顺序固定，两端自动使用相同顺序：

```text
r01：64 → 128 → 256 → 512 → 1024 → 2048 → 4096
r02：4096 → 2048 → 1024 → 512 → 256 → 128 → 64
r03：512 → 1024 → 2048 → 4096 → 64 → 128 → 256
```

不同顺序用于暴露运行漂移，不能消除所有共享机器干扰。3轮均值分别保留，主指标取3个均值的中位数；不只挑最快一轮。

## 3. 选择当前可用后端，并在两端设置环境

在master（202）和worker1（204）分别进入同一版本仓库。两后端分开在适用内核运行，脚本不切内核、重启、修改PCIe寄存器或设备设置。

| BACKEND | 预期内核 | 默认数据设备 |
| --- | --- | --- |
| verbs | `6.6.0-145.3.34.165.oe2403sp3.aarch64` | `rocep40s0f0`，GID3 |
| urma | `6.6.0-gdr_f00955680_diag` | `udmac0d1e2`，RC |

下面两端都执行，**只有RANK不同**。JOB在两端及两个后端保持同名；BUILD会按后端分开。

```bash
cd /home/h00918771/uccl-ep-xpu-integration-new
set -euo pipefail

export VERBS_DIAG_HOME=/home/h00918771
export URMA_DIAG_HOME="$VERBS_DIAG_HOME"
export VERBS_DIAG_PYTHON="$VERBS_DIAG_HOME/metax_dev/venv-maca-py312/bin/python"
export URMA_DIAG_PYTHON="$VERBS_DIAG_PYTHON"
export TOKENS_DIAG_PYTHON="$VERBS_DIAG_PYTHON"
export MACA_PATH=/opt/maca-3.9.0
export PYTHONDONTWRITEBYTECODE=1

RANK=0                 # 202设0；204设1
BACKEND=urma           # 当前是RDMA内核时改为verbs
JOB=normal_tokens_01
ROOT="$VERBS_DIAG_HOME/metax_dev/normal_tokens_results"
BUILD="$VERBS_DIAG_HOME/metax_dev/normal_tokens_build/${BACKEND}_v1"
DEST="$PWD/test/normal_tokens/results"
PY="$TOKENS_DIAG_PYTHON"

uname -r
mx-smi
"$PY" -c 'import sys; print(sys.executable)'
```

MACA版本及Python沿用两端已验证可运行的环境；跨后端尽量保持相同MACA、Torch和运行库，汇总会核对。上面的Python检查只确认解释器，真实扩展导入由后面的check在完整运行环境中验证。

**URMA环境额外设置**以下已验证私有SDK路径；如果自己的实际路径不同，填写真实目录，不创建空目录绕过检查：

```bash
export URMA_DIAG_LIBRARY_DIR="$URMA_DIAG_HOME/metax_dev/urma_after_kernel_20261029/diag/umdk_private_618957c3/lib"
export VERBS_DIAG_LIBRARY_DIR="$URMA_DIAG_LIBRARY_DIR"
export URMA_DIAG_INCLUDE_DIR="$URMA_DIAG_HOME/metax_dev/urma_sdk_private_include"
test -f "$URMA_DIAG_INCLUDE_DIR/urma_api.h"
test -d "$URMA_DIAG_LIBRARY_DIR"
```

已有额外依赖路径设置继续沿用，如`VERBS_DIAG_EXTRA_LIBRARY_DIRS`/`URMA_DIAG_EXTRA_LIBRARY_DIRS`。URMA硬件、原子能力等仍由既有预检查验证；报错停止并保留日志，不忽略检查继续运行。

## 4. 查看计划、构建、检查：两端分别运行

先查看计划，此命令不加载扩展、不跑GPU：

```bash
bash test/normal_tokens/run.sh --action plan \
  --backend "$BACKEND" --node-rank "$RANK" --job "$JOB" \
  --build-dir "$BUILD" --output "$ROOT" --destination "$DEST"
```

每端每个后端只需构建一份模块，七个tokens复用它。首次使用新BUILD目录：

```bash
bash test/normal_tokens/run.sh --action build \
  --backend "$BACKEND" --node-rank "$RANK" --job "$JOB" \
  --build-dir "$BUILD" --output "$ROOT" --destination "$DEST"
```

成功标志`NORMAL_TOKENS_BUILD_PASSED`。编译并行数固定1，包含现有CPU检查和编译探针；没有本地GPUbenchmark替代实机验证。若提示BUILD_INPUTS_CHANGED/SOURCE_CHANGED，使用新的BUILD目录，两端保持同样版本，不删除旧结果或修改manifest。

本地check不需要两端同时启动：

```bash
bash test/normal_tokens/run.sh --action check \
  --backend "$BACKEND" --node-rank "$RANK" --job "$JOB" \
  --build-dir "$BUILD" --output "$ROOT" --destination "$DEST"
```

成功标志`NORMAL_TOKENS_CHECK_PASSED`。此步骤确认模块、实际运行能力、库与内核；不执行双机benchmark。

## 5. 正式运行：两端执行相同命令，只改RANK

先在202启动，下列同一命令在204于180秒内启动。后续21项由两端脚本按固定顺序自动推进，每项仍进行双端配置/就绪配对。期间保持同一boot、源码、模块、库和空闲条件，不在扫描中途编译或改配置。

```bash
bash test/normal_tokens/run.sh --action run \
  --backend "$BACKEND" --node-rank "$RANK" --job "$JOB" \
  --master-addr 141.61.17.202 --port 33780 --gid-index 3 \
  --build-dir "$BUILD" --output "$ROOT" --destination "$DEST"
```

输出`TOKENS_POINT_BEGIN`表示开始某点，`TOKENS_POINT_PASSED`表示通过；最终两端应各有：

```text
NORMAL_TOKENS_RUN_PASSED backend=<verbs或urma> rank=<0或1> round=all
```

若机器窗口不足，可把上面的run命令分别增加`--round 1`、`--round 2`、`--round 3`，每次两端选择同一轮。三轮仍须同一boot和模块；完整汇总要求全部三轮。

重复启动时默认拒绝覆盖。仅对已经成功的点，两端可在同一命令加`--resume`；它会验证原始结果、当前boot和模块后跳过成功点，**不会重跑失败或不完整点，也不会删除其结果**。两端必须使用同样的round/resume选项。失败点先提交现有日志供分析，不自行重复整个扫描。

出现失败时脚本会收集已有日志后退出，不自动重试。如果手动中断后需要补收已有文件，可用：

```bash
bash test/normal_tokens/run.sh --action collect \
  --backend "$BACKEND" --node-rank "$RANK" --job "$JOB" \
  --build-dir "$BUILD" --output "$ROOT" --destination "$DEST"
```

collect只收集，不启动benchmark。当前可运行哪个后端就先完成哪个；另一后端等设备适用环境可用后，换BACKEND，重复第3～5节，保留相同JOB和负载。两后端模块分别构建，不能直接复用.so。

## 6. 需要提供哪些日志

脚本自动将必要结果复制到以下目录，保留整个对应后端子目录即可：

```text
test/normal_tokens/results/normal_tokens_01/
├── urma/
│   ├── plan_node0.json
│   ├── plan_node1.json
│   └── cases/normal_tokens_01_urma_r<01..03>_t<64..4096>/
│       ├── node0/...
│       ├── node1/...
│       ├── console/node0/...
│       ├── console/node1/...
│       └── build/...  # 首次构建/检查时收集到的构建记录
└── verbs/
    └── 同样结构，任务名含verbs
```

每个正式任务必须保留：`setup.json`、host/GPU/内核快照、模块manifest、冻结benchmark、`bench.log`、`status.json`、`runtime.json`、`api_samples.jsonl`、`sample_summary.jsonl`、`reference_control.jsonl`、measurement boundary和已有计数快照。verbs还需`config.json`；URMA需预检查及角色/排序日志。保留两端console、构建/探针/host_tests日志。

不需要上传.so、object、cache/tmpdir；collector已排除它们。失败时同样保留已生成的整个目录，不必等待所有tokens完成。

## 7. 汇总：可以只总结已完成的一个后端

两端日志已放在同一个JOB目录后运行。仅URMA已完成时：

```bash
"$PY" test/normal_tokens/summarize.py \
  --root "$DEST/$JOB" --backend urma \
  --output "$DEST/$JOB/summary_urma"
```

仅verbs已完成时，将`--backend urma`改为`--backend verbs`，输出改为`summary_verbs`。两个后端全部完成后：

```bash
"$PY" test/normal_tokens/summarize.py \
  --root "$DEST/$JOB" --backend both \
  --output "$DEST/$JOB/summary_compare"
```

输出目录必须不存在。也可以直接提供第6节的原始结果，由开发端汇总，不要求服务器安装画图库。

输出包括：

- `REPORT.zh-CN.md`：按tokens列出BF16/FP8 Dispatch、Combine12/16两端时延及后端差额。
- `per_run.csv`：每轮均值、P50/P95/P99、CV，保留漂移证据。
- `aggregate.csv`：三轮均值的中位数、轮间跨度及稳定标记；另有90个样本的描述性分位数。
- `samples.csv`、`paired_calls.csv`：原始有效值及对应序号两端本地耗时较大值。
- `comparison.csv`：两后端都完成时输出，各tokens的绝对差和百分比。
- `context.json`：实际源码、模块、库、boot、内核及计划记录。

稳定标记要求每轮CV≤5%，且三轮均值的最大最小跨度/中位数≤5%；不稳定点仍输出，不删数据、不自动重跑。这是工程筛选，不是显著性检验。P99来自有限样本，只描述此次样本尾部；90个样本不当作90次独立实验。

汇总严格拒绝源码、benchmark、诊断、模块、boot、库在同后端扫描中改变；跨后端核对共同源码/负载及MACA/Torch，允许各自内核、数据设备、SDK和后端编译开关不同。若共同输入不一致，可以保留单后端结果，不能强行合并出差额。

主线先看BF16 Dispatch：哪些tokens的URMA差额稳定为正、差额是固定开销还是随规模扩大；再看两端方向与FP8/Combine是否一致。`max_local`是对应序号两端本地API耗时的较大值，不是跨机统一时钟的全局makespan。WRITE/FADD可并行在途，不能把调用差额直接换算成单WRITE或单FADD时延。

## 8. 开发端验证范围

开发端验证计划覆盖、两后端参数、采样完整性与来源校验、两端样本配对、汇总统计和失败停止/日志收集；本地没有MetaX GPU，未声称本轮七种形状已经在实机通过。实机编译、64-token首项正确性及后续结果以第4～5节运行记录为准。
