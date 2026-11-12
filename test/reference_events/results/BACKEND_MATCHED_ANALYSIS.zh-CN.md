# verbs/URMA Normal同口径阶段对照：优先检查BF16的提交后长尾及远端可见性推进

本次接收提交29982f6b，verbs_ref_smoke128_02、verbs_ref_4096_paced_01、verbs_ref_4096_matched_on_01两端齐全且status rc=0、所有checks=True。独立从原始样本和事件重跑summarize_matched.py，输出usable=True；6个CSV与上传verbs_ref_matched_summary_01逐字节相同。

与已验收的urma_ref_4096_paced_01 / urma_ref_4096_matched_on_01对照，本轮开始有可用的同口径阶段观察。**当前最明确的整体差异是202端BF16；提交前CPU处理和SDK调用差异很小，提交后长尾及GPU数据可见性推进值得优先检查。还不能宣布唯一根因。**

## 可比条件与采集验收

- 两后端生产源码manifest逐文件SHA相同，准备后的Normal benchmark SHA也相同；sources_hash汇总字段不同，不能据此误判生产代码变了。
- Torch均2.6.0+metax3.7.0.7；MACA均3.9.0.29、KMD版本字符串均3.10.14；实际加载的libmcruntime.so SHA相同。这里只核查有记录的库和版本，不声称所有系统库/驱动二进制均相同。
- 共同构建flag一致，USE_URMA分别0/1。4096 tokens、hidden7168、experts256、topk8，GPU0/每端1卡，SM24、Dispatch chunk32、NVL chunk4，FIFO。
- 内核不同：verbs为6.6.0-145.3.34.165.oe2403sp3.aarch64，URMA为6.6.0-gdr_f00955680_diag；传输设备/SDK、boot也不同，不能消除系统栈混杂。verbs为DMA-BUF、rocep40s0f0、GID3；URMA为udmac0d1e2、Normal WRITE RELAX、completion=all，FADD仍强序、fence保留。
- 各后端paced/on共用本端同一事件模块、源码、boot、设备、库及采样方式。每点5次预热/10次采样，丢首值后9个有效样本。每后端只有一组paced/on campaign，9个样本不能当成9轮独立复测。

| 节点 | Dispatch | verbs开启采集相对paced | URMA开启采集相对paced |
| --- | --- | ---: | ---: |
| 202 | FP8 | +1.856% | +0.277% |
| 202 | BF16 | +1.385% | +0.547% |
| 204 | FP8 | +1.358% | +0.903% |
| 204 | BF16 | +1.518% | +0.398% |

四个Dispatch测点均通过绝对变化/CV不超过5%的工程门槛；verbs paced/on各组CV最高1.334%，URMA最高1.348%。这不是所有内部阶段零扰动的证明，没有新post控制。verbs正式on每端20窗，GPU三个事件每窗各12条；有效每端18窗，GPU/CPU dropped=0、无GPU时间早于CPU origin。每窗396条完整业务请求链，verbs为396个唯一提交组，URMA另有使用UINT64_MAX的内部控制组，完整业务链数相同。每后端共14256条完整业务链、1296条有效GPU记录。verbs测量前后内核快照无新增行。

## 整体时延：以关闭采集paced比较

单位ms，数值为9个有效API样本的均值；变化为URMA相对verbs：

| 节点 | Dispatch | verbs paced | URMA paced | 差值 | 变化 |
| --- | --- | ---: | ---: | ---: | ---: |
| 202 | FP8 | 5.669 | 5.905 | +0.236 | +4.165% |
| 204 | FP8 | 5.681 | 5.838 | +0.157 | +2.759% |
| 202 | BF16 | 7.149 | 8.114 | +0.965 | +13.502% |
| 204 | BF16 | 6.826 | 6.703 | −0.124 | −1.813% |

on计时方向一致：202 FP8 5.774→5.921ms（+2.550%），204 FP8 5.758→5.891ms（+2.298%）；202 BF16 7.248→8.158ms（+12.563%），204 BF16 6.930→6.729ms（−2.897%）。204本地BF16更快不等于两端任务整体更快；当前慢端202仍落后。

这是逐样本同步的paced负载，不用来替代历史连续调用基线。FP8差距较小，且只有一组campaign，暂不把这个百分比当成已稳定确认的后端回退幅度。

Combine没有专门打内部事件，paced读数URMA约慢1.57%（chunk12）/2.11%（chunk16）；on读数反而快约1.1%～1.2%。因此这里不把Combine的小差距作为已定位的稳定回退。

## CPU阶段：不能只看中位数就认定排队更快或更慢

每节点/测点汇集9窗×396条完整链，独立取各段中位数，单位μs。它们跨请求并行，不能相加到API时延。

| 节点/负载 | decoded→prepare verbs / URMA | prepare→post_begin verbs / URMA | post_begin→post_end verbs / URMA | post_end→最后CQE verbs / URMA |
| --- | ---: | ---: | ---: | ---: |
| 202 FP8 | 3.25 / 3.31 | 0.48 / 0.54 | 0.71 / 0.76 | 569.62 / 598.47 |
| 202 BF16 | 3.01 / 3.33 | 0.47 / 0.61 | 0.71 / 0.79 | 1294.78 / 1066.44 |
| 204 FP8 | 3.13 / 3.30 | 0.45 / 0.51 | 0.70 / 0.73 | 588.69 / 624.46 |
| 204 BF16 | 3.02 / 3.22 | 0.47 / 0.56 | 0.70 / 0.75 | 1373.94 / 1313.69 |

poll→decoded均0.03μs；CQE→ack_slot的verbs约0.18μs，URMA约0.28～0.36μs。提交前/SDK边界只有小幅差异，当前不能拿这些请求中位数解释202 BF16的近1ms整体差额；也没有覆盖GPU发布到CPU首次发现的全部等待。

进一步拆请求类别，所有节点/负载的大块WRITE中位完成间隔URMA均更短，但**尾部反而更长**。最值得关注的是204本地向对端发送的BF16大块WRITE：

| 204 BF16：post_end→最后CQE | verbs μs | URMA μs |
| --- | ---: | ---: |
| payload WRITE P50 | 2082.37 | 1989.20 |
| payload WRITE P95 | 2289.92 | 3449.89 |
| payload WRITE P99 | 2314.30 | 3649.22 |
| payload WRITE 最大值 | 2324.16 | 3791.02 |
| atomic P50 | 723.28 | 737.44 |
| atomic P95 | 2260.28 | 3427.15 |

P95采用池化请求的线性插值分位数；这些是同一campaign中相关请求的分布，不是独立实验置信区间。202 BF16也有长尾扩大，例如payload P99 2266.03→2448.83μs、atomic P99 2248.33→2642.05μs。不能据“payload P50更短”排除URMA提交后路径。

204 BF16按每窗分别计算payload P95，verbs九窗范围2.204～2.314ms，URMA九窗3.201～3.607ms，范围不重叠；该尾部差异不只来自某一个窗口的孤立异常。逐窗表仍按各后端分别保留，不将两次campaign的同号样本当作同时发生。

本地CQE间隔混有设备/队列、依赖及CPU完成轮询；不等于网络线速传输时间，也不能判断哪部分占比最大。CPU采集在两端完成后的barrier之后才结束，特意保留晚到完成，因此最后一条CPU ack也不必落在本端API计时内。

## GPU观察：202 BF16差异主要出现在首次WRITE之后

每窗先取本机最早event1与最晚event2的跨度，再对9窗取中位数，单位ms。event1是GPU调用首次远端payload WRITE前；event2是Forwarder看到最后远端token可见。**这是本地跨角色的观察包络，不是同一WR的网络耗时，也不是完整kernel关键路径。**

| 节点 | Dispatch | 首次WRITE→最后远端token可见 verbs | URMA | 差值 |
| --- | --- | ---: | ---: | ---: |
| 202 | FP8 | 4.200 | 4.206 | +0.005 |
| 204 | FP8 | 4.198 | 4.183 | −0.016 |
| 202 | BF16 | 6.428 | 7.110 | +0.682 |
| 204 | BF16 | 6.050 | 5.497 | −0.553 |

202 BF16首token→最后远端可见的包络为6.823→7.531ms，+0.708ms；同Sender CTA首token→首次WRITE的窗口内中位跨度约0.430→0.449ms，仅约+0.019ms。其他节点/负载的同CTA首批跨度也很接近。CPU origin到GPU首点的偏移不是kernel启动延迟测量，不能用它单独宣布启动慢了约0.2ms。

GPU时间来自周期更新的映射主机时钟，存在采样滞后、记录成本和调度空隙。没有kernel入口/最终Receiver结束点；并发事件包络和各自中位数不具可加性，不能用0.682ms直接计算“解释了回退的多少百分比”。

## 当前能说到哪一步

1. **实际整体差异**：本轮paced Normal的202 BF16 URMA约慢13.5%，204本地没有相同回退；FP8只见约2.8%～4.2%的小幅差距。
2. **优先候选区间**：202 BF16首次WRITE之后到远端数据可见的推进更长；对应204发送侧的BF16 WRITE/atomic完成长尾也更长。两种记录共同支持优先审查204→202方向的提交后尾部、排序/通知/完成推进及接收可见性。
3. **仍未区分的机制**：设备内部排队、顺序依赖、CPU轮询、GPU/FIFO反压及接收等待仍可能相互作用。当前不能将候选区间等同于“CPU算得慢”“GPU纯计算慢”或“链路线速慢”，更不能以请求中位数排除关键长尾。
4. Normal弱序保持不变。强序对照已经证明STRONG明显更慢，不为本轮定位改变WRITE/FADD/fence语义。

这批数据先完成离线尾部分布和事件窗口对照，不要求再次运行同一套128/paced/on。无需为了已有验收再次重编译或重测；本次新增的只是离线比较工具，没有修改GPU/传输生产路径。

## 复算与数据

从仓库根目录运行，输出目录须不存在；不需要GPU、特定内核或额外Python依赖：

```bash
python3 tools/reference_events/compare_matched.py \
  --verbs-control test/reference_events/results/verbs_ref_4096_paced_01 \
  --verbs-trace test/reference_events/results/verbs_ref_4096_matched_on_01 \
  --urma-control test/reference_events/results/urma_ref_4096_paced_01 \
  --urma-trace test/reference_events/results/urma_ref_4096_matched_on_01 \
  --output /tmp/uccl_backend_matched_review_01
```

本报告对应导出目录[backend_matched_comparison_01](backend_matched_comparison_01)：api_comparison、cpu_stage_comparison、cpu_operation_comparison、cpu_completion_tails、逐窗cpu_completion_windows、gpu_span_comparison、逐窗gpu_windows、capture_quality及context。工具重新验收两后端的paced/on，再检查生产源码、准备后benchmark、共同flag、节点、Torch、记录的运行库及GPU版本，保留两种内核/设备/SDK差异；不将它们强行视为同一环境。
