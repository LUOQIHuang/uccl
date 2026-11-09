# URMA Normal：同负载通知对照与真实Dispatch时间线

本轮只用URMA内核、设备和私有SDK；暂停新增verbs测试，不运行LL，不改变生产Normal排序/fence/完成策略，不注入200µs延迟。

目标是解释：URMA纯WRITE微测不普遍落后，为什么真实Normal Dispatch仍比此前verbs慢。补齐同负载WRITE/FADD/GPU通知对照，同时取得当前构建的完整Normal基线和真实CPU请求推进链。

## 1. 任务与采样

共 **7个双端任务**，202、204始终同时参与：

| 任务 | 内容 | 每点采样 |
| --- | --- | --- |
| micro_r01/r02/r03 | FP8/BF16两形状×下列四点，每轮8点 | 预热20、计时50，丢弃首个，49个有效 |
| baseline_r01/r02/r03 | 真实Normal的layout、FP8/BF16 Dispatch chunk32、Combine chunk12/16 | 预热50、计时50，丢弃首个；关闭CPU追踪及GPU profiler |
| timeline | 真实Normal程序，阶段统计、WRITE形状及FP8/BF16 Dispatch首个计时调用的详细时间线 | 预热5、计时5，仅作诊断 |

微测固定4线程、每线程8条数据lane、每lane窗口1、双向。每端每次：

| 测点 | WRITE数量 | FADD数量 | 等待条件 |
| --- | ---: | ---: | --- |
| A/write | 32 | 0 | 全部本地CQE |
| B/fadd | 0 | 32 | 全部本地CQE |
| C/write_fadd | 32 | 32 | 全部本地CQE |
| D/gpu_flag | 32 | 32 | 全部本地CQE＋本端GPU观察到接收通知 |

A/C/D的payload大小、对齐、发送数量、窗口和资源相同。B保留相同主数据与通知注册资源，只不提交WRITE。C/D保持生产UrmaTransport在同一lane上的WRITE RELAX、FADD STRONG、原有per-request fence，completion=all；不抑制WRITE CQE，不修改quiet/barrier。C/D每端64个CQE，A/B各32个。三轮轮换四点顺序。B在两个形状标签下的原子负载相同，保留两次对应参照。

使用生产GPU主缓冲区分配和 `allocate_urma_atomic_control()`、真实UrmaTransport注册/提交/完成路径；通知目标是GPU内存，FADD旧值回写到生产注册的host scratch。GPU观察kernel预先启动，只等待标志，不在计时中扫描payload。

双端计时结束后，每次都在计时外校验完整GPU payload/通知计数；B只校验计数。源payload双bank交替，确认双方校验完成后才复用槽位。本轮每端微测共1200条原始计时，不能把两个节点当作六轮重复。

已有split_06仍保留作参考，但其WRITE-only发送量/窗口不同，不能与本轮直接相减。微测绕过命令ring及Proxy反馈，也不是完整Dispatch回放。

## 2. 两端环境准备

两端更新代码后，分别在Bash中执行。202为RANK0，204为RANK1：

```bash
cd /home/h00918771/uccl-ep-xpu-integration-new
set -euo pipefail
export URMA_DIAG_HOME=/home/h00918771
export URMA_DIAG_PYTHON=/home/h00918771/metax_dev/venv-maca-py312/bin/python
export MACA_PATH=/opt/maca-3.7.0
export URMA_DIAG_LIBRARY_DIR=/home/h00918771/metax_dev/urma_after_kernel_20261029/diag/umdk_private_618957c3/lib
export URMA_DIAG_INCLUDE_DIR=/home/h00918771/metax_dev/urma_sdk_private_include

RANK=0  # 204改为1
BUILD=/home/h00918771/metax_dev/urma_mode_parity_build/normal_notify_v1
JOB=urma_notify_01
RESULTS=/home/h00918771/metax_dev/urma_notify_results/$JOB
CONSOLE=/home/h00918771/metax_dev/urma_notify_console/$JOB
LAYOUT="$PWD/test/urma_mode_parity/results/urma_normal_data_01/urma_normal_data_01_layout/node${RANK}/normal/write_layout.jsonl"
mkdir -p "$CONSOLE"
test -s "$LAYOUT"
test -s "$URMA_DIAG_INCLUDE_DIR/urma_api.h"
test -d "$URMA_DIAG_LIBRARY_DIR"
```

私有SDK和库目录若与上述不同，改成此前成功使用的实际路径；两端必须使用同版本。不能使用仓库CPU fixture头文件构建生产扩展。保留此前需要的 `URMA_DIAG_EXTRA_LIBRARY_DIRS`（若有）和正确的MetaX Python，不使用系统Python。

先确认切换后的环境：

```bash
uname -r
cat /sys/module/metax/parameters/runpm_link
ls -l /dev/uburma/udmac0d1e2
setpci -s 0000:a0:08.0 CAP_EXP+28.w
mx-smi
```

沿用split_06成功时的内核 `6.6.0-gdr_f00955680_diag`、URMA设备 `udmac0d1e2`、GPU0、控制接口 `enp33s0`。要求runpm_link=0、GPU空闲及P2P bridge就绪、PCIe Atomic Request开启；以前的0060值可通过，0020缺少0x40位会停止。脚本只检查，不写PCI配置。环境未恢复时先保留检查错误，不绕过校验。

## 3. 两端重新构建并检查

**必须用新BUILD重新编译**：新增了FADD-only控制和URMA提交/CQE时间线钩子，旧split/dispatch_trial模块不能代替。

```bash
bash test/urma_mode_parity/run.sh --action build \
  --node-rank "$RANK" --job notify_build --build-dir "$BUILD" \
  --backend urma --mode normal \
  2>&1 | tee "$CONSOLE/build_node${RANK}.log"

bash test/urma_mode_parity/run.sh --action check \
  --node-rank "$RANK" --job notify_check --build-dir "$BUILD" \
  --backend urma --mode normal --normal-completion all --notification-check \
  2>&1 | tee "$CONSOLE/check_node${RANK}.log"
```

应有 `BUILD_PASSED`（同一输入重试可为BUILD_ALREADY_PASSED）、`MODE_PARITY_CHECK_PASSED`；runtime中应有 `notify_version: 1`。检查不需要同时开始。

构建先运行真实私有SDK头文件下的CPU模拟边界测试，再进行MetaX扩展编译。开发机只能验证CPU模拟/语法，真实SDK ABI、MetaX编译和GPU硬件行为仍由服务器验证。构建/检查失败即停止，保存BUILD下 `host_tests.log`、`build.log`、`build_request.json`、`manifest.json` 中已产生的文件及终端日志；不要手改manifest。

## 4. 双端运行套件

202先启动，180秒内在204启动相同命令；JOB、端口相同，RANK不同：

```bash
bash test/urma_mode_parity/run_normal_notify.sh \
  --node-rank "$RANK" --master-addr 141.61.17.202 \
  --job "$JOB" --build-dir "$BUILD" --layout-file "$LAYOUT" \
  --micro-cpus auto --port 31980 --result-root "$RESULTS" \
  2>&1 | tee "$CONSOLE/suite_node${RANK}.log"
```

31980用于双端任务握手，31981用于微测TCP或torchrun，均需可用。脚本依次完成3轮微测、3轮正式Normal、1轮时间线诊断；无需手动切换任务。

微测auto选择GPU所在NUMA节点的四个可用CPU，另选同节点控制CPU，记录并在线程内核对亲和性。若已协调固定CPU，将auto改成本机四个实际可用核，例如204的 `--micro-cpus 192,193,194,195`；202按本机实际核号填写。完整Normal沿用之前设置，关闭Proxy绑核和adaptive sleep，不额外做绑核扫描。

每轮微测应有8条 `URMA_MICRO_POINT`、`URMA_MICRO_ALL_PASSED`；其余四个任务各应有 `CASE_END normal rc=0`、`URMA_MODE_PARITY_ALL_PASSED`。两端最终均出现 `URMA_NOTIFICATION_SUITE_ALL_PASSED`。

失败立即收集两端当前目录，不继续后续任务、不自动清理共享资源；重跑换新JOB。微测单case超时900秒，完整程序超时1800秒。

## 5. 时间线与结果的解释

`urma_timeline.jsonl` 每端两行：FP8/BF16 Dispatch各捕获首个计时调用。该样本丢弃，整个timeline任务均不作为正式性能基线。

事件以本机steady_clock记录，按proxy、逻辑wr_id关联：

`collect → prepare → post_begin → post_end → cqe → ack_slot → tail_published`

collect是CPU读到命令；prepare是Proxy进入本批分类/准备；post_begin/end来自实际 `urma_post_jetty_send_wr`；cqe来自原生URMA完成上下文还原；ack_slot在当前FIFO中表示pending命令退休，ring分支表示槽完成标记；tail_published记录实际队列推进。FIFO普通命令可在CPU收集时先pop，早于CQE/ack_slot；quiet/barrier可能延后pop。共享JSON字段 `qp` 在URMA文件中表示实际endpoint索引（0为控制，1–8为数据lane），不是verbs QP；汇总CSV将其命名为lane。

原始事件保留内部/边界请求；汇总只对完整、单lane关联链输出相邻区间，并报告覆盖率。若WRITE使用内部wr_id、控制请求没有SDK post或存在窗口边界，不能凭空补齐。C/D的同lane通知依赖由原传输语义保证，本轮不放松fence追求数字。

同一完整请求的CPU侧区间可以关联，但请求之间、不同Proxy及GPU/DMA存在重叠，不能把所有均值/累计范围相加成完整Dispatch。仍缺GPU发布到CPU发现、GPU消费确认以及跨GPU/CPU时钟对应；不同机器的绝对时间戳不能直接相减。

微测span按本端发送与接收观察区间记录，另保存本端CQ排空span。D包含本端GPU接收通知等待，不能把D−C或span−CQ span直接称为纯远端可见性时延。A/C/D吞吐按本端32个payload字节/本端host span算，B不报payload吞吐。

## 6. 两端各自整理日志

```bash
bash test/urma_mode_parity/run.sh --action collect --collect-suite \
  --node-rank "$RANK" --job "$JOB" --output "$RESULTS"

DEST="$PWD/test/urma_mode_parity/results/$JOB"
mkdir -p "$DEST/console/node${RANK}" "$DEST/build/node${RANK}"
cp -v "$CONSOLE"/*.log "$DEST/console/node${RANK}/"
for NAME in host_tests.log build.log build_request.json manifest.json; do
  if [ -f "$BUILD/$NAME" ]; then
    cp -v "$BUILD/$NAME" "$DEST/build/node${RANK}/"
  fi
done
```

需要提供 **`test/urma_mode_parity/results/$JOB/` 完整目录，包含全部7个任务的node0/node1**：

- 微测：原始samples、summary、plan、request、urma_path、描述符、实际WRITE/MVA布局验证、布局输入、affinity及status/bench.log。
- 三轮完整Normal：api_samples、sample_summary、关闭插桩的phases、status/bench.log；五个固定点的原始样本均保留。
- 时间线：urma_timeline、phases、write_layout、原始采样、status/bench.log。
- 各任务setup、host、preflight、runtime、build_manifest、实际Normal源码、设备/内核/计数器及测量后销毁前边界快照；console和上述build日志。失败文件同样保留。

无需.so、objects、cache、tmpdir。若构建阶段失败，没有任务时collect会提示无结果，直接整理build/console即可。

## 7. 双端数据齐全后汇总

```bash
"$URMA_DIAG_PYTHON" test/urma_mode_parity/summarize_normal_notify.py \
  "test/urma_mode_parity/results/$JOB" \
  --output "test/urma_mode_parity/results/$JOB/summary"
```

应有 `URMA_NOTIFICATION_SUMMARY_PASSED`。汇总拒绝缺端/缺轮次、错误case、原始样本缺失或CQE/字节数错误、错误通知策略、形状采集截断、时间线溢出、基线混入插桩，以及同端构建/内核/boot/私有库变化。

输出完整Normal的 `normal_runs.csv`、`normal_aggregate.csv`；微测 `notification_runs.csv`、`notification_aggregate.csv`；诊断 `cpu_stages.csv`、`request_timeline.csv`、`timeline_coverage.csv`。三轮均值取中位数并保留范围，不混合节点、不混入诊断计时。数据回来后先核对当前完整Dispatch差距，再判断通知组合、GPU观察和生产推进空档分别提供了什么证据。
