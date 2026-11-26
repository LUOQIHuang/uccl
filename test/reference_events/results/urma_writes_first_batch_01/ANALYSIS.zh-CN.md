# WRITE/FADD分阶段提交：改动生效，但完整Dispatch无收益

结果提交58f4c5de；两端新构建及四项ABBA均已上传。开发侧重新执行summarize_submit.py，从原始样本、运行身份及CPU提交记录复算，全部CSV与服务器summary逐字段一致。两端共8份status均为rc=0、全部checks通过。此轮来源记录齐全，未沿用上一轮缺元数据的验收结论。

## 1. 完整BF16 Dispatch结果

固定4096 tokens/rank、hidden7168、experts256、topk8、RDMA chunk32；当前202c1/204c0设备、202全开fence/204仅tail-off、窗口8、ordered回收、completion all。每项5预热、10采样，按原计划保留后9条；正式性能计时时不启用CPU/GPU事件采集。

主指标为同序号两端本地API耗时取max后求均值，不是全局同步时钟的makespan。单位μs。

| 配对 | A：原lane-mixed | B：writes-first | B相对A | 最大单端CV |
| --- | ---: | ---: | ---: | ---: |
| r01，A→B | 7543.381 | 7545.856 | +0.0328% | 1.1605% |
| r02，B→A | 7535.360 | 7526.002 | −0.1242% | 1.0809% |

第一配对慢2.475μs，第二配对快9.358μs，变化均远小于预定3%无实质变化门槛。A轮间漂移−0.1063%，B轮间漂移−0.2631%，均通过3%门槛；各组CV通过5%门槛。判定为no_material_change_keep_default，不将约0.1%的变化称为优化。

逐端均值也没有明显改善：

| 配对 | 节点 | A原方式 | B分阶段提交 |
| --- | --- | ---: | ---: |
| r01 | 202 | 7500.032 | 7513.913 |
| r01 | 204 | 7508.622 | 7506.716 |
| r02 | 202 | 7501.397 | 7506.062 |
| r02 | 204 | 7498.638 | 7503.986 |

FP8两配对为+0.0170%/+0.2065%，同样无收益。Layout/Combine保留为控制项；Combine约132～136ms的历史数量级疑点不在本轮解决，不与早期约7ms的Combine混合计算总收益。

## 2. 不是“改了开关但实际没改变”

每项BF16计时结束后额外运行一次相同Dispatch，仅采CPU提交记录。此记录只用于核查SDK链覆盖，任何采集延迟都不进入上表。

下表为各端4个Proxy合并后，以大payload WRITE字节量加权的混合链覆盖率：

| 配对 | 节点 | A：WRITE与FADD同链 | B：WRITE与独立FADD分阶段 |
| --- | --- | ---: | ---: |
| r01 | 202 | 58.391% | 0% |
| r01 | 204 | 63.137% | 0% |
| r02 | 202 | 56.035% | 0% |
| r02 | 204 | 62.353% | 0% |

A每端每轮均远超预定10%覆盖门槛，B全部为0。单次CPU witness受采集影响，不当作未采集时的精确分组比例，但可以确认目标提交方式实际执行。每份witness均有每端144条数据WRITE；202每次58498944B，204每次58556352B，A/B一致。不是靠少发数据、删请求换取结果。

模块、编译来源、库、boot和固定负载一致性由严格汇总器核验，实际normal_submit_schedule及fence/窗口/完成策略由runtime/status确认。A与B只改变每个Proxy一次收集批次内的SDK分阶段提交组织，没有等待全Dispatch的所有WRITE发送完再发FADD。

## 3. 对定位意味着什么

1. **关闭当前writes-first优化方向。** 在本次配置中，即使将明确存在的混合SDK链拆开，完整Dispatch也未获得可见净收益。保留lane-mixed，不增加提交间隔/批次/lane遍历扫描，不重跑同组ABBA。
2. **修正对提交组链的优先级。** 此前把它作为CQE可读之前的一项实现差异来验证，有明确可干预对象；现在结果没有支持它是当前主要可优化代价。不能继续用“URMA混合组链不同于verbs”解释主要性能差额。
3. **它不推翻此前完成可读边界。** 上轮选中慢请求的等待仍主要在CQE可读之前；本轮只是没有靠这种分阶段提交组织消除它。设备内部顺序、资源排队、GPU P2P访问、远端访问和完成生成尚未分开。
4. **没有新增URMA/RDMA差距结论。** 本轮是同URMA环境的A/B，不能称URMA已经追平RDMA，也不能把历史差距继续精确写成10%。需要同口径另一后端记录才能更新差额。

当前不追加这组测试。先补交上轮normal_completion已存在的元数据，完成原本就应该完成的来源验收，不耗GPU机时。下一项实机实验必须转向尚未排除的CQE可读前路径，并明确干预量、完整API结果或匹配负载吞吐；不再以CPU已就绪后取完成、简单扩窗或当前SDK拆链作为主要突破口。尚没有直接支持某一项硬件内部代价占多数的数据，不为了继续测试而发出新参数矩阵。

## 4. 复算

```bash
python3 tools/reference_events/summarize_submit.py \
  --root test/reference_events/results/urma_writes_first_batch_01 \
  --job urma_writes_first_batch_01 --cadence batch \
  --output /tmp/urma_writes_first_independent_review
```

输出目录必须不存在。原始来源及独立复算摘要见[independent_review.json](independent_review.json)。上轮缺失文件仍为42条必需路径，列表在[normal_completion的context](../../../normal_completion/results/normal_completion_01/raw_review/context.json)；补收命令沿用[运行文档第5节](../../WRITES_FIRST_BATCH.zh-CN.md)，不重做setup或GPU运行。
