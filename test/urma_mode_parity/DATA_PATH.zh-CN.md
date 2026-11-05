# URMA Normal 数据路径分解测试

本轮只使用 URMA 环境，只分析 Normal。最新完成合并 A/B 中，WRITE CQE 数减少约 21%，但 Dispatch 完整调用时延仅改善不到 1%；因此继续判断大块 WRITE、通知 FADD、接收 GPU 观察通知这几项中，哪项值得优先优化。它们目前都是待验证的方向，尚未确定根因。

## 1. 本轮做什么

默认运行 **4 个双端任务**，无需切换 verbs 内核，不运行 LL，也不重复上一轮 all/paired A/B：

1. `layout`：运行真正的 `test_internode.py` Normal，抓取 FP8/BF16 Dispatch 实际 WRITE 长度、源/目标地址对 4096 的余数及 lane 分布。固定 tokens4096、hidden7168、experts256、topk8、SM24、NVL chunk4、Dispatch chunk32；同时执行原有正确性检查和 Combine 对照点。每点预热5次、计时5次。**抓取带锁，会扰动时延，其计时不能作为性能基线。**
2. `micro_r01`、`micro_r02`、`micro_r03`：各启动一个新进程。每轮12个测点，每点预热20次、计时50次，丢弃首个计时，保留49个有效值。最终分别统计两端各轮均值的中位数、范围、单轮CV和p95，不把两端当作6轮重复。

每种 FP8/BF16 形状各6个测点：

| 测点 | 每线程 WRITE 数/次 | 每 lane 窗口 | 完成边界 |
| --- | ---: | ---: | --- |
| batch WRITE-only | 32 | 1、4 | 排空本批本地 CQE |
| batch WRITE＋FADD | 32 | 1、4 | 排空本批 WRITE/FADD 的本地 CQE |
| step CQE | 8 | 1 | 排空本地 WRITE/FADD CQE |
| step GPU observed | 8 | 1 | 排空本地 CQE，并等本机 GPU 观察对端通知、读回校验收到的数据 |

微测直接调用生产 `UrmaTransport`，默认4个CPU发送线程，每线程8条数据 lane＋1条控制 lane，RC、Normal WRITE RELAX、FADD STRONG＋fence、完成策略 `all`。主数据使用 GPU 显存，控制段使用生产的对齐分配方法和原生 FADD，加载已配置的私有 URMA 库；数据路径不调用 verbs。

FP8/BF16各选择采集中出现最多的、长度至少4096B的 WRITE，再选择该长度下出现最多的源/目标对齐组合。两端使用 node0 生成的同一计划；node1 自己的采集也保留。`plan.json` 中保存实际字节数、对齐及覆盖数量，**不预设消息长度，不是完整流量回放**。微测每点首个预热还会核对实际提交地址（含导入MVA）的对齐和WRITE数量，记录 `micro_layout_verified.jsonl`；正式计时关闭采集。

## 2. 两端设置环境

202（141.61.17.202）设 `RANK=0`，204（141.61.17.204）设 `RANK=1`。两端先更新到本轮分支，在各自 Bash 终端执行，只改 RANK：

```bash
cd /home/h00918771/uccl-ep-xpu-integration-new
set -euo pipefail
export URMA_DIAG_HOME=/home/h00918771
export URMA_DIAG_PYTHON=/home/h00918771/metax_dev/venv-maca-py312/bin/python
export URMA_DIAG_LIBRARY_DIR=/home/h00918771/metax_dev/urma_after_kernel_20261029/diag/umdk_private_618957c3/lib
export URMA_DIAG_INCLUDE_DIR=/home/h00918771/metax_dev/urma_sdk_private_include

RANK=0  # 204 改为 1
BUILD=/home/h00918771/metax_dev/urma_mode_parity_build/urma_normal_data_v1
JOB=urma_normal_data_01
RESULTS=/home/h00918771/metax_dev/urma_data_results/$JOB
CONSOLE=/home/h00918771/metax_dev/urma_data_console/$JOB
mkdir -p "$CONSOLE"
```

保留之前检查通过的 URMA 内核、Atomic Request、runpm、私有库和 SDK。默认设备 `udmac0d1e2`、GPU0、网卡 `enp33s0`；若之前使用额外运行库目录，继续设置同一个 `URMA_DIAG_EXTRA_LIBRARY_DIRS`。不要换用系统 Python，不要使用上次完成合并 A/B 的旧扩展。

## 3. 两端各自重新编译

本轮新增 C++ 和 CUDA/cu-bridge 文件，**必须重编译新模块**，不能只更新测试脚本：

```bash
bash test/urma_mode_parity/run.sh --action build \
  --node-rank "$RANK" --job normal_data_build --build-dir "$BUILD" \
  2>&1 | tee "$CONSOLE/build_node${RANK}.log"
```

两端都出现 `BUILD_PASSED` 后再继续。构建先用真实私有 SDK 跑CPU模拟测试，再编译 MetaX 扩展；开发机已通过模拟测试，但没有 MetaX GPU，未完成真实 GPU 编译和双机性能验收。

构建失败就停在这里，保留 `build.log`、`host_tests.log`、`build_request.json` 和终端日志。输入未改变可以原命令重试；源码/SDK/构建参数变化必须换新 BUILD 目录，不要手改 manifest。若已运行过本轮任务，重跑时也换 JOB，避免混入旧日志。

## 4. 两端各自检查

这一步不要求同时开始：

```bash
bash test/urma_mode_parity/run.sh --action check \
  --node-rank "$RANK" --job normal_data_check --build-dir "$BUILD" \
  --backend urma --mode normal --phase-suite --api-only --capture-layout \
  --normal-completion all \
  2>&1 | tee "$CONSOLE/check_node${RANK}.log"
```

应出现 `MODE_PARITY_CHECK_PASSED`，runtime 中 `layout_diagnostics` 和 `micro_diagnostics` 均为 `true`，`normal_completion` 为 `all`。检查还包括源码/模块 SHA、SDK、私有库、内核和设备；它不是 GPU benchmark。

## 5. 双端正式运行

先在202启动，随后在204执行同一命令；启动间隔控制在180秒内，两端使用同一 JOB、参数和源码：

```bash
bash test/urma_mode_parity/run_data_path.sh \
  --node-rank "$RANK" --master-addr 141.61.17.202 \
  --job "$JOB" --build-dir "$BUILD" \
  --port 31480 --result-root "$RESULTS" \
  2>&1 | tee "$CONSOLE/suite_node${RANK}.log"
```

31480用于任务握手，31481先用于采集任务的 torchrun，再用于微测的CPU控制连接；两端端口均须可用。CPU控制连接不进入计时区间。套件内部先后执行4个任务，不需要手工逐点运行。

每轮微测在 `micro/bench.log` 中应看到12条 `URMA_MICRO_POINT` 和 `URMA_MICRO_ALL_PASSED`；最终两端均应输出 `NORMAL_DATA_PATH_ALL_PASSED`。默认三轮，**本轮先不要减少 repeats**。需要当前扩展的完整 Normal 健康对照时，两端同时给套件加 `--with-reference`，会额外运行一个无采集、无阶段统计、50/50次的 Normal API 任务；它只是单次参考，不替代三轮稳定性对照。

如果任何步骤失败，套件会停止，先收集失败任务和两端终端日志；不要绕过设备空闲检查，也不要继续运行后面的任务。GPU数据、原子计数或通知观察不匹配均算失败，不产生有效性能结论。若内核出现AE错误，保留测量结束前与清理后的两个时间边界，不能仅凭最终退出码判断无异常。

## 6. 两端分别收集结果

无论成功失败，两端都执行以下命令。`--collect-suite` 会保留所有匹配 JOB 的任务、节点和 JSON 元数据，并跳过大体积扩展、目标文件、缓存：

```bash
bash test/urma_mode_parity/run.sh --action collect --collect-suite \
  --node-rank "$RANK" --job "$JOB" --build-dir "$BUILD" \
  --output "$RESULTS"

DEST="$PWD/test/urma_mode_parity/results/$JOB"
mkdir -p "$DEST/console/node${RANK}" "$DEST/build/node${RANK}"
cp -v "$CONSOLE"/*.log "$DEST/console/node${RANK}/"
for NAME in host_tests.log build.log build_request.json manifest.json; do
  if [ -f "$BUILD/$NAME" ]; then
    cp -v "$BUILD/$NAME" "$DEST/build/node${RANK}/"
  fi
done
```

如果构建失败、没有任何运行任务，collect会报 `NO_RESULTS_TO_COLLECT`；此时跳过collect，直接执行上面 DEST 起的日志整理命令即可。

需要提供 **`test/urma_mode_parity/results/$JOB/` 整个目录，两端 node0/node1 都要有**。成功运行时重点内容如下（收集命令已包括，无需手工筛选）：

- `*_layout/node*/normal/write_layout.jsonl`：实际WRITE大小、对齐和lane分布。
- `*_micro_r0*/node*/micro/`：`plan.json`、`request.json`、`layout_input.jsonl`、`micro_layout_verified.jsonl`、`runtime.json`、`micro_samples.jsonl`、`micro_summary.jsonl`、`status.json`、`bench.log`。
- 每个任务两端的 `setup.json`、`host.json`、`preflight.json`、`build_manifest.json`，以及设备空闲检查、`counters_before/after.json`、`kernel_before.txt`、`kernel_after_measurements.txt`、`measurement_boundary.json`、`kernel_after.txt`。失败时还需 `preflight_error.txt`（若有）。
- `console/node*/` 和 `build/node*/` 中的上述日志与manifest。无需扩展 `.so`、`objects/`、缓存或共享内存文件。

上轮曾因忽略规则漏掉JSON；当前规则已允许本目录的JSON/JSONL。提交结果前可抽查：

```bash
find "$DEST" -name setup.json -o -name micro_samples.jsonl -o -name write_layout.jsonl
git status --short --untracked-files=all -- "test/urma_mode_parity/results/$JOB"
```

## 7. 汇总与判断

拿到两端完整结果后，在仓库根目录用 Python 汇总；也可在单端先检查本端三轮：

```bash
"$URMA_DIAG_PYTHON" test/urma_mode_parity/summarize_micro.py \
  "test/urma_mode_parity/results/$JOB" \
  --output "test/urma_mode_parity/results/$JOB/summary"
```

应输出 `MICRO_SUMMARY_PASSED`。脚本检查12点齐全、每点50个原始计时/49个有效值、CQE与逻辑退休数量、原始值与均值一致、每端三轮齐全；失败或缺日志会拒绝汇总。结果包括 `micro_runs.csv`、`micro_aggregate.csv`、`micro_comparison.csv`、`micro_geometries.csv`。

按以下方向筛选下一项优化，不能单凭一个测点直接认定根因：

| 观察到的结果 | 优先进一步验证 |
| --- | --- |
| WRITE-only 本身耗时高，窗口4明显优于窗口1 | 大块WRITE、并发深度、目标显存传输路径 |
| 同窗口下加FADD显著变慢 | 通知原子、队列顺序与WRITE/FADD依赖；后续单独设计排序实验 |
| GPU observed 相对 CQE step 明显增加且重复稳定 | 接收GPU观察通知、数据读取、双端进度与可见性相关路径 |
| 微测数据路径较快，而实际Normal仍慢 | 回到GPU命令发布、Proxy发现/反馈、kernel等待与打包的闭环 |

## 8. 计时边界和解释限制

- 发送采用“提交一批→排空这批→下一批”，不是连续补充的峰值带宽测试。窗口1/4表示每lane每批1/4个WRITE；每个WRITE在FADD测点均有一个通知，和完整Normal的WRITE/FADD比例不同。
- 两端同时双向发送。`tx_GBps` 是本端发出的字节数除以本端CPU计时；不是双向相加的带宽，也不是链路速率。仅在相同形状、窗口和方法之间比较。
- `span_ns` 包含各工作线程构造请求、production post/poll、等待和线程调度，线程创建在计时前；`post_ns_sum` 包含transport准备与SDK提交，**不是SDK单独耗时**。各线程累计时间可以重叠，不能加起来当关键路径。
- GPU observer在计时前启动并确认就绪。GPU observed等待的是**本机收到对端数据**，同时本端也在发送；还包含读取并校验完整payload，以及CPU观察GPU完成标志的成本。两端进入计时仍有偏差，两个step之差不是纯单向网络时延或纯GPU可见性时延。
- 每个计时迭代交替使用两套payload模式；两端确认上次完成后才复用接收槽。每点结束检查完整GPU数据和累计原子值，GPU observed则每次观察后立即校验。该诊断的volatile读取/system fence不能独立证明完整Normal kernel的内存模型正确性。
- 接收/发送缓冲使用重复槽，缓存和工作集与真实Normal不同；选取高频形状也不覆盖全部长尾大小、对齐或Combine流量。微测绕过GPU命令ring、Proxy回写和kernel打包，目的在于拆分候选成本，不能用其吞吐直接代替benchmark性能。
- 默认 `all`，不改变paired默认、不删除通知FADD、不放松其fence。GPU观察有超时与校验，失败样本不作性能结论。仅修改实验入口，尚未证明任何新的生产优化有效。
