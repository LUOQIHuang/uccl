# Observe_02新一轮A/B补跑：净收益再次出现，阶段观测仍未通过代表性门槛

2026-11-16接收提交5dacc2a8。本次是旧fence作用范围A/B（A全部on，B仅204全部数据FADD off）的补跑，不是新三配置fence_scope实验，没有C（tail-only）结果。

该提交替换了同名observe_02的部分A原始文件/汇总并新增B。此目录仅分析当前5dacc2a8版本；此前4c8d40c6的A=6.34%扰动报告属于上一组记录，不能与本次B拼接。旧值可从Git历史核查，不回填到新文件。

## 1. 运行及身份验证

- 六项正式任务、两端共12个case，status rc=0、所有checks=true，原始Normal正确性检查通过。
- 同一端A/B各项module、boot、源码/诊断指纹、运行库及采样协议一致。202 boot=1c7ff953…，204 boot=dee44112…。
- 实际transport日志202始终fence on，204 A on/B off，FADD STRONG、WRITE RELAX、窗口8、completion all、lane-mixed不变。
- 各模块仍是此前be76300e00…/bd1cfa319a…，normal_atomic_role_version缺失（按0处理），没有新Tail/Head分类能力，不能解释成三配置结果。
- 运行成功不等于采集代表性通过。工具重新读取原始样本、事件、状态和身份，未直接采用上传汇总结论。

## 2. 关闭采集的BF16净性能

主指标为两端对应样本max(node0,node1)的均值，均由202端主导；不是两端总和或跨机全局makespan。

| 关闭采集位置 | A两端on | B仅204数据off | 节省 | B变化 |
| --- | ---: | ---: | ---: | ---: |
| paced | 8.06807ms | 7.79204ms | 276.02μs | -3.4212% |
| post | 8.08693ms | 7.80029ms | 286.63μs | -3.5444% |
| 两位置等权均值 | 8.07750ms | 7.79617ms | 281.33μs | -3.4829% |

两组关闭采集比较与此前严格ABBA的3.35%/3.52%方向和幅度一致，为约3.4%的有限净收益增加了一次同启动状态下的复现支持。本次任务顺序是A paced/trace/post后B paced/trace/post，并非新的ABBA；两个控制位置也不是两次完全独立、顺序反转的实验。

## 3. 采集代表性不通过

| BF16 | A trace相对A控制 | B trace相对B控制 |
| --- | ---: | ---: |
| 202 | +1.6560% | +2.1099% |
| 204 | **+62.0863%** | **+5.3795%** |

A的204关闭采集为6.62824/6.62238ms，开启采集10.73872ms；B对应6.61825/6.62238/6.97646ms。两端前后控制稳定，说明不能用整体运行漂移解释采集项的大幅改变。A FP8的204采集影响另达+104.37%。

两端BF16各9个有效窗口，gpu_before_origin和scheduler_error均为0，CPU/GPU丢记录为0；但时钟/覆盖检查通过不能消除实际执行扰动。

更关键的是两端配对最大耗时：采集中A=10.73872ms、B=7.96066ms，表面改善25.8695%；关闭采集只有约3.48%。差分观察器效应达到22.3867个百分点，超过预设1个百分点；因此不能把25.87%当成真实优化幅度。decision.json保留control_gain_reproduced=true、eligible_for_mechanism_review=false。

## 4. 原始trace出现的机制线索（仅限带观察器运行）

每组各端1188对同proxy/ring、连续WR、同endpoint、payload/token对应的BF16 WRITE/FADD，通过逐对校验。

| 204端观测 | A on | B off |
| --- | ---: | ---: |
| WRITE提交结束→CPU取到通知，均值 | 390.57μs | -2.33μs |
| WRITE提交结束→FADD提交结束，均值 | 396.45μs | 5.72μs |
| WRITE完成观测→相邻FADD完成观测，中位数 | 639.75μs | 0.17μs |

负的第一个间隔表示通知命令已在WRITE提交结束前被CPU读出，符合先收集多条命令再提交的路径，未裁剪成0。单次均值区间可加；各自中位数不能相加。

这表明在**这些带扰动的记录中**，204 fence off确实伴随通知推进节奏/成对完成观测间隔改变，支持继续缩小fence作用范围的实验方向。但A/B观察器扰动严重不对称，不能据此宣称未采集程序中639μs等待全部被消除，或这些变化就是281μs净收益的精确来源；没有为追求阶段结论降低验收门槛。202仍fence on，其通知提交间隔约289.43/290.92μs，并未出现204同样改变。

## 5. 后续工作

旧observe采集系列到此停止，不继续重复A/B、增加轮次或补收历史错误。

下一项仍是已经推送30939c8c的[三配置关闭采集实验](../../FENCE_SCOPE.zh-CN.md)：新模块明确标记Tail/Head，A全部on、B仅204全部数据off、C仅204 Tail off/Head on，固定ABC-CBA。目的为检验能否缩小放宽范围并保留约3.4%收益，不继续追求完整阶段归因。

必须使用文档指定的新BUILD=urma_fence_scope_v1、JOB=urma_fence_scope_01，不能沿用本次旧模块或observe_02任务。A/B/C均要求normal_atomic_role_version=1及实际角色日志，缺少会拒绝。当前新scope结果目录未上传。仍只测URMA；候选稳定后再安排同口径RDMA最终对照。

原始计算输出：[BF16净耗时](bf16_api.csv)、[门槛判定](decision.json)、[带扰动请求间隔](request_intervals.csv)、[A控制](all_on/api_controls.csv)、[B控制](peer_off/api_controls.csv)。本目录为实际数据离线导出，无新增GPU或合成测量值。
