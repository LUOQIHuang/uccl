# WRITE完成观测之后，URMA通知FADD仍有约0.55～0.66ms的间隔

本次只做离线分析，不运行新benchmark。输入为此前验收的verbs_ref_4096_matched_on_01、urma_ref_4096_matched_on_01，以及本轮已通过BF16前后控制门槛的urma_boundaries_01_trace。FP8不纳入。

## 如何确定观察的是对应的通知

每个CPU窗口，按(proxy,wr_id)分组；选择payload_bytes≥4096的大WRITE，其下一条命令必须是同ring的wr_id+1 FADD，并要求：

- 同一传输endpoint。
- WRITE、FADD各有一个post_begin、post_end、CQE，不用拆分/抑制完成的记录冒充成对完成。
- FADD的正增量与payload字节严格对应：payload_bytes=notify_tokens×14352，14352为BF16 hidden7168×2B加16B元数据。
- 每窗132对、9个有效窗口，每端/campaign1188对；六组共7128对均通过。
- 不按完成间隔的大小/正负、是否提前提交筛选配对。

这是源码相邻命令及内容约束的关联，不是新增协议携带的配对ID，也不测远端GPU实际看到tail的时刻。

## 成对CQE间隔

时间均来自同一端CLOCK_MONOTONIC_RAW；无跨机相减。

| campaign/节点 | 配对数 | WRITE CQE→FADD CQE 中位μs | P95 μs | FADD已在WRITE CQE前提交 |
| --- | ---: | ---: | ---: | ---: |
| 历史verbs / 202 | 1188 | **4.700** | 9.186 | 1188/1188 |
| 历史verbs / 204 | 1188 | **4.180** | 9.013 | 1188/1188 |
| 历史URMA / 202 | 1188 | **545.075** | 1054.532 | 1188/1188 |
| 历史URMA / 204 | 1188 | **659.745** | 1232.211 | 1188/1188 |
| 本轮URMA / 202 | 1188 | **546.275** | 1072.514 | 1188/1188 |
| 本轮URMA / 204 | 1188 | **632.545** | 1230.721 | 1188/1188 |

两轮URMA以不同采集配置重复看到同量级现象；不能将跨campaign相同序号样本当同步配对，但方向稳定。历史verbs与URMA有内核、设备、SDK差异，仍不能隔离为单纯软件实现成本。

## 不只是“FADD在WRITE完成后才被提交”

| campaign/节点 | FADD post_end−WRITE post_end 中位μs | WRITE CQE−FADD post_end 中位μs |
| --- | ---: | ---: |
| 历史verbs / 202 | 8.835 | 1979.225 |
| 历史verbs / 204 | 9.095 | 2070.185 |
| 历史URMA / 202 | 265.755 | 1032.815 |
| 历史URMA / 204 | 357.805 | 1537.955 |
| 本轮URMA / 202 | 284.895 | 1055.555 |
| 本轮URMA / 204 | 356.600 | 1507.535 |

URMA通知的提交本身比WRITE晚约0.27～0.36ms，也是值得保留的现象；但所有FADD仍在WRITE完成观测之前就已提交。尤其本轮204 FADD通常提前约1.51ms提交，WRITE完成后还约0.63ms才观察到对应FADD完成，不能把后者全部算成晚发通知。

各列分别取中位数，不做代数相加。SDK post_end只是软件返回边界，不等于设备真正取走WR。

本轮202/204分别有941/1188、928/1188个成对CQE间隔内出现至少一个稀疏SDK轮询心跳，说明这些间隔中CPU并非完全没进入轮询。它不能证明CQE硬件何时到达、是否已可消费，也不能把所有软件延迟排除。

## 能得出的结论和不能得出的结论

能支持的进一步定位：**通知FADD的提交后完成推进，是比“窗口是否太大”更具体的差异位置。** 它与先前FIFO队首ATOMIC迟完成、阻塞后续命令的记录一致。

不能据此声称：

- 已测得URMA硬件atomic单操作延迟为0.63ms。
- WRITE CQE意味着远端GPU已观察到所有payload数据或通知。
- 将1188对间隔相加，就能解释整个Dispatch时延。
- 只要去掉URMA fence便可正确且更快。
- 全部后端差额由这个间隔独立造成。

优先需要审查的实现位置：Proxy构造FADD的fence=true；UrmaTransport::prepare设置ATOMIC STRONG/fence；SDK/endpoint实际依赖及GPU控制内存的atomic访问。verbs Native FADD只显式置IBV_SEND_SIGNALED，并没有同名IBV_SEND_FENCE字段设置；字段表面不同不等于两设备语义可直接互换。

## 复算

```bash
python3 tools/reference_events/audit_notify_completion.py \
  --verbs-trace test/reference_events/results/verbs_ref_4096_matched_on_01 \
  --urma-trace test/reference_events/results/urma_ref_4096_matched_on_01 \
  --current-trace test/reference_events/results/urma_boundaries_01_trace \
  --output /tmp/notify_completion_audit
```

输出目录须不存在。notify_pairs.csv保留全部7128对原始本机时间戳、关联依据和间隔；notify_pair_summary.csv汇总中位/P95、提前提交和心跳数量。context.json记录各组源码、运行环境与局限。工具复核trace正确性/覆盖及来源；原始campaign性能验收见各自报告，不由本工具重新运行。
