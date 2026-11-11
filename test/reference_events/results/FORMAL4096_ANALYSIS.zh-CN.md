# 4096-token参考事件：记录完整，性能代表性部分通过

更新：OFF对照也已完成，四个Dispatch偏差均小于0.2%，见[OFF核查](OFF_CONTROL_ANALYSIS.zh-CN.md)。后续只补[采样方式控制](../CADENCE_CONTROL.zh-CN.md)，下文保留原on阶段核查，不重复已完成任务。

截至提交42d20a50，urma_ref_4096_base_01、urma_ref_4096_trace_01、urma_ref_post_01两端全部齐全，另有上传汇总urma_ref_summary_01。已独立重跑summarize.py，结果同样为REFERENCE_EVENT_SUMMARY_PASSED usable=False。128-token的成功采集另见[功能验证核查](SMOKE128_ANALYSIS.zh-CN.md)。本报告不以提交说明中的PASS代替原始数据检查。

## 功能和数据完整性已通过

两端base/trace/post共6个case均rc=0、status所有checks为True。每个case的24组正确性检查通过；trace开启事件时的reference_correctness和离线validate(case,10)通过。正式case_data检查也通过，三组归一化后的生产源码、诊断脚本、SDK/编译参数、库、GPU版本、benchmark、设备及boot身份一致，base/post确为同一模块SHA。

形状固定4096 tokens、hidden7168、experts256、topk8，Normal WRITE=RELAX、completion=all。base预热50/采样50（49个有效值），trace预热5/采样10（9个有效值）。每端20个Dispatch采集窗口；每窗GPU事件0/1/2各12条、其余为0，GPU和CPU dropped均0。两个Dispatch的18个有效窗口均可恢复每窗396条完整FIFO逻辑链，时间顺序正确；每窗另有2次UINT64_MAX标识的内部控制提交，不拼成完整FIFO请求。

## 当前API对照

单位μs；变化为trace相对同节点(base+post)/2。门槛要求前后漂移、trace变化、各组CV均不超过5%；这是工程筛选，不是统计显著性检验。

| 节点 | Dispatch | base | trace | post | trace变化 | trace CV | 代表性筛选 |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| 202 | FP8 | 5739.081 | 6244.722 | 5747.268 | +8.733% | 14.747% | 不通过 |
| 202 | BF16 | 7960.654 | 8151.779 | 7964.646 | +2.375% | 0.300% | 通过 |
| 204 | FP8 | 5740.565 | 5909.532 | 5745.523 | +2.899% | 0.501% | 通过 |
| 204 | BF16 | 7959.489 | 6717.241 | 7965.262 | −15.638% | 0.656% | 不通过 |

四个Dispatch的前后基线漂移分别为+0.143%、+0.050%、+0.086%、+0.073%；base/post的CV均小于1%。不支持将trace的明显变化归为整轮环境漂移。

202 FP8的有效样本index=3为8846.080μs，其余有效样本为5825.280–5978.624μs。它确实推高均值和CV，但不能因其不利就删除、改成8个有效样本或宣称整体扰动通过。204 BF16的9个有效样本为6662.656–6791.936μs，明显低于base，不只是一个离群值；加速同样属于测量代表性需要解释的变化，不能直接当成实现优化收益。

202 BF16和204 FP8通过API代表性筛选，可保留为条件较好的局部观测；这仍不保证其每个内部时刻都无扰动，尤其两个节点会相互影响。不能因为其中两行较好就宣布整套打桩合格，也不应把所有已采原始记录丢弃。

相对前后参考均值，layout变化为202 −0.512%、204 +0.980%；Combine12两端约−0.244%，Combine16约−0.241%，这些控制项均通过。没有旧全范围RAII构建约28%的Combine劣化。本轮Dispatch变化不能直接解释为整体环境一同加速/减速。

## 已能看到的CPU请求链

每个节点/测点汇集9个有效窗口的3564条完整逻辑链，分别取各段中位数，单位μs。按逻辑WR保留批量SDK提交边界，不能解释为每WR独占开销。原始396条完整组外还有1个保留控制标识组，因此capture_quality为396/397=99.748%；实际物理post为398次，不把重复保留标识当成唯一业务请求。

| 节点 | Dispatch | poll→decoded | decoded→prepare | prepare→post_begin | post_begin→post_end | post_end→最后CQE | 最后CQE→ack_slot |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 202 | FP8 | 0.030 | 3.280 | 0.490 | 0.700 | 581.245 | 0.320 |
| 202 | BF16 | 0.030 | 3.200 | 0.540 | 0.730 | 1080.320 | 0.330 |
| 204 | FP8 | 0.030 | 3.370 | 0.560 | 0.440 | 627.460 | 0.330 |
| 204 | BF16 | 0.030 | 3.280 | 0.660 | 0.460 | 1325.795 | 0.320 |

在当前事件运行里，提交到软件观察到CQE的间隔远大于SDK调用范围。它包含排队、设备/依赖推进、传输以及完成轮询观察，不等于纯网络耗时，也不含GPU发布到Proxy第一次取到命令的等待。混合WRITE/FADD及各请求并行，不能将这些中位数相加成Dispatch，也没有相同参考测点下的verbs数据可做差。

GPU的三类事件已采齐，但它们不覆盖kernel入口和最终Receiver结束；事件0相对CPU origin的偏移包含主机和其他GPU工作，不能直接归为Sender计算等待。每窗clock_stats最大间隔202最高87.17μs、204最高732.55μs；统计含启动/结束边界及尾间隔，尚不能认定全部发生在GPU采样期间，也不能无视它并宣称GPU/CPU具备精确微秒关联。保留原始设备clock括号，正式归因前需检查时钟质量。

按payload_bytes把大payload WRITE与16B元数据WRITE、atomic分开，提交后到CQE的中位间隔如下。每个节点/测点各1188条大WRITE、108条16B WRITE、2268条atomic：

| 节点 | Dispatch | payload WRITE | 16B WRITE | atomic |
| --- | --- | ---: | ---: | ---: |
| 202 | FP8 | 733.495 | 4.540 | 506.890 |
| 202 | BF16 | 1324.390 | 6.940 | 924.925 |
| 204 | FP8 | 879.655 | 7.700 | 365.880 |
| 204 | BF16 | 2002.715 | 7.825 | 736.070 |

这些是同一运行中不同请求集合的观察，不是等条件算子微测；请求大小、QP、排序依赖和提交时刻均可能不同。大数据请求及通知推进值得继续核查，但不能只按表格大小宣布WRITE设备服务时间或FADD硬件时间。204 BF16属于代表性不通过的观测，不用于原始性能归因。

## 代码侧发现的明确测量差异

普通base/post在一组样本开始前同步，然后连续记录多次调用，组末再同步；reference_events开启后则每个样本都做GPU同步、两端barrier、trace reset、时钟线程启停及读回。它们虽多在CUDA Event区间外，仍会改变两端发起时刻、队列历史和流水线状态。事件版还逐样本创建CUDA Event，普通版预先创建整组Event。

因此本次对照混合了“编译进探针”“实际记录CPU/GPU事件”“逐样本运行节奏”三项因素；这是已确认的实现差异，但尚未证明其中哪一项造成204 BF16的加速。不能把该加速当作生产路径优化。缺少kernel入口/最终Receiver事件，也不能用本轮三个点验证此前Receiver等tail究竟由哪一级拖慢。

## 内核日志边界

按400行快照的公共首尾衔接检查，base/trace的计时结束前未新增旧的bind错误；销毁后仍出现TP level AE type16/subtype0。不能把历史错误当作此次复发，也不能将销毁后告警归入计时内延迟。它们仍是需要记录的驱动现象，不能写成整个运行无任何内核异常。

## 当前结论及下一步

已从“连接失败、无数据”推进到“目标负载两端正确运行、CPU/GPU事件采集完整”。这不证明此前失败必然由残留进程/共享内存造成，日志没有单变量恢复实验；也不代表已经找到URMA比verbs慢的具体步骤。

post和汇总已全部收到，不需要重跑。可离线复算（输出目录需不存在）：

```bash
"$PY" test/reference_events/summarize.py \
  --baseline "$DEST/urma_ref_4096_base_01" \
  --trace "$DEST/urma_ref_4096_trace_01" \
  --post "$DEST/urma_ref_post_01" \
  --output "$DEST/urma_ref_summary_recheck_01"
```

原计划的`--step off`对照已完成，无需重跑，见[OFF核查](OFF_CONTROL_ANALYSIS.zh-CN.md)。下一步只补关闭采集的batch/paced采样方式控制，操作见[CADENCE_CONTROL.zh-CN.md](../CADENCE_CONTROL.zh-CN.md)。
