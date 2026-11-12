# URMA boundaries 首批128-token结果分析

输入提交 `a87f5c62`，打点实现 `de66ec0f`。本次仅收到 `urma_boundaries_01_smoke128` 的node0/202和node1/204；4096-token paced、trace、post尚未收到。这里只分析现有数据，不要求重跑已通过的smoke。

## 已确认的结果

两端status均rc=0，全部checks为true。本地重新执行 `event_runtime.validate(case,3,'boundaries',128)` 和 `correctness_passed(case,'boundaries',128)` 亦通过。

- 两端真实Normal正确性测试通过，新12流ABI在实际MetaX环境下能运行。
- 每端FP8/BF16各3个采集窗口；按既定规则丢index=0，保留index=1、2。下文每组阶段中位数来自2个独立窗口×12通道=24条观测，不能称为24次独立benchmark。
- 每个窗口事件0～9覆盖12个通道，事件5允许多次，GPU/CPU均零丢记录。事件10/11为0符合128-token预期：实际每通道只有1条远端payload WRITE，没有第6条。
- 同一窗口有108～112个不同GPU采样主机时间值，未出现整窗时钟冻结；保留样本scheduler_error=0，没有GPU事件早于CPU窗口origin。
- 两端sources/SDK/编译flags一致；窗口8、WRITE relax、lane-mixed、completion all，运行库摘要一致。每端模块哈希分别记录，未要求两台机器构建出的二进制相同。

## 新增GPU边界已经有数据

单位μs，均为24条通道观测的中位数。各角色并行，以下跨度不相加，不表示完整关键路径。

| 节点 | 数据 | 末WRITE及通知发布 E3→E4 | 末块tail检查总跨度 首E5→E6 | 最后token epoch检查 E7→E2 | 最后远端token可见→Receiver结束 E2→E9 |
| --- | --- | ---: | ---: | ---: | ---: |
| 202 | FP8 | 41.67 | 222.14 | 7.24 | 66.76 |
| 204 | FP8 | 43.85 | 214.22 | 7.01 | 68.06 |
| 202 | BF16 | 41.90 | 331.52 | 7.70 | 76.19 |
| 204 | BF16 | 41.36 | 291.21 | 6.54 | 81.54 |

这批已测边界中，较大的跨度落在“Forwarder开始寻找末块tail，到实际取得可处理数据”这一段，而不是最后一个token的epoch检查。它是需要由4096-token结果进一步核实的具体位置。

**首E5→E6包含中间round-robin工作，不能称为纯tail自旋或网络时延。**最终成功检查轮次的E5→E6中位数分别为202 FP8/BF16 17.73/50.64μs、204 FP8/BF16 7.78/6.89μs。两种口径差别明显，说明把总跨度直接全部归因于tail spin会误导。

短跨度还受观察器开销影响。128-token每通道首条和末条payload WRITE是同一条，E1和E3在代码上相邻，但E1→E3仍读到约13.0～13.8μs；这不是后续payload准备时间，而包含前一个打点的开销。因而不能把约7μs的epoch跨度或约42μs的发布跨度直接解释为未经打点的真实算子耗时，也不能简单减掉固定常数修正。

## CPU记录提供了另一条线索

只统计带payload_bytes且≥4096B、具有poll/post_begin/cqe的远端大WRITE；每组2窗口×12请求=24条。不是16B元数据或atomic。

| 节点 | 数据 | CPU发现命令→开始提交，中位μs | 开始提交→软件观测CQE，中位μs | 后者P95 μs |
| --- | --- | ---: | ---: | ---: |
| 202 | FP8 | 5.40 | 103.42 | 117.19 |
| 204 | FP8 | 4.75 | 79.31 | 88.96 |
| 202 | BF16 | 4.57 | 202.42 | 214.97 |
| 204 | BF16 | 5.05 | 166.89 | 180.05 |

在当前小负载中，大WRITE的可见时间主要位于提交后的完成观测区间，而不是CPU发现命令后的提交准备区间。BF16 payload为100464～157872B，FP8为51856～81488B，约1.94倍；完成观测区间中位数约增加到1.96倍/2.10倍。这个现象与数据量敏感的提交后路径相符，但不能据此单独认定网络、URMA设备或显存访问是根因。

每窗口每个Proxy只有1条心跳，总计4条，未形成相邻心跳间隔。**本批不能排除Proxy迟轮询造成CQE晚被观测，也不能将post→CQE当作纯硬件传输时延。**128-token不为补心跳而重跑，继续现有4096-token三项即可。

## 当前不能直接用于性能回退归因

| 节点 | 数据 | Dispatch API有效均值μs | CV |
| --- | --- | ---: | ---: |
| 202 | FP8 | 1488.00 | 11.26% |
| 204 | FP8 | 2185.86 | 15.86% |
| 202 | BF16 | 1486.34 | 11.07% |
| 204 | BF16 | 2107.65 | 23.45% |

只有2个有效样本，且没有同模块paced/post控制。本批证明打点能运行并取得有区分度的记录；不证明打点性能代表性，也不能与旧4096-token verbs数据相减解释生产形状的回退。不同角色跨度及CPU请求亦不拼成跨机器、可相加的关键路径。

## 接下来只做现有计划

继续正在运行的4096-token paced→trace→post，不改变代码或传输参数。收到后依次检查：

1. 前后关闭采集的漂移、trace扰动及CV是否通过既定5%门槛。
2. 首次WRITE→末payload就绪是否仍占主要跨度，还是尾部tail/epoch/输出收尾拉长。
3. 第6条及末条WRITE在GPU准备/发布、CPU发现/提交、post→CQE哪个区间变长；心跳完整夹住请求时，才用短心跳间隔排除长时间未轮询。

若本轮URMA确认大跨度在哪，下一次verbs环境仅补同边界对照；现有旧verbs三点不能替代新增点的对照。

## 可核查附件

- `boundary_spans.csv`：768条原始通道跨度及同角色device cycles、读钟cycles。
- `boundary_medians.csv`：上述跨度的中位数、P95、最小/最大值。
- `cpu_writes.csv` / `cpu_write_medians.csv`：192条WRITE记录（含元数据，kind区分）及汇总。
- `api_samples_summary.csv`：原始5项API汇总，不将其相加。
- `capture_quality.csv`：8个保留采集窗口的丢记录、心跳和时钟检查。
- `context.json`：输入提交和采样口径；performance_representative为null，表示尚无匹配关闭采集对照，不表示通过。

原始输入保留在相邻的 `urma_boundaries_01_smoke128` 目录。分析直接复用 `event_runtime.validate/correctness_passed` 和 `summarize_boundaries.spans`，无额外剔除离群点。
