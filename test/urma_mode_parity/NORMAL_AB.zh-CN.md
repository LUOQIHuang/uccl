# 只测URMA Normal：WRITE完成合并A/B运行步骤

本轮只使用已经协调好的URMA服务器环境，不运行verbs和LL。目标是验证：减少Normal中可以合并处理的WRITE CQE，能否降低Dispatch完整调用时延。尚未在开发设备运行MetaX GPU benchmark，不预先承诺加速。

## 1. 本轮变更与固定条件

同一新模块提供三种Normal完成策略：

| 策略 | WRITE完成通知 | 后续FADD | 用途 |
| --- | --- | --- | --- |
| `all` | 每个WR分别产生CQE | 保留原placement/fence，本地completion order维持原设置 | 本轮原路径基线 |
| `paired` | 仅合格WRITE取消单独成功CQE；其上下文仍保留 | 对同ring、同Jetty、本次提交链中相邻的fenced FADD设置completion order，以它退休两项逻辑请求 | 优化候选 |
| `ordered` | 每个WR仍分别产生CQE | 合格配对FADD同样设置completion order | 可选：区分完成排序开销和CQE减少收益 |

默认仍为 `all`，优化必须显式选择 `paired`。Normal WRITE始终RELAX，FADD的STRONG placement和原有fence不变，8个数据lane＋1控制lane不变。GPU kernel、输入、命令发布顺序、quiet/barrier状态机不变。

非EFA Normal分别发布WRITE和通知ATOMIC。本轮不只匹配WRITE自带的通知字段，而是识别**已准备好的同一次提交链内，相邻且同ring的WRITE→fenced FADD**。不等待攒批，不将单独发布的WRITE延迟到未来某批，不调整链内请求顺序。无法满足条件的WRITE继续产生自己的CQE，因此实际收益取决于配对覆盖率。

后续FADD只是作为本地完成锚点，不合并或改写远端原子值，也不要求它在业务上只通知这一条WRITE。

若两条请求分别对应两个GPU命令，FADD完成后会同时确认两个原始wr_id；不是只确认原子命令。Proxy按“退休的逻辑请求数”回收在途预算，物理CQE数与逻辑退休数分开统计。原先WRITE自带通知的两项内部请求也支持配对；两组都确保这类显式配对不跨SDK提交边界。

显式completion order用于约束本地完成，不能用placement order或fence替代。若SDK部分接收了无CQE的WRITE却拒绝FADD，会在同一数据lane尝试一个有序、fenced的host barrier `FADD +0`作为回收锚点，然后报告原错误并停止；不会发布原通知。如果回收锚点也失败或接收状态不明，则保留上下文及注册，销毁失败关闭，防止过早释放DMA内存。任何该类错误都不作为成功性能样本。

## 2. 测什么、默认运行多少次

固定每rank tokens4096、hidden7168、experts256、topk8、SM24、NVL chunk4；只跑 `test_internode.py`：

- FP8 Dispatch，RDMA chunk32。
- BF16 Dispatch，RDMA chunk32。
- Combine，RDMA chunk12和16，作为对照。
- layout单独记录，不混入Dispatch。

默认共 **8个双机Normal case**，两端按同一顺序执行：

1. diagnostic_all、diagnostic_paired：各一次开启CPU阶段统计，先确认有配对活动、没有新增完成错误。
2. 第1轮基线：all → paired。
3. 第2轮基线：paired → all。
4. 第3轮基线：all → paired。

正式基线关闭CPU统计，全部case都跳过GPU profiler。每个测点预热50次、计时50次，丢弃首个，保存49次有效样本及全部原始值。三轮独立启动程序；最终比较各轮均值的中位数、范围和CV。两端数据分别分析，不合并成6次独立重复。

诊断数据只用于验证CQE、WR数量和完成推进；Normal的插桩扰动此前可达约9%–15%，**不使用diagnostic的耗时判断加速**。本轮不保存GPU trace，也不执行LL或LL排序A/B。

## 3. 两端准备并构建新模块

202：master，141.61.17.202，RANK=0。

204：worker1，141.61.17.204，RANK=1。

先确认两端已经拉取本轮最新代码，且当前处于已通过检查的URMA内核、设备和私有库环境。在各自Bash终端执行；只改RANK：

```bash
cd /home/h00918771/uccl-ep-xpu-integration-new
set -o pipefail
export URMA_DIAG_HOME=/home/h00918771
export URMA_DIAG_PYTHON=/home/h00918771/metax_dev/venv-maca-py312/bin/python
export URMA_DIAG_LIBRARY_DIR=/home/h00918771/metax_dev/urma_after_kernel_20261029/diag/umdk_private_618957c3/lib
export URMA_DIAG_INCLUDE_DIR=/home/h00918771/metax_dev/urma_sdk_private_include

RANK=0  # 204上改为1
BUILD=/home/h00918771/metax_dev/urma_mode_parity_build/urma_normal_completion_v1
JOB=urma_normal_completion_01
RESULTS=/home/h00918771/metax_dev/urma_normal_results/$JOB
CONSOLE=/home/h00918771/metax_dev/urma_normal_console/$JOB
mkdir -p "$CONSOLE"
```

本轮修改了C++传输和完成处理，**必须构建新模块**。不复用phase_v1或LL order_ab模块，不覆盖已安装扩展：

```bash
bash test/urma_mode_parity/run.sh --action build \
  --node-rank "$RANK" --job normal_completion_build --build-dir "$BUILD" \
  2>&1 | tee "$CONSOLE/build_node${RANK}.log"
```

两端均出现 `BUILD_PASSED` 再继续。构建先用实际私有SDK编译并运行CPU模拟测试，再构建MetaX扩展。源码或构建参数变更后使用新BUILD目录；不要改manifest绕过校验。

## 4. 两端各自做检查

以下检查不要求两端同步启动：

```bash
for POLICY in all paired; do
  bash test/urma_mode_parity/run.sh --action check \
    --node-rank "$RANK" --job "normal_check_${POLICY}" --build-dir "$BUILD" \
    --backend urma --mode normal --phase-suite --api-only \
    --normal-completion "$POLICY" \
    2>&1 | tee "$CONSOLE/check_${POLICY}_node${RANK}.log"
done
```

两端两种策略都应出现 `MODE_PARITY_CHECK_PASSED`，打印的runtime中 `normal_completion` 分别为all和paired。硬件、Atomic Request、runpm、设备、私有库及源码校验仍执行；不使用verbs或LL脚本。检查只验证导入、环境和能力，SDK是否接受FADD的completion-order位及真实正确性，还需要下一步实际双机运行确认。

## 5. 正式运行：只执行这一条套件命令

先在202执行，再在204执行同一命令；两端RANK不同，其他参数相同，启动间隔控制在180秒内：

```bash
bash test/urma_mode_parity/run_normal_ab.sh \
  --node-rank "$RANK" --master-addr 141.61.17.202 \
  --build-dir "$BUILD" --job "$JOB" --result-root "$RESULTS" \
  2>&1 | tee "$CONSOLE/suite_node${RANK}.log"
```

不需要另外运行 `run_order_ab.sh`、`run_stages.sh` 或旧的Normal参数扫描。默认控制端口31280、torchrun端口31281；如被占用，两端同时添加相同的 `--port`。

本套件单case超时设为600秒，避免异常占用过长；有必要时两端可一致添加 `--case-timeout` 调整。任一端失败后，另一端可能仍在等待退出或超时，不要只保留先失败一端的日志。

每个case开始打印日志路径，结束应为 `CASE_END normal rc=0`；最后两端均出现 `NORMAL_AB_ALL_PASSED`。默认先做诊断，如果 `pair_activity=false`，说明合格配对没有被实际触发，套件立即停止，不浪费后续三轮窗口；保留结果反馈即可。

发生正确性、CQ、SDK提交、退出或配对活动检查失败会停止。不要删除失败日志，不要自动清理其他同事的GPU进程或共享内存。同一个JOB/node目录不允许覆盖，重跑使用新JOB。

资源极紧张时，两端可以一致添加 `--skip-diagnostics`，仅运行6个正式case，但缺少CQE减少与覆盖率的直接证据，首次运行建议使用默认完整套件。`--repeats 1`仅用于短窗口跑通检查，汇总时相应传 `--expected-repeats 1`；不能替代三轮结论。

## 6. 收集、汇总、需要提供哪些日志

两端完成后，各自执行一次：

```bash
bash test/urma_mode_parity/run.sh --action collect --collect-suite \
  --node-rank "$RANK" --job "$JOB" --output "$RESULTS"

"$URMA_DIAG_PYTHON" test/urma_mode_parity/summarize_normal_ab.py \
  "test/urma_mode_parity/results/$JOB" \
  --output "test/urma_mode_parity/results/$JOB/summary_node${RANK}"

"$URMA_DIAG_PYTHON" test/urma_mode_parity/summarize_phases.py \
  "test/urma_mode_parity/results/$JOB" \
  --output "test/urma_mode_parity/results/$JOB/stages_node${RANK}"
```

collect只是筛选复制到仓库，不执行网络传输。两端结果结构为：

```text
test/urma_mode_parity/results/$JOB/
  ${JOB}_diagnostic_all/node0|node1/normal/
  ${JOB}_diagnostic_paired/node0|node1/normal/
  ${JOB}_r01_baseline_all/node0|node1/normal/
  ${JOB}_r01_baseline_paired/node0|node1/normal/
  ... r02、r03 ...
  summary_node0|summary_node1/
  stages_node0|stages_node1/
```

汇总程序拒绝失败、缺失case和不完整采样；默认要求每端每个测点各有3轮all和paired正式样本。输出：

- `normal_runs.csv`：逐case分布，诊断标记单独保存。
- `normal_comparison.csv`：三轮各自均值的中位数、范围、paired相对all的变化；负数表示时延降低。
- `normal_cqe_counts.csv`：每次调用的WRITE数/字节、FADD数、可配对数、取消WRITE CQE数、实际CQE数、逻辑退休数、SDK CPU范围和恢复锚点数。
- `stages_node*/`：补充CPU阶段和各策略自身的插桩扰动。两种完成策略不会合并为同一组。

阶段窗口的计数可能有少量跨边界操作，不要求“CQE数＋取消数＝退休数”逐窗口严格相等；主要比较每次调用均值、配对覆盖率和明确的CQE减少比例。配对WRITE等待锚点的抽样另列为 `paired_write_to_notify_cqe_sampled`，不混入独立WRITE完成抽样。

需要提供以下内容，**尤其不要漏node1**：

1. `test/urma_mode_parity/results/$JOB/` 中完整的两端筛选结果和汇总。包含每个case的 `bench.log`、`status.json`、`api_samples.jsonl`、`sample_summary.jsonl`、`metrics.jsonl`、`phases.jsonl`、`runtime.json`；上层的 `setup.json`、`host.json`、`preflight.json` 和 `build_manifest.json`。
2. 每个case的前后计数器、`kernel_before.txt`、`kernel_after_measurements.txt`、`measurement_boundary.json`、`kernel_after.txt` 和硬件预检查记录。销毁前快照后有双端barrier，避免另一端提前退出影响判断。
3. `$CONSOLE` 下两端build、check、suite及每个case的console日志；以及 `$BUILD/build.log`。可将这些小文件放在该JOB的 `console/` 目录一起提供。

不需要上传 `.so`、objects、venv、cache、tmpdir；collect会排除。失败时执行collect保留已产生文件即可，汇总可能因缺失/失败而拒绝，这是预期行为；不要为了生成汇总删除失败case。

## 7. 可选ordered控制组：当前不必额外执行

paired为了安全退休WRITE，给后续FADD增加completion-order约束。如果paired比all慢或收益不明显，需要区分“CQE减少无收益”与“完成排序约束抵消收益”。届时用新JOB，在套件命令增加 `--include-ordered-control`：三轮轮换all、ordered、paired，另各一次诊断，共12个Normal case。两端都要加此参数。

`ordered`和paired具有相同完成排序约束，区别在是否保留WRITE成功CQE，以及相应的上下文退休方式；`normal_comparison.csv` 额外输出ordered相对all、paired相对ordered的变化。本轮默认的all/paired比较衡量优化净效果，不把全部差异都称为CQE数量的因果收益。

## 8. 我们如何判断是否值得继续

- 正确性通过，WRITE字节数、FADD工作量相当，paired实际CQE减少，三轮Dispatch稳定变快：继续验证并保留候选，尚不自动修改默认策略。
- 配对覆盖率很低：优先分析GPU命令到达/提交批次边界；不通过等待攒批强行增加覆盖率。
- CQE明显减少但时延无改善：完成处理不是主要瓶颈，必要时加ordered控制，然后转向GPU目标WRITE/可见性路径，不继续盲调CQE。
- 任何恢复锚点、错误CQE或内核异常：先分析失败，性能数据不作为优化成功证据。

本地验证覆盖真实transport和Proxy完成/预算代码的CPU模拟、实际SDK字段编译、部分接收、错误CQE、槽位回收和两条GPU命令确认；参考SDK模拟不等于私有SDK ABI或真实GPU正确性验证，后者由本轮服务器运行确认。
