# verbs Normal：WRITE／FADD／GPU通知对照与真实请求时间线

本轮只使用当前RDMA/verbs内核，不运行URMA、LL或大范围参数扫描。已收到的 `verbs_normal_campaign_01` 继续作为完整Normal基线，本轮不重复那11个case。

目标：补齐在相同负载下，WRITE加入原子通知和GPU观察后的verbs参照；另在真实 `test_internode.py` 的Normal Dispatch中关联CPU收集、提交、CQE和GPU确认更新。它不是完整、可相加的跨机GPU关键路径分解。

## 1. 本轮执行内容

共 **4个双端任务**，202/204始终同时参与：

| 任务 | 内容 | 默认次数 |
| --- | --- | --- |
| notify_r01/r02/r03 | 每轮FP8/BF16两形状×A/B/C/D四点，共8点 | 每点预热20、计时50，丢弃首个，49个有效 |
| timeline | 真实Normal程序，保留原正确性检查及五个固定计时点；仅FP8/BF16 Dispatch首个计时调用记录详细时间线 | 预热5、计时5，仅作诊断 |

四点含义：

- A/write：每线程8条WRITE，等待全部本地CQE。
- B/fadd：每线程8条64位FADD，等待全部本地CQE；不发送payload。
- C/write_fadd：先提交8条WRITE，再提交8条FADD；每lane的WRITE/FADD在同一RC QP上，等待全部CQE。
- D/gpu_flag：请求与C相同，另在远端GPU预先运行观察kernel，只等待通知计数达到预期，不扫描payload；本端线程在排空CQE后等待本端GPU观察完成。

固定4线程、每线程8条数据QP、每lane窗口1、双向。A/C/D每次本端32条WRITE、相同payload与源/目标地址对齐；B/C/D各32条FADD，C/D各64个CQE。三轮分别轮换四点执行顺序，不能把两端当作六轮重复。

A/B/C使用同一已注册资源，A也保留通知缓冲区及返回值MR，但不提交FADD。复用生产 `reg_mr_gpu_dmabuf()`、`allocate_atomic_buffer()` 和 `register_control_buffer_mr()`；主数据与通知目标均为GPU内存、DMA-BUF注册，无host目标fallback。FADD旧值返回到注册的host缓冲区，沿用生产native Normal结构。当前生产FADD的send_flags只有SIGNALED，本轮保持相同，不新增IBV_SEND_FENCE。QP使用port1、显式GID、active MTU、SL3、TC104、PSN0。

微测不复现完整ring、quiet/barrier和WRITE_WITH_IMM软件通知；它控制的是原生WRITE/FADD组合。所有结果先用于比较依赖趋势，不能把微测当作整个生产Dispatch。

每个点开始清零接收和计数；计数在逐次迭代中递增，源payload双bank交替。双端本地计时结束后，在计时外逐次校验完整GPU payload及32个通知计数；B只校验计数。失败立即停止，不把错误轮次纳入结果。

## 2. 两端设置环境

两端更新代码，在Bash终端执行。202为RANK0，204为RANK1：

```bash
cd /home/h00918771/uccl-ep-xpu-integration-new
set -euo pipefail
export VERBS_DIAG_HOME=/home/h00918771
export VERBS_DIAG_PYTHON=/home/h00918771/metax_dev/venv-maca-py312/bin/python
export MACA_PATH=/opt/maca-3.7.0

RANK=0  # 204改为1
BUILD=/home/h00918771/metax_dev/verbs_baseline_build/notify_v1
JOB=verbs_notify_01
RESULTS=/home/h00918771/metax_dev/verbs_notify_results/$JOB
CONSOLE=/home/h00918771/metax_dev/verbs_notify_console/$JOB
LAYOUT="$PWD/test/urma_mode_parity/results/urma_normal_data_01/urma_normal_data_01_layout/node${RANK}/normal/write_layout.jsonl"
GID=3
mkdir -p "$CONSOLE"
test -s "$LAYOUT"
```

LAYOUT仅为已有形状输入，不会启动URMA。沿用此前通过的MetaX Python和 `VERBS_DIAG_EXTRA_LIBRARY_DIRS`（若有），不要使用系统Python。不启用FIFO backend，不另外设置绑核或调优变量。

默认HCA `rocep40s0f0`、控制接口 `enp33s0`、GPU0。默认GID3沿用上一轮已通过路径，两端分别确认：

```bash
ibv_devinfo -d rocep40s0f0
cat /sys/class/infiniband/rocep40s0f0/ports/1/gids/$GID
cat /sys/class/infiniband/rocep40s0f0/ports/1/gid_attrs/types/$GID
```

要求port ACTIVE、非零且可互通的RoCE v2 GID。若环境变动，按实际GID表选择两端可互通项，并始终使用该变量；本轮不修改网络配置。

## 3. 两端重新构建、各自检查

**必须使用新BUILD，不能复用上一轮扩展。** 新增GPU通知微测绑定及生产时间线钩子：

```bash
bash test/verbs_baseline/run.sh --action build --variant current \
  --node-rank "$RANK" --job notify_build --build-dir "$BUILD" \
  --mode normal --gid-index "$GID" \
  2>&1 | tee "$CONSOLE/build_node${RANK}.log"

bash test/verbs_baseline/run.sh --action check --variant current \
  --node-rank "$RANK" --job notify_check --build-dir "$BUILD" \
  --mode normal --gid-index "$GID" --campaign-check --notify-check \
  2>&1 | tee "$CONSOLE/check_node${RANK}.log"
```

成功标志为 `VERBS_BUILD_PASSED`（相同输入重试可为ALREADY_PASSED）和 `VERBS_CHECK_PASSED`。runtime包含 `verbs_notify_version: 1`、`verbs_micro: true`、`verbs_diag_version: 1`。两端检查不必同时启动。

构建先运行真实verbs头文件下的模拟provider测试：源码post/poll追踪，以及生产微测实现的双端WRITE/FADD/通知等待、数据与计数校验，再编译MetaX扩展。开发机已验证的是CPU模拟边界和host语法；真实MetaX编译及硬件语义由服务器验证。

若构建/检查失败，停止，整理本节终端日志和BUILD下的 `host_tests.log`、`build.log`、`request.json`、`manifest.json`（存在的文件即可）。不要手改manifest或复用旧模块绕过检查。

## 4. 两端运行一键套件

先启动202，然后180秒内启动204。两端JOB、GID、端口一致，RANK不同：

```bash
bash test/verbs_baseline/run_notifications.sh \
  --node-rank "$RANK" --master-addr 141.61.17.202 \
  --job "$JOB" --build-dir "$BUILD" --layout-file "$LAYOUT" \
  --gid-index "$GID" --micro-cpus auto --port 31880 --result-root "$RESULTS" \
  2>&1 | tee "$CONSOLE/suite_node${RANK}.log"
```

31880用于任务握手，31881用于微测TCP/torchrun。脚本按顺序执行三轮微测，再执行一个真实Normal诊断任务。

微测CPU使用GPU所在NUMA节点的四个可用核，另选同节点控制核，写入affinity文件并在线程内校验。若已协调固定核，可将auto改成本机四个可用CPU，例如204的 `--micro-cpus 192,193,194,195`；202使用自己的实际核号。真实Normal诊断沿用上一轮设置，关闭Proxy绑核与adaptive sleep。

每轮微测应输出8条 `VERBS_MICRO_POINT`、`VERBS_MICRO_ALL_PASSED`；timeline任务应有 `VERBS_CASE_END ... rc=0`。两端最后均出现 `VERBS_NOTIFICATION_SUITE_ALL_PASSED`。

任何失败先收集两端当前日志，不继续后面的测试、不自动清理共享资源；重跑换新JOB。微测单个case超时900秒，真实程序超时1800秒。

## 5. 时间线字段与解释边界

`verbs_timeline.jsonl` 每端两行，分别对应FP8/BF16 Dispatch；仅首个计时调用插入详细追踪，该样本丢弃，整个任务仍仅作诊断。

事件使用本机steady_clock纳秒，按proxy、wr_id关联：

`collect → prepare → post_begin → post_end → cqe → ack_slot`

- collect：CPU已读取该命令，尚不包含GPU发布到CPU发现的等待。
- prepare：进入生产Proxy本次分类/准备范围；不是独立纯计算阶段。
- post_begin/end：源码实际ibv_post_send调用前后；物理子WR可能共用逻辑wr_id。
- cqe：软件poll观察到发送CQE；包含设备、排队和发现延迟。
- ack_slot：生产ring清理槽并标记完成后；不等于GPU已经消费确认。
- tail_published：推进连续完成tail之后记录；可能因前面的命令未完成而晚于ack_slot。

汇总只对完整且单QP关联链输出同一请求的相邻区间，另报告匹配覆盖率。未匹配控制命令/边界完成保留原始事件，不伪造缺失阶段。线程间时间戳可使用本机同一时钟观察相对关系，但不同机器的时间戳不能直接相减；wr_id只在本机proxy及本次窗口内解释。

这些区间在**单个完整关联请求**内可形成CPU侧推进链，但各请求相互重叠，不能把所有区间平均值、四个Proxy累计范围或两端耗时相加成Dispatch。仍缺GPU发布/消费时间和跨GPU/CPU时钟对应。

D的主span覆盖本端发送及本端接收观察，另保存本端CQ排空span；两端开始时间不是同一个全局零点，二者差值不是纯远端可见性时延。A/C/D吞吐以本端32个payload字节除以host span计算，D包含GPU通知等待；B不报告payload吞吐。不与上一轮128条WRITE、窗口4的数字直接相减。

## 6. 两端分别整理所需日志

```bash
DEST="$PWD/test/verbs_baseline/results/$JOB"
"$VERBS_DIAG_PYTHON" test/verbs_baseline/collect_campaign.py \
  --root "$RESULTS" --job "$JOB" --node-rank "$RANK" --destination "$DEST"

mkdir -p "$DEST/console/node${RANK}" "$DEST/build/node${RANK}"
cp -v "$CONSOLE"/*.log "$DEST/console/node${RANK}/"
for NAME in host_tests.log build.log request.json manifest.json; do
  if [ -f "$BUILD/$NAME" ]; then
    cp -v "$BUILD/$NAME" "$DEST/build/node${RANK}/"
  fi
done
```

需要提供 **`test/verbs_baseline/results/$JOB/` 完整目录，包含两端node0/node1**：三轮微测的原始samples、summary、plan、request、verbs_path、描述符、亲和性、status、bench.log、布局输入和内核测量边界；timeline任务的 `verbs_timeline.jsonl`、`verbs_phases.jsonl`、WRITE形状、原始采样、status、bench.log；各任务setup、host、runtime、module_manifest、benchmark源码和内核/端口计数器快照；以及上述console/build日志。

不需要扩展.so、objects、cache或tmpdir。失败目录也保留。若构建失败没有任务，collect提示无结果，直接整理build/console即可。

## 7. 两端结果齐全后严格汇总

```bash
"$VERBS_DIAG_PYTHON" test/verbs_baseline/summarize_notifications.py \
  "test/verbs_baseline/results/$JOB" \
  --output "test/verbs_baseline/results/$JOB/summary"
```

成功标志：`VERBS_NOTIFICATION_SUMMARY_PASSED`。汇总拒绝缺端、缺轮次、失败case、原始样本不完整、CQE/数据量错误、注册路径不符、阶段追踪溢出以及同端构建/内核/boot/GID变化。

输出 `notification_runs.csv`、`notification_aggregate.csv`、`request_timeline.csv`、`timeline_coverage.csv`。每端每点以三轮均值中位数和范围评价，不把timeline任务作为性能重复。数据回来后先判断：C相对A如何变化；D是否出现额外等待；真实程序的提交空档和确认链是否对应这些现象。再决定下一项URMA对照，不预设原子或Proxy一定是根因。
