# 真实Normal Dispatch：首条大块WRITE错峰因果实验

本轮目标是把微测线索连接回完整Dispatch：在输入、WRITE/FADD数量、请求顺序和完成语义不变的情况下，轻微改变一端的首批发送时机，观察完整调用是否有净收益。只使用URMA环境，不运行LL或verbs，不再增加内存类型/对齐微测矩阵。

## 1. 两组只改变什么

使用真正的 `test_internode.py`、Buffer/Proxy/UrmaTransport，固定tokens4096、hidden7168、experts256、topk8、SM24、NVL chunk4、Dispatch chunk32。保留原有24组正确性检查、layout、FP8/BF16 Dispatch和Combine chunk12/16五个计时点。

| 参数 | d0基线 | d200错峰 |
| --- | --- | --- |
| 202/node0 | 无注入延迟 | 无注入延迟 |
| 204/node1 | 无注入延迟 | 每个Proxy在本次Dispatch首条≥4096B WRITE准备阶段暂停200µs |
| 后续请求 | 正常推进 | 正常推进，不重复暂停 |
| 数据/通知 | Normal WRITE RELAX、FADD STRONG＋原fence、completion all | 相同 |
| layout与Combine | 无注入 | 无注入，作为对照 |

暂停发生在实际生产传输路径，**计入完整Dispatch的CUDA-event和host调用耗时，不从结果中扣除**。操作系统sleep可能超时调度，实际暂停长度按Proxy逐次记录。它不是修改GPU kernel或绕开原子通知，也不是承诺有效的生产优化。

延迟只改变首批发布时机，不能保证两个方向的整个数据传输不再重叠；它也会改变该Proxy的CPU进度服务节奏。若观察到改善，首先说明真实Dispatch对发布/进度节奏敏感，不能仅凭改善就认定纯硬件双向竞争是根因。

## 2. 计时协议与任务数量

为了让暂停只对应一个Dispatch，所有计时点改为逐次同步：前次GPU调用结束→双端barrier→启动本次计时→执行完整调用→等待本次GPU完成→结束计时。每次注入在调用前开启、GPU同步后关闭，Combine和layout不启用注入。基线和错峰使用完全相同的隔离协议。

这个协议与此前连续排队计时不同，因此**只比较本轮d0/d200，不直接拿新绝对时延与历史3.703/4.138ms等数字判断加速**。barrier和日志写入在单次计时外，但可能改变缓存和相邻调用节奏。

默认8个双端任务：

1. `diagnostic_d0`、`diagnostic_d200`：预热5次、计时5次；开启CPU阶段统计、WRITE形状采集。每个Dispatch点只对首个计时调用记录详细SDK提交时间线。该样本本来也会丢弃；诊断数据不用于性能结论。
2. 三轮正式A/B：r01为d0→d200，r02为d200→d0，r03为d0→d200。每点预热20次、计时50次、丢弃首个，保留49个有效样本。关闭详细追踪和形状采集，保留每次4个Proxy首条大块WRITE的到达/释放时间作为注入生效证据。

正式结果逐样本匹配两端同一个调用，计算两端CUDA时延较大值及host耗时较大值，再计算每轮均值和三轮中位数；同时保留两端各自均值。不把“两端各快一点”或“202快、204慢”当成整体改善。

注意：两端GPU event的开始时刻并非同一个跨机时钟，较大值是保守的配对指标，不是全局同步时间戳测得的真实makespan。SDK时间线使用本机单调时钟，只能按本机trial起点归一化，不直接相减两台机器的绝对时间戳。

## 3. 两端环境设置

先更新本轮分支。202使用RANK0，204使用RANK1，在各自Bash终端执行：

```bash
cd /home/h00918771/uccl-ep-xpu-integration-new
set -euo pipefail
export URMA_DIAG_HOME=/home/h00918771
export URMA_DIAG_PYTHON=/home/h00918771/metax_dev/venv-maca-py312/bin/python
export URMA_DIAG_LIBRARY_DIR=/home/h00918771/metax_dev/urma_after_kernel_20261029/diag/umdk_private_618957c3/lib
export URMA_DIAG_INCLUDE_DIR=/home/h00918771/metax_dev/urma_sdk_private_include

RANK=0  # 204改成1
BUILD=/home/h00918771/metax_dev/urma_mode_parity_build/urma_dispatch_trial_v1
JOB=urma_dispatch_trial_01
RESULTS=/home/h00918771/metax_dev/urma_dispatch_trial_results/$JOB
CONSOLE=/home/h00918771/metax_dev/urma_dispatch_trial_console/$JOB
mkdir -p "$CONSOLE"
```

保留上一轮split_06成功运行时的内核、Atomic Request、runpm、私有库、设备及额外运行库设置。默认GPU0、`udmac0d1e2`、`enp33s0`。本轮不同时切换CPU调度策略，沿用完整Normal测试入口的既有设置（`UCCL_EP_DISABLE_CPU_AFFINITY=1`）；host.json记录进程允许CPU集合。不要因为split的原生微测绑在192–195，就把它误认为当前真实Proxy也已固定在这些核上。

## 4. 两端构建并检查

新增了实际传输路径的诊断开关，**必须在新BUILD目录重新编译，不能复用split_v2**：

```bash
bash test/urma_mode_parity/run.sh --action build \
  --node-rank "$RANK" --job dispatch_trial_build --build-dir "$BUILD" \
  2>&1 | tee "$CONSOLE/build_node${RANK}.log"

bash test/urma_mode_parity/run.sh --action check \
  --node-rank "$RANK" --job dispatch_trial_check --build-dir "$BUILD" \
  --backend urma --mode normal --phase-suite --api-only --dispatch-trial \
  2>&1 | tee "$CONSOLE/check_node${RANK}.log"
```

两端都应看到 `BUILD_PASSED`、`MODE_PARITY_CHECK_PASSED`，runtime中 `dispatch_trial: true`、`normal_completion: all`。构建包含默认关闭、一次性延迟、并发记录及生产传输模拟边界测试；开发机无法完成MetaX实机性能验收。

构建失败先保留 `host_tests.log`、`build.log`、`build_request.json` 和终端日志。源码或SDK改变后换新BUILD目录，不手改manifest。若重跑任务，换新JOB，不覆盖已有结果。

## 5. 双端只运行这一条套件命令

先在202启动，随后180秒内在204执行；两端JOB、端口和参数相同，仅RANK不同：

```bash
bash test/urma_mode_parity/run_dispatch_trial.sh \
  --node-rank "$RANK" --master-addr 141.61.17.202 \
  --job "$JOB" --build-dir "$BUILD" \
  --port 31680 --result-root "$RESULTS" \
  2>&1 | tee "$CONSOLE/suite_node${RANK}.log"
```

31680用于任务握手，31681用于torchrun，均需可用。内部自动跑8个完整Normal任务。每个成功任务应有 `CASE_END normal rc=0`、24组正确性检查、5个原始采样点及退出确认，最终两端出现 `DISPATCH_TRIAL_ALL_PASSED`。

任何错误立即停下收集两端日志，不继续后续任务、不根据退出成功忽略内核AE。特别是重现先前的绑定/完成错误时，需要完整原始任务目录中的当次kernel快照，而不是只提供开机dmesg。

本轮补上非成功完成记录的 `[URMA_COMPLETION_ERROR]` 日志，包含原始status、Proxy、token、wr_id和WRITE/ATOMIC分类；非零完成状态与等待超时是不同错误，不能混为一类。

## 6. 两端各自收集

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

如果构建失败且还没有任务，collect会提示没有结果，此时从DEST起整理构建/终端日志即可。

需要提供 **`test/urma_mode_parity/results/$JOB/` 完整目录，两端node0/node1都要有**。收集命令已包括：

- 每个任务的 `setup.json`、`host.json`、`preflight.json`、`build_manifest.json`，保留转换后的真实benchmark源码便于审核。
- `normal/dispatch_trial_samples.jsonl`：全部50次原始CUDA、host耗时和首条payload时间，含被丢弃的首个样本。
- 诊断任务的 `dispatch_trial_trace.jsonl`、`write_layout.jsonl`、`phases.jsonl`：核对大小、地址对齐、lane、WRITE/FADD数量和各Proxy提交时机。详细提交追踪最多4096条，溢出不能用于判断。
- `api_samples.jsonl`、`sample_summary.jsonl`、`metrics.jsonl`、`bench.log`、`status.json`。
- 当次 `kernel_before.txt`、`kernel_after_measurements.txt`、`measurement_boundary.json`、`kernel_after.txt`、设备空闲检查、`counters_before/after.json`、失败时的 `preflight_error.txt`（若有）。
- `console/node*/` 与 `build/node*/` 的日志及manifest。不需要扩展 `.so`、objects、cache或共享内存文件。

## 7. 双端数据齐全后汇总

```bash
"$URMA_DIAG_PYTHON" test/urma_mode_parity/summarize_dispatch_trial.py \
  "test/urma_mode_parity/results/$JOB" \
  --output "test/urma_mode_parity/results/$JOB/summary"
```

此脚本要求两端齐全；每端单独跑会明确拒绝。它核对三轮A/B、每点50个原始样本和49个有效值、双端元数据、实际注入长度、4个Proxy均走到首条大块WRITE，以及独立诊断任务的SDK时间线。输出：

- `trial_runs.csv`：每轮、每点、每端时延及配对较大值分布。
- `trial_comparison.csv`：d0/d200三轮中位数、范围、CUDA与host变化，负百分比代表更快。
- `trial_diagnostics.csv`：诊断样本的实际暂停、提交数量、WRITE字节、FADD数量及本机时间线范围。完整lane和形状分布在JSONL中。

诊断耗时不加入性能统计；不能把 `sdk_post` 范围当纯SDK耗时，该诊断范围还包含提交追踪开销。不要使用之前normal_ab或phase汇总脚本混合这些隔离计时结果。

## 8. 如何决定下一步

只有d200在三轮中呈现稳定的完整Dispatch净收益、两端配对较大值和host指标均支持、正确性与内核边界可接受、诊断确认只是时序变化时，才考虑把“首批发布/进度节奏”作为生产优化方向。最终还需在原连续计时协议中验证，不能把固定sleep直接作为默认优化上线。

若仅202变快而204或配对较大值变慢，属于代价转移。若CUDA变快而host明显变慢，也不能宣称完整收益。若200µs错峰没有改善，只说明本次最小干预无净收益，不能据此排除所有双向竞争。

本轮不改变数据路由、token数、算子语义、fence或lane数；没有同协议verbs数据，因此仍不能直接证明它解释了历史0.435/0.931ms的全部Dispatch差距。本轮的目的，是让微测线索接受真实Dispatch的检验，再决定是否继续该方向。
