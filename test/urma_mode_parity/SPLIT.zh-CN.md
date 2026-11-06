# URMA Normal：单向WRITE与GPU观察成本分解

本轮继续只用URMA内核和私有库，不切换verbs，不运行LL，不修改生产默认排序或完成策略。上一轮结果显示发送方向/端点差异明显，GPU观察额外耗时中又包含完整字节扫描，因此本轮先把这两件事拆开。

## 1. 运行哪些测试

复用 `urma_normal_data_01` 已采集的消息大小和对齐，不再运行完整Normal benchmark。

默认 **6个双端任务**：三轮独立启动，每轮分别用1、4个发送线程；第二轮交换线程配置的执行顺序。202和204始终都要启动脚本，包括它在某个测点只接收的时候。

| 配置 | 每种FP8/BF16形状的测点 | 每次WRITE数量 |
| --- | --- | --- |
| 1线程 | WRITE-only双向、202→204、204→202 | 每线程128个，共128个 |
| 4线程 | 同上三个WRITE-only测点 | 每线程32个，共128个 |
| 4线程 | WRITE＋FADD：仅本地CQE、GPU只等通知、GPU等通知后扫描payload | 每线程8个WRITE，共32个，每WRITE一个FADD |
| 4线程 | 本地GPU扫描校准，不发URMA请求 | 扫描与上述observer相同的32个接收槽 |

1线程每轮6点，4线程每轮14点；每点预热20次、计时50次、丢弃首个计时，保留49个有效样本。每端总计3000个原始计时。最终每端、每线程配置、每测点分别汇总三轮均值的中位数和范围，不混合两个节点。

WRITE-only固定每lane窗口4，仍是提交一批后排空完成，不是持续补充的峰值带宽测试。每线程8条数据lane，线程数变化也会改变总lane数、接收槽数量和工作集；虽然发送量相同，不能把差异全部解释成CPU线程扩展能力。

本轮加入逐次接收校验及固定亲和性，会改变计时区间之间的缓存状态和节奏。与上一轮的绝对吞吐差异也不能只归因于绑核，应优先比较本轮内部对照。

单向WRITE-only的接收端不在计时区间内等待GPU通知，其短耗时只是接收端空操作，**不能作为网络时延或吞吐**。发送端和接收端的计时均退出后，接收端每次都做完整GPU数据检查；双方交换校验结果后才复用接收槽。最终CSV明确标记 `inactive_receiver` 并留空吞吐。

## 2. 两端准备

202（141.61.17.202）为RANK0，204（141.61.17.204）为RANK1。两端更新本轮代码后，在各自Bash终端执行，仅修改RANK：

```bash
cd /home/h00918771/uccl-ep-xpu-integration-new
set -euo pipefail
export URMA_DIAG_HOME=/home/h00918771
export URMA_DIAG_PYTHON=/home/h00918771/metax_dev/venv-maca-py312/bin/python
export URMA_DIAG_LIBRARY_DIR=/home/h00918771/metax_dev/urma_after_kernel_20261029/diag/umdk_private_618957c3/lib
export URMA_DIAG_INCLUDE_DIR=/home/h00918771/metax_dev/urma_sdk_private_include

RANK=0  # 204改为1
BUILD=/home/h00918771/metax_dev/urma_mode_parity_build/urma_normal_split_v2
JOB=urma_normal_split_01
RESULTS=/home/h00918771/metax_dev/urma_split_results/$JOB
CONSOLE=/home/h00918771/metax_dev/urma_split_console/$JOB
LAYOUT="$PWD/test/urma_mode_parity/results/urma_normal_data_01/urma_normal_data_01_layout/node${RANK}/normal/write_layout.jsonl"
mkdir -p "$CONSOLE"
test -s "$LAYOUT"
```

LAYOUT默认直接使用已提交到仓库的上一轮本端文件。若该文件缺失，可改成上一轮原始结果中的同一个 `write_layout.jsonl`；不要使用summary CSV、微测的 `micro_layout_verified.jsonl` 或自行编造形状。两端会校验本端采集完整性，并实际采用node0分享的计划。

保留已经通过检查的URMA内核、runpm、PCIe Atomic Request、设备和额外运行库设置。默认仍是GPU0、`udmac0d1e2`、`enp33s0`。无需手动清理共享内存或修改设备状态。

## 3. 两端重新编译并检查

本轮新增C++控制参数和CUDA观察/扫描路径，**必须构建新模块**，不能复用 `urma_normal_data_v1`。

```bash
bash test/urma_mode_parity/run.sh --action build \
  --node-rank "$RANK" --job split_build --build-dir "$BUILD" \
  2>&1 | tee "$CONSOLE/build_node${RANK}.log"

bash test/urma_mode_parity/run.sh --action check \
  --node-rank "$RANK" --job split_check --build-dir "$BUILD" \
  --backend urma --mode normal --micro-suite split --normal-completion all \
  2>&1 | tee "$CONSOLE/check_node${RANK}.log"
```

两端均应看到 `BUILD_PASSED`、`MODE_PARITY_CHECK_PASSED`，runtime应有 `micro_version: 2`。检查无需同时开始。旧模块将被拒绝；源码/SDK/编译选项变化需换新BUILD目录，不要修改manifest绕过校验。

开发机已用生产传输和微测C++代码跑模拟GPU/SDK边界测试，覆盖单向/双向、1/4线程、绑核和通知分流；仍需要服务器真实MetaX编译及双机验收。构建失败先保留日志，不继续跑套件。

## 4. CPU亲和性规则

默认 `--micro-cpus auto`：优先从GPU PCI设备报告的NUMA节点，选出4个可用CPU作为工作线程核，再选一个同NUMA的独立CPU运行Python控制进程。1线程固定使用同一四核集合中的第一个，4线程分别使用四个核。

GPU NUMA未知或可用CPU不足时，退到第一个拥有至少5个可用CPU的NUMA节点，明确记录fallback原因；**这不代表它一定靠近GPU/URMA设备**。选核与进程原始允许集合、GPU NUMA信息记录在每个任务的 `node*/affinity.json`，两端交换结果另存 `micro/affinity_pair.json`。C++工作线程在每个计时区间前设置并核对单核亲和性，失败就停止。

如需按同事分配的CPU运行，两端分别查看本机拓扑和进程允许集合：

```bash
lscpu -e=CPU,NODE,SOCKET,ONLINE
cat /sys/bus/pci/devices/0000:a1:00.0/numa_node
taskset -pc $$
```

选择同一NUMA节点内4个可用、互不重复的CPU，且该节点必须另有至少1个可用核给控制进程。将下一节命令里的 `--micro-cpus auto` 改成例如 `--micro-cpus 96,97,98,99`；这里的数字只是格式示例，必须按实际资源分配选择。两端CPU编号允许不同，三轮内各自保持相同。

不做CPU绑核开关A/B，也不据此宣称原Normal默认亲和性已经被优化；本轮只是固定实验条件。

## 5. 双端运行

202先启动，随后180秒内在204执行同一命令。两端RANK不同，JOB、端口、线程计划必须相同：

```bash
bash test/urma_mode_parity/run_split.sh \
  --node-rank "$RANK" --master-addr 141.61.17.202 \
  --job "$JOB" --build-dir "$BUILD" --layout-file "$LAYOUT" \
  --micro-cpus auto --port 31580 --result-root "$RESULTS" \
  2>&1 | tee "$CONSOLE/suite_node${RANK}.log"
```

31580用于任务握手，31581用于CPU控制连接，均需可用。预热、计时区间外的握手、校验和线程绑核不计入发送耗时。

各 `r01_t1/r02_t1/r03_t1` 任务应出现6条 `URMA_MICRO_POINT`，各t4任务14条；每个任务最终 `URMA_MICRO_ALL_PASSED`，整个套件最终 `NORMAL_SPLIT_ALL_PASSED`。任务失败会停止，不跳过校验、不自动继续后续轮次。失败后先收集两端日志；重跑应使用新JOB，避免覆盖已有结果。

## 6. 收集与提供日志

两端无论成功失败都执行收集，保留本端的全部任务：

```bash
bash test/urma_mode_parity/run.sh --action collect --collect-suite \
  --node-rank "$RANK" --job "$JOB" --build-dir "$BUILD" --output "$RESULTS"

DEST="$PWD/test/urma_mode_parity/results/$JOB"
mkdir -p "$DEST/console/node${RANK}" "$DEST/build/node${RANK}"
cp -v "$CONSOLE"/*.log "$DEST/console/node${RANK}/"
for NAME in host_tests.log build.log build_request.json manifest.json; do
  if [ -f "$BUILD/$NAME" ]; then
    cp -v "$BUILD/$NAME" "$DEST/build/node${RANK}/"
  fi
done
```

若构建就失败、尚无任务，collect会提示没有结果，此时直接执行DEST起的日志整理命令。

需要提供 **`test/urma_mode_parity/results/$JOB/` 整个目录，包含两端node0/node1**。不要只上传summary或终端成功标志。收集包括：

- 每个任务的 `setup.json`、`host.json`、`affinity.json`、`preflight.json`、`build_manifest.json`。
- `micro/` 下的 `request.json`、`plan.json`、`affinity_pair.json`、`layout_input.jsonl`、`micro_layout_verified.jsonl`、`runtime.json`、`micro_samples.jsonl`、`micro_summary.jsonl`、`status.json` 和 `bench.log`。
- 设备空闲检查、`counters_before/after.json`、`kernel_before.txt`、`kernel_after_measurements.txt`、`measurement_boundary.json`、`kernel_after.txt`，以及失败时的 `preflight_error.txt`（若有）。
- `console/node*/` 和 `build/node*/` 下的构建、检查、套件日志与manifest。不需要 `.so`、objects、cache、共享内存文件。

## 7. 汇总

收集本端三轮后即可先在本端汇总；拿到双端完整目录再运行同一命令可同时检查两端：

```bash
"$URMA_DIAG_PYTHON" test/urma_mode_parity/summarize_split.py \
  "test/urma_mode_parity/results/$JOB" \
  --output "test/urma_mode_parity/results/$JOB/summary"
```

应出现 `SPLIT_SUMMARY_PASSED`。脚本核对每端两种线程配置、三轮、全部测点、原始样本、实际WRITE数量/对齐、CQE/逻辑退休数量、均值以及亲和性元数据。生成 `split_runs.csv`、`split_aggregate.csv`，失败或缺样本会拒绝汇总。

读CSV时：

- `network_host_span`：发送端的CPU构造/提交/轮询完成区间；observer模式再包含本机等待接收GPU通知的部分。
- `inactive_receiver`：单向WRITE-only的接收端，不看该行吞吐或耗时作网络结论。
- `local_gpu_scan`：本地校准。`gpu_median_run_mean_us`为GPU event扫描kernel耗时；`median_run_mean_us`为其host启动和同步区间。不能混用这两种时钟。

优先比较同线程配置下单向与双向，再比较相同方向1/4线程；observer只在同一节点、同一形状下比较CQE/flag/payload；最后参考本地扫描GPU时间判断完整字节校验占了多少成本。

## 8. 解释边界

仅等待通知的GPU路径仍保留原子通知和system fence；完整payload校验移到计时区间外，不能以省略正确性检查换取更好的数字。WRITE始终Normal RELAX，FADD始终STRONG＋原有fence，完成策略all。未修改原有Normal/LL benchmark默认行为。

observer仍为双向测试：本端发完后等待本机收到对端通知，不是单向网络时延。flag与CQE之差还包含GPU观察、host标志反馈和双端起步偏差；payload与flag的差也不是严格可相减的独立阶段，二者GPU读写和争用状态可能不同。

本地扫描先用D2D拷贝填好相同接收槽，再计时同样的volatile字节校验；填充和内存分配在计时外，缓存可能较热，且没有远端DMA/常驻等待kernel。它是校准值，不能直接从payload观察耗时里相减后命名为纯可见性延迟。

本轮仍使用上一轮0/0代表对齐，不覆盖所有实际非零地址偏移；没有同形状verbs微测，不据此直接声称URMA已追平/落后多少。内核AE在测量前/后分别保留，退出成功不等于可以忽略清理阶段AE。
