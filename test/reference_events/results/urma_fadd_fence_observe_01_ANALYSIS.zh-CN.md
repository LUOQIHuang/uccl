# fence稀疏观测首轮：A基线通过，正式trace缺失，后控制遭遇URMA完成错误

本次接收提交03e59628，分析日期2026-11-13。尚未取得完整A/B阶段对照，不能据本轮确认278μs来自哪个阶段。提交说明中的“trace SIGSEGV”与实际上传的all_on_post SIGABRT必须区分。

## 已有可用数据

| 任务 | 202 | 204 | 判断 |
| --- | --- | --- | --- |
| observe_smoke128，204 fence off，开启boundary采集 | rc=0、checks全true | 同左 | 小形状采集/正确性门禁通过，不是4096性能结论 |
| all_on_paced，4096，关闭采集 | BF16 8.08565ms，CV0.218% | BF16 6.67955ms，CV1.976% | 完整有效A基线，原始样本及身份检查通过 |
| all_on_trace | 未上传对应目录 | 未上传对应目录 | 提交说明称SIGSEGV，缺实际bench/console/堆栈，无法定位崩溃点 |
| all_on_post，4096，关闭采集 | URMA status=2后SIGABRT | URMA status=2后SIGABRT | 性能点前失败，不能作为后控制 |
| peer_off_paced / peer_off_post | EXISTING_BARRIER拦截 | 同左 | 未启动GPU benchmark，没有B样本 |
| peer_off_trace | 未上传对应目录 | 未上传对应目录 | 没有B阶段记录 |

A基线202与上一轮两次all-on均值8.08357ms相比仅+0.0257%，204相比6.63242ms为+0.7106%。这说明A的BF16耗时总体延续此前水平；不是本轮新测到的fence优化收益，不能借上一轮B拼出本轮完整对照。

## 错误链条：已知与未知

all_on_post的setup记录reference_events=paced、normal_fadd_fence_plan=all-on，实际transport日志fence on。因此该失败发生在**未关闭fence、未开启采集**的后控制项，不能称为本次fence-off导致的正确性失败。

- 202：首次“BF16 without top-k”正确性测试中，proxy1上WRITE和ATOMIC出现URMA_COMPLETION_ERROR status=2，随后抛出runtime_error并terminate，torch子进程记录Signal6/SIGABRT。
- 204：同一正确性阶段，proxy0的ATOMIC出现status=2，随后同样SIGABRT。
- 本项只留下layout计时，未产生BF16/FP8 Dispatch正式样本及reference_control。reference_validation_error里缺少reference_control.jsonl是进程提前退出的后果，不是首个错误。
- 两端kernel_after记录udma_ae_tp_level_error回调，并有“timeout after15360ms, not all jetty get ack”。说明此次失败涉及URMA传输/设备状态，需要结合更早的trace故障时段检查；无法仅凭这一条日志认定驱动就是最初根因。
- 随后B项因/dev/shm/uccl_barrier_*残留在idle_check中停止，是崩溃后的阻断条件，不是B性能差或fence关闭失败。

status=2在当前私有SDK中的具体枚举/硬件原因尚未核对，本报告不凭其他版本SDK数值强行解释。也不能依据提交标题，将post的SIGABRT改写成trace的SIGSEGV。没有all_on_trace日志及栈，尚不能判断观察器内存访问、并发退出、GPU/SDK还是其他原因先触发。

## 当前结论

1. 保留上一轮BF16约3.43%/278μs的部分收益结论；本轮既未推翻，也未进一步解释它。
2. 4096开启采集路径尚未通过本轮验证，没有可接受的新阶段数据。
3. 先收取已有all_on_trace及崩溃上下文，不重跑六项，不绕过barrier门禁，不删除别人的共享内存，不自动重置设备。
4. 当前证据不足以针对生产算子或GPU kernel提交一个可验证的根因修复；先补足栈与首发错误，避免盲改再次消耗机器时间。

## 现在只运行只读补收集，不使用GPU benchmark

两端均执行，202设RANK=0、204设1。复用原始JOB/ROOT，不创建替代trace或改写旧结果：

```bash
cd /home/h00918771/uccl-ep-xpu-integration-new
RANK=0  # 204改为1
PYTHON=/home/h00918771/metax_dev/venv-maca-py312/bin/python
ROOT=/home/h00918771/metax_dev/reference_event_results
BUILD=/home/h00918771/metax_dev/reference_event_build/urma_fadd_fence_v1
JOB=urma_fadd_fence_observe_01
DEST="$PWD/test/reference_events/results"

"$PYTHON" tools/reference_events/collect_fence_failure.py \
  --root "$ROOT" --build "$BUILD" --job "$JOB" --node-rank "$RANK" \
  --destination "$DEST" \
  --since '2026-11-13 21:45:00' --until '2026-11-13 22:20:00'
```

时间范围来自本轮日志显示时间，journalctl/coredumpctl按服务器本地时间解释。若实际trace故障更早，扩大since。工具不会运行benchmark，不kill进程、不清理共享内存、不重载驱动、不重启。

工具补收原ROOT下七项已有node/console，尤其all_on_trace；同时记录进程、GPU/设备占用、barrier文件元数据、指定时段内核日志和coredumpctl list/info（仅元数据/可用栈，不导出core内存）。若某工具未安装或权限不足，会记录原因后继续。若原trace目录已不存在，会明确记录missing，不合成数据。已有目的文件发生冲突也只记录collection_errors，不覆盖；检查capture.json。

需要提供：

- 补收的all_on_trace完整node0/node1和console（若存在），以及peer_off_trace（若曾启动）。
- 新目录urma_fadd_fence_observe_01_failure_capture/，两端均保留。
- 若崩溃时另有手工执行的完整终端日志、gdb栈或错误报告，也保留原文件。提交中没有这些内容时，collector无法从不存在的文件恢复。

拿到首发trace栈后再决定修复位置及是否需要协调恢复设备。不要为补齐目录启动新benchmark；仅删除barrier不能证明URMA设备已恢复。
