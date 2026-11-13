# FADD fence实验：BF16 Dispatch稳定改善约3.43%，属于部分收益

结果提交b83cdd95；分析日期2026-11-13。主指标为每个对应样本max(node0,node1)的均值，不是两机全局makespan。四项4096-token按A-B-B-A运行，每项5次预热、10次采样、丢首值后9个有效值。A为两端fence on，B为仅204数据FADD fence off；FADD始终STRONG，WRITE RELAX、窗口8、completion all、lane-mixed不变。

## 1. 数据可用性

- 两端128-token smoke及四项正式运行共10个case，rc均为0、所有status checks均为true，原始Normal正确性检查通过。
- runtime能力版本均为1，实际transport日志中202始终on，204按A/B切换on/off；fadd_order=strong、barrier_fence=per_request。
- 各端在全部任务中各自模块SHA、boot、源码/诊断指纹、库及采样口径保持。两端分别构建，模块SHA不要求跨端相同。
- reference_control确认GPU/CPU事件与host clock均关闭。此次只测净性能，没有新的阶段观测。
- 离线从原始样本重算，per_node、paired_calls、rounds、decision四份CSV与上传结果逐字一致。Windows重算context的benchmark_sha与服务器不同，经核对是检出文件CRLF/LF差异；分析使用服务器指纹，不把该文件摘要混入跨平台源码一致性结论。

## 2. BF16主结果

| 配对 | A 两端on | B 仅204 off | 节省 | 变化 | 相关最大CV |
| --- | ---: | ---: | ---: | ---: | ---: |
| r01 A→B | 8.08223ms | 7.81178ms | 270.45μs | -3.346% | 0.511% |
| r02 B→A | 8.08491ms | 7.80032ms | 284.59μs | -3.520% | 0.365% |
| 两配对等权合并 | 8.08357ms | 7.80605ms | 277.52μs | -3.433% | 不另合成CV |

两配对方向一致，A回到约8.085ms、B两次约7.80～7.81ms，效果明显大于项内波动。这支持本环境/形状下204数据FADD fence存在部分净性能代价；独立重复只有两配对，不把同一运行内9个样本视为9次独立实验。

汇总器给出threshold_not_met_keep_default，是因为事先规定至少5%的工程采用门槛。它不表示没有观察到收益，也不是统计显著性检验。应保留“稳定约3.4%的候选收益”，保持生产默认on。

## 3. 收益主要体现在哪端

以下为两配对内各端均值的等权合并：

| 节点 | A BF16 | B BF16 | 节省 | 变化 |
| --- | ---: | ---: | ---: | ---: |
| 202 | 8.08357ms | 7.80605ms | 277.52μs | -3.433% |
| 204 | 6.63242ms | 6.61881ms | 13.61μs | -0.205% |

只改变204，主要缩短202的本地API耗时，支持存在跨端推进耦合；不能据此称为202 GPU算术执行速度提升。干预范围包括204全部Normal数据ATOMIC（通知和反馈，也包括Combine相关请求），不是只改一条tail通知，因此尚不能唯一归因到某一种FADD。

## 4. 其他API

| API | A配对最大值均值 | B配对最大值均值 | 合并变化 |
| --- | ---: | ---: | ---: |
| FP8 Dispatch | 5.89875ms | 5.90454ms | +0.098% |
| Combine chunk12 | 133.07951ms | 133.16331ms | +0.063% |
| Combine chunk16 | 134.05949ms | 134.45531ms | +0.295% |

这些项没有同样的稳定收益。Combine两配对变化方向不同，本轮仅作为副作用检查；此处绝对时间来自当前测量口径，不与早期不同benchmark/tuning口径的毫秒表直接混用。正确性通过范围仅覆盖本次执行的形状/检查，不能推广为所有Normal形状均可关闭fence。

## 5. 不增加GPU实验的进一步定位

将此前已验收BF16 trace中的同proxy、同ring、连续WR编号、同endpoint且payload/token匹配的WRITE/FADD对，进一步拆分到CPU取出FADD的位置。三个campaign每端1188对、合计7128对，均通过校验。

以下是**均值**，同一行可相加；不是把不同区间中位数相加：

| 204端已有fence-on trace | WRITE post_end→CPU取到FADD | CPU取到FADD→FADD post_end | 合计 |
| --- | ---: | ---: | ---: |
| verbs历史匹配 | 5.44μs | 5.01μs | 10.45μs |
| URMA历史匹配 | 390.84μs | 5.69μs | 396.53μs |
| URMA boundaries | 388.52μs | 5.87μs | 394.39μs |

当前URMA约98.51%的这段提交间隔发生在CPU取到该通知命令之前；取到后解码/准备/SDK提交总共约6μs。202也有相同趋势：284.40+5.62=290.02μs，verbs约5.44+5.08=10.52μs。

这里poll是proxy.cpp中fifo->poll成功返回后的命令读取观测。第一段包含未知的GPU命令发布时间、FIFO预算/排队、CPU服务间隔等，不能直接叫CPU计算时间；也不能据此确认每一对都由同一种等待造成。与已有“预算满、队首ATOMIC尚无CQE”的FIFO重放证据结合，下一定位重点应是**通知被CPU取出之前的命令推进/回收链路**，而非FADD封装或SDK调用本身慢了数百微秒。

本次fence-off没有trace，不能声称上述388.52μs或632.55μs完成观测间隔已缩短277.52μs。净性能收益和旧trace机制证据分别成立，尚缺开关前后同一位置的直接观测。

## 6. 当前结论与后续边界

1. 比之前更进一步：已经从“通知推进可能相关”取得了一项顺序约束干预的稳定部分收益，约278μs/3.43%。
2. 它不是已经解释全部URMA-vs-verbs回退，也不意味着把所有FADD改RELAX；STRONG本轮从未改变。
3. 不重复相同ABBA，不扫描窗口、不改WRITE、不展开LL矩阵，也不直接将默认fence关闭。
4. 如继续上机，目的应限定为确认已观察收益是否对应通知取出前等待/FIFO回收改善，或区分数据通知与反馈原子请求；应复用同位置稀疏观测和前后控制。当前报告不把未执行的新实验写成结论，不要求用户立即重复旧命令。

数据：[逐配对](rounds.csv)、[逐端](per_node.csv)、[原始对应样本](paired_calls.csv)、[配置与来源](context.json)、[离线提交间隔拆分](../notify_submission_audit_01/submission_summary.csv)。

离线复现（不使用GPU；output必须为新目录）：

```bash
python3 tools/reference_events/audit_notify_submission.py \
  --verbs-trace test/reference_events/results/verbs_ref_4096_matched_on_01 \
  --urma-trace test/reference_events/results/urma_ref_4096_matched_on_01 \
  --current-trace test/reference_events/results/urma_boundaries_01_trace \
  --output /tmp/notify_submission_audit_new
```

工具记录原始event_trace SHA及身份指纹，保留有符号区间并逐对检查可加关系；中位数仍独立导出，不解释成可相加关键路径。新增离线拆分测试及原工具回归共18项通过。
