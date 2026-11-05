# URMA下一轮：LL WRITE排序A/B与Normal纯基线

本轮针对 `urma_phase_v1` 的结果缩小变量。先补齐已存在的node1及r01_baseline日志，不要求为补文件重新占用GPU。下面的运行仅在**下一次已协调到URMA内核和设备的窗口**执行；现在只有RDMA资源时，不执行URMA guard或切内核。

## 1. 改了什么

- 默认行为不变：Normal WRITE=RELAX，LL WRITE=STRONG，FADD=STRONG及原请求fence。
- 新增 `--ll-write-order strong|relax` 诊断参数，只改变LL WRITE的 `place_order`。同一模块对照；不关闭FADD fence，不改变lane、WR分组、内存分配、quiet/barrier、原子值和正确性矩阵。
- 新增 `--api-only`：跳过Kineto，保留完整调用的50次预热、50次计时（49次有效样本）。它与 `--save-traces` 互斥。
- `--stage-profile` 单独诊断，不混入正式基线；本轮无需再保存GPU trace。
- 最后一次测量完成后、benchmark销毁前，生成 `kernel_after_measurements.txt` 和 `measurement_boundary.json`。与case前后内核日志对照，判断TP AE是否主要发生在销毁阶段。读取失败会明确记录rc，不能据此判断事件不存在。
- 诊断指纹排除results/cache等输出目录，避免两端各自保留的旧benchmark副本导致脚本指纹误判；实际输入源码、构建和测试脚本仍严格核对。

RELAX仅为待验证候选。通过有限正确性测试不等于已经证明所有可见性/排序语义；默认策略不会因运行此脚本而永久改变。每个进程退出后结束该次策略。

## 2. 两端准备并重建

202用RANK=0，204用RANK=1；下面在各自Bash终端执行。两端SDK、代码、参数和私有库需匹配。旧phase_v1模块缺少开关能力，新开关检查会拒绝它。

```bash
cd /home/h00918771/uccl-ep-xpu-integration-new
set -o pipefail
export URMA_DIAG_HOME=/home/h00918771
export URMA_DIAG_PYTHON=/home/h00918771/metax_dev/venv-maca-py312/bin/python
export URMA_DIAG_LIBRARY_DIR=/home/h00918771/metax_dev/urma_after_kernel_20261029/diag/umdk_private_618957c3/lib
export URMA_DIAG_INCLUDE_DIR=/home/h00918771/metax_dev/urma_sdk_private_include
RANK=0  # 204改成1
BUILD=/home/h00918771/metax_dev/urma_mode_parity_build/urma_order_ab_v1
JOB=urma_order_ab_01
ORDER_RESULTS=/home/h00918771/metax_dev/urma_order_results/$JOB
CONSOLE=/home/h00918771/metax_dev/urma_order_console/$JOB
mkdir -p "$CONSOLE"

bash test/urma_mode_parity/run.sh --action build \
  --node-rank "$RANK" --job order_build --build-dir "$BUILD" \
  2>&1 | tee "$CONSOLE/build_node${RANK}.log"
```

构建读取实际私有SDK，并运行CPU模拟检查后编译；出现 `BUILD_PASSED` 再继续。开发端没有MetaX GPU，只能做CPU模拟，真实ABI和性能需要以下服务器测试确认。

两端分别检查两个策略，检查不需要同步启动：

```bash
for ORDER in strong relax; do
  bash test/urma_mode_parity/run.sh --action check \
    --node-rank "$RANK" --job "order_check_${ORDER}" --build-dir "$BUILD" \
    --phase-suite --api-only --ll-write-order "$ORDER" \
    2>&1 | tee "$CONSOLE/check_${ORDER}_node${RANK}.log"
done
```

检查打印的runtime中 `ll_write_order` 应分别为strong和relax，并出现 `MODE_PARITY_CHECK_PASSED`。原有URMA硬件、runpm、Atomic和私有库检查仍执行；使用已经协调好的配置，不跳过硬件检查。

## 3. 第一优先级：LL交替A/B

两端同步执行同一命令，先202后204，启动间隔小于180秒：

```bash
bash test/urma_mode_parity/run_order_ab.sh \
  --node-rank "$RANK" --master-addr 141.61.17.202 \
  --build-dir "$BUILD" --job "$JOB" --result-root "$ORDER_RESULTS" \
  2>&1 | tee "$CONSOLE/suite_node${RANK}.log"
```

默认只跑LL，tokens=128、hidden=7168、experts=256、topk=8。共8个双机case：

1. r01：strong baseline → relax baseline。
2. r02：relax baseline → strong baseline。
3. r03：strong baseline → relax baseline。
4. diagnostic strong、diagnostic relax：各一次启用CPU阶段统计。

两种LL工作负载均保存：无hook完整Dispatch+Combine是主指标；立即执行receive hook的完整调用作为诊断对照，不插入大GEMM。每种排序有三轮独立基线，计时都不启动GPU profiler。策略写入两端握手指纹、runtime和transport日志。

资源紧张可两端同时追加 `--skip-diagnostics`，先做6个基线case。`--repeats 1`可用于短窗口验证是否能跑通，但一轮不足以替代三轮性能结论。可用两端相同的 `--port` 改控制端口（默认31280，torchrun使用31281）。

最终两端应出现 `ORDER_AB_ALL_PASSED`。任何正确性、CQ、启动或退出检查失败都会停止后续case；保留失败目录。不要用新策略覆盖原结果，不自动处理其他任务的共享barrier。

## 4. 第二优先级：Normal纯基线

这一步修正旧URMA phase-suite“先GPU profiler，再测API”的顺序差异。使用同一新模块，关闭CPU统计和GPU profiler；Normal WRITE仍为默认RELAX。三轮×四个固定点，与新的verbs基线对比，不再大扫参数。

若窗口足够，两端继续同步执行：

```bash
for REP in 01 02 03; do
  bash test/urma_mode_parity/run.sh --action run --backend urma \
    --node-rank "$RANK" --master-addr 141.61.17.202 \
    --build-dir "$BUILD" --job "${JOB}_normal_r${REP}" --output "$ORDER_RESULTS" \
    --mode normal --phase-suite --api-only --ll-write-order default \
    --warmups 50 --samples 50 \
    2>&1 | tee "$CONSOLE/normal_r${REP}_node${RANK}.log"
done
```

Normal默认shape和四个点仍为tokens4096、hidden7168、experts256、topk8、SM24、NVLchunk4；FP8/BF16 Dispatch chunk32、Combine chunk12/16。

**API-only下原benchmark的tuning字段含非零占位notify值，不是GPU时延。** 以 `api_samples.jsonl` 和 `sample_summary.jsonl` 为完整调用指标，`MODE_PARITY_API_ONLY_POINT` 标明没有实际GPU profile。不得将其占位kernel值拿来分析阶段。

## 5. 收集与汇总

每端执行一次，按JOB前缀收集该轮所有LL和可选Normal结果；不需要逐job传命令：

```bash
bash test/urma_mode_parity/run.sh --action collect --collect-suite \
  --node-rank "$RANK" --job "$JOB" --output "$ORDER_RESULTS"
```

结果进入 `test/urma_mode_parity/results/$JOB/CASE_JOB/node0|node1/`，不复制二进制、objects、cache或tmpdir。两端目录合并后可以运行汇总；每端也可先分别生成summary_node0/1：

```bash
"$URMA_DIAG_PYTHON" test/urma_mode_parity/summarize_order_ab.py \
  "test/urma_mode_parity/results/$JOB" \
  --output "test/urma_mode_parity/results/$JOB/summary_node${RANK}"

"$URMA_DIAG_PYTHON" test/urma_mode_parity/summarize_phases.py \
  "test/urma_mode_parity/results/$JOB" \
  --output "test/urma_mode_parity/results/$JOB/stages_node${RANK}"
```

第一个程序生成 `order_runs.csv`、`order_comparison.csv`，只用关闭插桩的各轮均值比较STRONG/RELAX；两端、内核、build、输入、SDK库、hook工作负载均分组。第二个程序输出各策略的CPU阶段及各自插桩扰动；已修正为不会把两种排序或API-only/旧profiler模式混在同一组。若只做Normal，没有两种LL排序，不运行第一个程序。

需要提供：完整收集结果中的两端目录、原始采样和summary、phases/metrics、status/config/runtime/host、构建manifest、三份内核快照及measurement_boundary、前后计数器；另加本轮console和构建 `build.log`。不需要传 `.so` 或环境目录。失败时同样保留已产生的两端结果。这里不包含网络传输步骤。

## 6. 如何决定下一步

- RELAX稳定降低无hook总时延，并且WRITE/FADD完成抽样和receive等待同步降低：支持排序/依赖是重要因素，再扩大正确性和语义验证；不能直接将RELAX改为生产默认。
- RELAX影响很小：将GPU目标FADD、WRITE+FADD依赖、远端可见性独立微测；不继续盲目改Normal分组扫描或关闭fence。
- kernel_after_measurements没有新AE，而kernel_after在unmap后新增：更支持销毁阶段告警；若计时结束前已出现新AE，优先联合驱动排查运行期事件。
- Normal API-only仍稳定慢约10%–14%：下一轮针对实际WRITE长度、目标显存写入/可见性与完成推进做微测，保留固定shape，不同时改多个参数。
