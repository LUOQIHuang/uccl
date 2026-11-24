# Combine从个位数到三位数毫秒：基线隔离与结论撤回

2026-11-24。当前尚未确认变化根因。不能把该差异仅归为paced节奏，也不能继续用后期大Combine稀释Dispatch差距后宣称当前总差距不到1%。

## 原始数据核查

以下为202、Normal、4096 tokens、hidden7168、topk8、experts256、SM24、NVL chunk4、Combine RDMA chunk16的原始独立调用均值。

| 来源 | 预热/计时/有效数 | 原始mean_us | 换算ms | MACA / GPU驱动 |
| --- | --- | ---: | ---: | --- |
| 早期URMA phase r02 baseline | 50/50/49 | 7250.7663 | 7.25077 | 3.7.0.38 / 3.8.23 |
| 后期URMA dispatch_base_01，关闭GPU阶段编译 | 50/50/49 | 133855.9287 | 133.85593 | 3.9.0.29 / 3.10.14 |
| 后期URMA ref_4096_paced_01 | 5/10/9 | 135925.2760 | 135.92528 | 3.9.0.29 / 3.10.14 |

早期与后期普通base本身就相差约18.5倍；这不是把微秒错当毫秒，也不是将50次计时总和当成一次均值。sample_summary和逐条API采样记录的是每次CUDA Event区间。后期base即使没有活动插桩、同样50预热/50计时也出现该数量级，不能只归因活动trace、样本数或paced。

来源：[早期原始采样](../urma_mode_parity/results/urma_phase_v1/urma_phase_v1_r02_baseline/node0/normal/sample_summary.jsonl)、[后期普通base](../dispatch_diag/results/urma_dispatch_base_01/node0/normal/sample_summary.jsonl)、[后期paced](results/urma_ref_4096_paced_01/node0/normal/sample_summary.jsonl)。每组的gpu_before、setup、build_manifest和runtime均保留在相邻目录。

## 已核实的代码与环境边界

1. 早期和后期准备后的normal.py完全相同，归一化LF后的SHA256均为 `91e0a517d13c17239425bd23aad25e973c694b0a737739b6521144fdf1b56907`。负载和chunk相同。
2. 两组URMA均使用6.6.0-gdr_f00955680_diag，Torch版本字符串相同，但MACA和GPU驱动不同，加载的扩展模块不同。
3. 两组生产源码manifest也不同。internode.cu的归档指纹分别匹配378012cf与aa5accb0；ep_configs.cuh分别匹配0854fb6f与2e63b8ba。还有Proxy/FIFO/transport等变更，不能只归因MACA或某一行代码。
4. 后期普通base的UCCL_GPU_STAGE_TRACE=0；参考事件版本还带独立编译标志。不能把“关闭采集”理解为跨版本编译产物相同。
5. `phase_runtime.Measurements.api` 的paced分支要求 `label.startswith('dispatch_')`；verbs worker也如此。**Combine没有走该paced分支，仍走连续事件计时。** “整个job叫paced”不是解释Combine数量级差异的证据。
6. 两后端后期都记录到约133ms，不能将这个共同现象直接归为URMA特有问题。实际执行、计时适配器/调用流程、公共生产代码和MACA/驱动变化仍需继续区分。

## 哪些结论保留，哪些撤回

- 保留：早期约7ms和后期约133ms都是已上传原始计时读数，完整记录没有被删除或修正数值。
- 保留：固定同轮、同模块下的Dispatch fence干预与Dispatch阶段观察。Combine异常不自动否定这些局部对照。
- 保留：仅关tail时，某轮Combine前后变化很小；这只支持同轮内部近似不变，不证明其绝对值可代表预期业务基线。
- **撤回：将近期约133ms的Combine加到最新Dispatch后，称当前总差距仅0.56%–0.70%的结论。** 在数量级变化未闭合前，它只是未验证跨批数据的算术组合。
- 更早“总差2.69%”同样只能作为该组读数的算术之和，不作为已验证的当前Dispatch→Combine总链路差距。
- 最早约13.85/14.64ms的合计属于早期两套栈的历史分项统计，不外推到当前优化版本。

## 当前处理

将Combine的跨版本绝对值及由它推导的总延迟先隔离。继续保存它作为控制项的原始数据，但不再据它评价总体追平程度。尚无证据决定要改哪一处生产代码，不为凑出个位数而修改计时除数或删除慢样本。

已经在运行的大区间主套可以继续，硬件代码与步骤不变，无需仅为本次口径纠正重新编译、重跑或切换内核。其Dispatch主线仍按同一当前版本的两后端对照分析。Combine下一步先做现有版本、调用路径和编译产物的离线追溯；若需硬件验证，应固定同一当前环境、分开原计时和适配计时及生产版本，不同时改变MACA、驱动、编译选项和采样参数后宣称找到了原因。
