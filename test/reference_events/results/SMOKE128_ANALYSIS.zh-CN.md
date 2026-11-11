# 128-token 普通模块与事件模块：功能通过，正式性能验收待完成

更新：随后已收到4096-token的base/trace/post及汇总，下一步以[4096核查](FORMAL4096_ANALYSIS.zh-CN.md)为准；下文保留128-token当时的数据与流程，不重新执行已经完成的任务。

复核提交31f1a65b、b62ce411：urma_ref_connect_base_02、urma_ref_connect_trace_01，两端原始文件齐全。本报告重读status、setup/runtime、原始API样本和事件文件，不以提交标题代替验收。

## 已确认的进展

- 两端普通模块和事件模块共4个case均rc=0、所有checks为True，各有24组正确性测试。trace的reference_correctness.json均verified=True；离线重跑event_runtime.validate(case,3)及correctness_passed也通过。
- 普通模块reference_events_version=0，事件模块为1且运行开关on。同一节点base/trace的boot_id、生产源码、诊断脚本、私有库/运行库、形状和Torch一致；MACA3.9.0.29、KMD3.10.14。
- 两端各6个事件窗口（FP8/BF16各3次），全部窗口GPU事件0/1/2各12条，其余事件为0；GPU及CPU dropped均0，epoch及API样本一致。正确性长窗口的三个事件计数均576、dropped=0。
- 每个采样窗口可恢复36条完整poll→decoded→prepare→post_begin→post_end→cqe→ack_slot链。另有两次内部控制提交共用UINT64_MAX标识，不具备FIFO首尾链，不合并伪造为一个请求，不按丢失业务CQE解释。每窗口38次物理post，37个带post的(proxy,wr_id)组，其中36组完整。

因此本次确实打通了参考事件方案，不再只有layout。开启新打点也能成功建连和完成正确性测试，不支持“新打点必然导致绑定失败”。但没有严格记录并单独控制清理、等待和环境变化，不能进一步证明此前所有4096绑定错误只有一种原因，或宣称环境永久恢复。

## 本轮API数据

单位μs；每点warmup1、采样3、丢弃首个，实际只有2个有效样本。变化为trace/base−1，尚无post回测。

| 节点 | Dispatch | base | trace | 变化 | base/trace CV |
| --- | --- | ---: | ---: | ---: | ---: |
| 202 | FP8 | 2082.304 | 2118.656 | +1.75% | 14.84% / 15.08% |
| 202 | BF16 | 2669.184 | 1972.352 | −26.11% | 11.31% / 0.34% |
| 204 | FP8 | 1786.752 | 1336.576 | −25.20% | 0.85% / 1.97% |
| 204 | BF16 | 2392.960 | 1314.048 | −45.09% | 0.78% / 0.45% |

两端节点不能当作独立重复轮次。明显加速也属于待解释的测量差异，不能宣布新探针提升性能或已经满足5%代表性门槛。128-token小负载不能替代4096-token目标负载；正式summarize要求4096及规定样本数，拒绝本轮是预期行为。

## 可用的局部CPU观察

202节点，trace的有效窗口index=1，按每条完整逻辑链计算，再分别取中位数；单位μs。只描述这一个小负载采样窗口，不是稳定性结论或跨后端对照。

| CPU边界 | FP8 | BF16 |
| --- | ---: | ---: |
| poll→post_begin | 5.070 | 6.060 |
| post_begin→post_end | 0.930 | 0.930 |
| post_end→最后CQE | 74.765 | 167.570 |
| 最后CQE→ack_slot | 0.460 | 0.570 |

已能观察提交前、SDK调用和完成后退休。在本次窗口中，提交后到CQE被观察的间隔比SDK调用明显长；它仍包含传输、依赖/排队和轮询观察，不能据此认定网络慢，也没有对应verbs相同记录。多个请求重叠，各列中位数不可相加为Dispatch时延。

GPU事件记录也已完整获得，但它们只标识首次打包、首次远端WRITE调用和最后远端token的epoch可见；没有kernel入口/最终Receiver完成测点。CPU origin在CUDA Event提交前，不能把事件0相对origin的偏移全部叫作GPU kernel等待；也不能将本地GPU事件和CPU请求强行一一配对。

## 时钟与内核日志

- 各采样窗口的时钟线程均有更新、scheduler_error=0。clock_stats.max_gap_ns范围为59.66–92.60μs；该统计覆盖开始/结束边界，结束时还计入保守尾间隔，不能直接当成GPU实际时刻的误差界，也不能假定微秒精度。保留原始device_before/after cycles，后续正式分析需检查时钟质量。
- 对每个case的400行kernel快照按公共首尾衔接提取新增行：计时结束前均未新增TP level AE或bind失败；计时结束到销毁完成后，两端每个case各新增10条TP level AE type16/subtype0，邻近解绑/unmap。这不等于无驱动异常，但不能把快照中的旧绑定错误当成本次复发，也不能据销毁后告警解释计时内延迟。

## 下一步只补目标负载，不重编译、不重复smoke

沿用原来已构建好的BASE、TRACE和现有成功环境；普通模块、事件模块均已验证可运行。本轮只需README第5节的4096-token三项，按顺序两端各执行：

```bash
run_case baseline "urma_ref_base_01" "$BASE" 4096
```

```bash
run_case trace "urma_ref_trace_01" "$TRACE" 4096
```

```bash
run_case post "urma_ref_post_01" "$BASE" 4096
```

run_case及变量定义见[运行文档第2、4节](../README.zh-CN.md)；已有定义可直接复用，不重复第4节末尾smoke调用。base/post各50次预热、50次采样，49个有效值；trace各5次预热、10次采样，9个有效值。函数结束自动收集。

提供test/reference_events/results/urma_ref_base_01、urma_ref_trace_01、urma_ref_post_01三个完整目录，两端均需；文件要求沿用README第6节。已有JOB就统一改新尾号，不覆盖。任一步失败先保留日志并停止，不自动重试，不清理或重启其他共享任务。

这是URMA侧功能、测量代表性与局部事件的验收；当前还没有这套参考测点下的verbs对照。若4096扰动仍明显，则先分析现有控制与记录，停止用失真阶段数据归因，不原样重复扩大测试。
