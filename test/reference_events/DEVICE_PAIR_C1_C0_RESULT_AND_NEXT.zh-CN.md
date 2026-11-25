# 指定设备组合已通过，恢复Normal阶段定位

输入提交 `a0a2e10b`，结果 `results/urma_device_pair_01/urma_c1_c0_01/`。

## 1. 本次结果

| 项目 | 202 | 204 |
| --- | --- | --- |
| 设备 | `udmac1d1e2` | `udmac0d1e2` |
| rc | 0 | 0 |
| status中的全部检查 | 通过 | 通过 |
| Normal正确性配置 | 24组完成 | 24组完成 |
| 错误完成/Dispatch CPU超时 | bench中未出现 | bench中未出现 |
| Normal数据FADD fence | 全开 | 仅tail-off |

首次屏障已完成，后续Dispatch/Combine及退出握手都通过。保留当前组合，停止把寻找UDMA源码当作恢复性能测试的前置条件。

与此前c0/c0失败的 `normal_scope_urma_02_connect` 对照，各节点boot ID、生产sources_hash、build_hash和扩展module_sha分别相同；工作负载仍为128 tokens、隐藏维7168、topk8、专家256、paced、关闭活动采集。主要实验干预是202从c0改为c1。该结果有力支持设备选择与此次故障有关，是恢复运行的有效配置；没有同期反向复现及完整设备路径追踪，不将它写成已证明的EID冲突或具体硬件内部根因。

此前UMMU权限异常对应当前通信故障，不能直接当作历史Normal性能落后的原因。恢复通信后回到整体差距及阶段定位。

## 2. 128-token诊断样本

单位μs，warmup=1、计时3次丢首值，每点仅保留2个样本。

| 点 | 202均值 | 204均值 |
| --- | --- | --- |
| FP8 Dispatch chunk32 | 3182.98 | 3285.89 |
| BF16 Dispatch chunk32 | 3189.76 | 3254.14 |
| Combine chunk12 | 4174.98 | 4185.09 |
| Combine chunk16 | 4169.22 | 4166.14 |

这只是门禁诊断值，不是稳定性能结论，不能与历史4096或不同计时形态的RDMA数据计算改善百分比。204 FP8两样本范围2753.79–3817.98 μs，Combine也有约5%–6% CV，不能用两个点声称稳定。

两个bench的kernel_before/after可见记录未见0x13，但结束清理附近存在TP level AE。仅凭这些尾部不能宣称系统没有任何设备事件。上传结果缺console/node1，202的2000行journal也没有可对齐的尾部重叠；完整窗口审计有局限。这不否认两端rc=0及正确性通过。保留已有204 console文件即可，不为补日志重跑本次connect。

## 3. 下一步及必要性

不再运行connect或设备/EID扫描。复用同一构建、同一设备组合：

1. 一次128-token **trace门禁**：本次通过的是关闭采集的paced，尚未验证该设备组合下活动稀疏打点的正确性。门禁通过后才进入正式阶段。
2. 4096 tokens原定五项 **coverage**：batch_pre → paced → trace → post → batch_post。测实际整体时延、采集扰动/状态漂移，以及GPU发布、CPU读取/提交/完成、接收可见和输出边界。

本轮没有增加新打点、fence/队列参数扫描或LL测试。目标先验证新路径上Normal BF16 Dispatch的实际水平，再定位最大差异区间。现有RDMA旧数据如拓扑/配置不匹配，只作背景，不据此宣布追平或超越。

## 4. 两端运行

两端拉取同一提交；已通过的connect无需重跑，扩展无需因这些shell修改重新编译。

```bash
cd /home/h00918771/uccl-ep-xpu-integration-new
git pull --ff-only https://github.com/LUOQIHuang/uccl.git codex/metax-urma-integration
git rev-parse HEAD
```

202先启动，204尽量在60秒内启动对应命令：

202：

```bash
bash test/reference_events/try_device_pair.sh --node-rank 0 --part smoke
```

204：

```bash
bash test/reference_events/try_device_pair.sh --node-rank 1 --part smoke
```

两端均出现 `BOUNDARY_SUITE_PASSED ... part=smoke` 后，再执行：

202：

```bash
bash test/reference_events/try_device_pair.sh --node-rank 0 --part coverage
```

204：

```bash
bash test/reference_events/try_device_pair.sh --node-rank 1 --part coverage
```

两端默认JOB仍为 `urma_c1_c0_01`。smoke与coverage自动使用新的后缀目录，不覆盖原connect。若这些后缀已存在，两个阶段、两端均追加相同新 `--job`；coverage需要该JOB的trace门禁通过记录，不能跳过。

默认构建、Python、MACA、接口、私有库与[设备组合运行说明](DEVICE_PAIR_C1_C0_RUN.zh-CN.md)一致。构建路径不同可追加 `--build-dir /实际目录`。失败即停，保留失败目录，不重复前面已经完成的阶段。不要先跑旧formal再跑coverage。

## 5. 提供哪些结果

提供完整 `test/reference_events/results/urma_device_pair_01/`，包含原connect和两端新smoke128、batch_pre、paced、trace、post、batch_post，以及已有console日志。自动收集status、runtime、setup、样本和事件记录。无需.so、objects或重新收集源码。

数据到齐后可在开发侧离线汇总，不额外占用GPU：优先看4096 BF16整体与慢端，再检查trace扰动，最后分析与最终推进对应的最大阶段增量。并行角色的区间不强行相加为总延迟。
