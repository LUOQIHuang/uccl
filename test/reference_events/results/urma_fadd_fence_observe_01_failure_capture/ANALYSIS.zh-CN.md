# 补收集分析：trace在ready阶段停止，未取得本测试崩溃栈

接收提交3c5a5cc0。本次补充没有新增性能或有效阶段数据，上一轮BF16约3.43%的部分收益结论不变。

## 1. 补充终端日志澄清了trace停止位置

两端external_console/observe.log均显示：

```text
MODE_PARITY_CHECK_PASSED
PAIR_STOP local={'stage': 'ready', 'job': 'urma_fadd_fence_observe_01_all_on_trace',
                 'mode': 'normal', 'rc': 1}
          peer={'stage': 'ready', ... 'rc': 1}
```

driver.py在idle_check异常后写normal/preflight_error.txt，并在ready配对阶段停止；GPU子进程在该门禁后才启动。因此**这份终端记录中的trace尝试没有进入GPU benchmark**，不能称为“开启4096打点后SIGSEGV”。日志没有提供同一尝试的SIGSEGV栈。

但两端采集manifest仍将原ROOT的all_on_trace列为missing，对应preflight_error.txt未收回，无法分辨该次ready失败具体是GPU/设备占用、残留barrier还是硬件检查。终端曾打印已收集到仓库目标目录，可检查仓库已有all_on_trace是否仍保留；不要创建新trace来代替。

之前all_on_post实际记录的URMA status=2→SIGABRT仍是成立的独立故障证据。不能根据这份ready失败日志反推另一次post故障已解决，也不能排除未提供的其他尝试曾出现SIGSEGV。

## 2. 补收集时环境已变化

| 端 | 快照uname | 设备占用 |
| --- | --- | --- |
| 202 | 6.6.0-145.3.34.165.oe2403sp3.aarch64 | fuser rc=1，所查设备未报占用 |
| 204 | 6.6.0-gdr_f00955680_diag | fuser rc=0，URMA设备被ub_test_server占用 |

mx-smi快照显示两端GPU空闲、barrier列表为空。其时间分别显示11月14日01:21/01:14，晚于此前11月13日22:10附近失败；只代表补收集时状态。202已经切回标准内核，两端不满足本轮同一URMA测试协议；204的占用也必须协调，不能擅自结束他人进程。GPU空闲不等于URMA设备空闲，更不证明此前的传输错误已恢复。

这些新状态不能用来解释过去ready失败或post异常的原因。

## 3. 没有取得可用的本测试core/历史内核日志

- 两端coredumpctl list均为No coredumps found，未找到本测试已知故障PID的栈。
- 204的泛化coredumpctl info返回另一程序、另一用户在另一时段的SIGABRT；它不是UCCL/Python测试，排除，不作根因证据。
- 旧收集脚本使用journalctl -k。该选项默认限定当前启动；切核/重启后查询故障时段会漏掉上一启动的内核记录。因此本次为空不能解释为此前没有UDMA错误。此前已上传的kernel_after仍保留传输错误证据。
- peer_off_paced/post原始目录与仓库目标出现preflight.json/gpu_before.txt冲突。意味着两份现场文件已经不同；不能覆盖旧证据或混成同一次运行。

## 4. 已修收集工具，暂不增加GPU测试

这次只修复取证范围，不声称修复生产传输故障：

1. 使用journalctl _TRANSPORT=kernel按时段查询保留的跨启动日志，并记录启动列表；若日志未持久化，仍可能找不到，不能恢复已丢失记录。
2. core info限定已有bench.log中明确记录的测试PID和对应host.json的boot ID，停止读取泛化“最近一次core”。
3. 源文件与已上传数据冲突时，将当前源另存conflicting_source，不覆盖原文件。
4. 原ROOT缺失但仓库目标仍有trace目录时，另存destination_only并保留已有node/console。

可在当前内核下执行，只读取文件/日志，不运行benchmark、不清理共享内存、不重置设备。若需要补收，沿用[上一份文档中的同一收集命令](../urma_fadd_fence_observe_01_ANALYSIS.zh-CN.md)，重新拉取代码后执行一次即可。提供新failure_capture时间戳目录，特别是归档的all_on_trace/normal/preflight_error.txt。所有冲突和归档路径均写入capture.json。

若跨启动日志/core和原trace错误文件确实都不存在，停止继续机械补收集，明确本轮初始故障无法从存量记录恢复。等两端URMA内核和设备独占条件恢复，再依据保留证据准备最小复现；当前不重跑六项、不改fence或WRITE参数、不将不同内核结果拼接。

现阶段最确定的进展是：本份trace终端记录的失败在运行前门禁，尚无证据归因于GPU打点或fence off；生产瓶颈仍以已有“ATOMIC完成回收背压”和约3.43%干预收益为限，没有新的阶段因果结论。
