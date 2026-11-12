# Normal WRITE排序A/B：STRONG显著劣化，当前RELAX应保留

复核提交98f10683，目录urma_normal_order_current_02。两轮完整配对：r01 RELAX→STRONG，r02 STRONG→RELAX，共8个成功case（每项两端）。r03在ready预检查停止，只有console，不算有效实验，更不将对端ready rc=0解释为benchmark完成。

## 原始数据验收

8个case均status rc=0、所有checks为True。250条API样本/端/case、5条summary齐全，每点预热50、采样50、丢首值后49个有效值；逐点重算均值与保存值一致。运行时normal_write_order及transport日志分别为relax/strong，completion=all、FIFO、普通模块reference_events_version=0；没有GPU/CPU采集。FADD placement为STRONG，原fence按请求保留，未改lane、GPU程序或完成策略。

8个case的kernel_before与kernel_after_measurements均有可匹配重叠；新增日志未匹配到ctrlq wait resp timeout、Failed to exchange tp info、TP level AE或bind jetty failed。它只排除这些已知错误签名，不将历史内核日志当作本轮失败。

逐字段核对，同一节点的模块、生产源码、诊断脚本、库、boot、形状等一致；除预期normal_write_order外，唯一差异是preflight硬件快照的runtime_status：r01_relax为suspended，其余active。

汇总器此前把这个动态功耗状态也当作不可变身份而拒绝。本次只将已知active/suspended快照移到context的独立preflight_power_states中，继续严格检查其余硬件、模块、库等字段，未知状态仍拒绝。它不等于宣称功耗状态永远不影响性能。r02两种策略都为active，且执行次序反过来，仍复现9～12倍差异，不能用首轮唤醒解释整个效应。

修正后以--repeats 2严格汇总通过，输出保存在urma_normal_order_current_02/summary；没有关闭缺轮次检查或将两轮标成三轮。

## 核心结果

每列为该节点两轮独立运行均值的中位数（两轮时即中点），单位ms；不混合两端当作重复轮次。

| 节点 | 测点 | RELAX | STRONG | STRONG/RELAX |
| --- | --- | ---: | ---: | ---: |
| 202 | FP8 Dispatch | 5.745 | 52.726 | 9.178× |
| 204 | FP8 Dispatch | 5.744 | 52.728 | 9.180× |
| 202 | BF16 Dispatch | 7.964 | 99.968 | 12.553× |
| 204 | BF16 Dispatch | 7.962 | 99.967 | 12.555× |

按同轮配对，FP8的STRONG劣化约+816.5%～+819.5%，BF16约+1154.6%～+1156.2%，两端、两种执行次序方向一致，远大于本轮样本CV（Dispatch约0.24%～1.64%）。STRONG相对RELAX新增约47ms/次FP8、92ms/次BF16。

Combine12两端约132.922→136.220ms，+2.48%；Combine16约134.219→138.349ms，+3.08%。同轮差值有变化：Combine12约+0.93%/+4.05%，Combine16约+1.23%/+4.95%。它们没有Dispatch的数量级劣化，但不能写成“完全不受影响”。layout约154～155μs，没有类似变化。

上传normal_order_ab_summary.md的Combine12聚合RELAX数值132672.46μs与原始文件不一致；重算为约132922.46μs。因此本报告与严格汇总CSV为核查口径。原报告的“mean of 50 samples”也应理解为50次计时中丢首值，实际49个有效样本。

## 对原目标的意义

1. 当前环境中，Normal WRITE强弱序对Dispatch影响巨大，STRONG明显更慢。继续保留RELAX默认策略，不把切STRONG作为优化方向。
2. **之前URMA仍慢于verbs的有效基线和参考事件实验已经使用RELAX。**本轮不能将那个剩余差距解释为“默认误用了WRITE强序”，也没有证明FADD强序/fence可取消。
3. 本轮只改WRITE placement，但没有采集强序内部阶段，因此还不能把新增47/92ms分配给SDK、设备排队、数据可见性或GPU等待中的某一段。
4. 两轮反向次序、两端一致已足以支持上述初步方向结论；不需要为了补足第三轮数字机械重跑。它仍是两轮描述性结果，不冒充完整三轮或统计显著性检验。

下一步优先补现有VERBS_MATCHED文档中的同口径阶段记录，与已经可用的URMA RELAX数据对照；不再为一个明显更慢的STRONG策略铺开全套GPU打桩扫描。若暂时没有verbs窗口，可离线继续分析现有RELAX请求/事件，不强行切内核或要求立即重测。
