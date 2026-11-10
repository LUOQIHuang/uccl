# Normal性能证据核查：已有结论与真正缺口

本报告只复核仓库已有日志和对应代码，没有新增服务器实验，也不修改生产通信实现。

## 结论

已有粗粒度证据将主要差距指向dispatch主kernel的执行区间，而不是独立notify kernel。主kernel包含计算、GPU等待及CPU/通信推进的影响，不能将它解释为纯GPU计算变慢。尚未定位到内部具体等待点，未证明Proxy CPU或SDK提交调用是根因。

此前“完全不知道在哪个阶段”的说法不准确；准确状态是：已缩小到dispatch主kernel区间，但两后端内部关键路径证据未采齐。继续扩大host微测、LL、Combine参数扫描不能补上这个缺口。

## 1. 可复核的粗粒度定位

单位μs，202节点。verbs为verbs_baseline_01的gpu_profile诊断；URMA为urma_phase_v1的r01_instrumented诊断。这些是历史各自环境中的profiler辅助数据，不是最新Normal基线的可相加分解。

| 范围 | verbs | URMA r01 | 差值 |
| --- | ---: | ---: | ---: |
| FP8 dispatch主kernel | 3391.00 | 3831.00 | +440.00 |
| FP8 notify kernel | 240.28 | 214.28 | -26.00 |
| BF16 dispatch主kernel | 6189.00 | 7123.00 | +934.00 |
| BF16 notify kernel | 228.38 | 217.17 | -11.21 |

URMA另外两轮的FP8主kernel为3825/3834μs、BF16为7115/7113μs，方向一致。这个证据支持将后续定位范围限制在dispatch主kernel及其等待依赖，但不能证明其中某个算子或CPU阶段造成全部差额，也不能直接拿440/934μs与另一轮基线差额相减。

原始来源：

- [verbs GPU范围](results/verbs_baseline_01/node0/gpu_profile/normal/gpu_stages.jsonl)
- [URMA r01](../urma_mode_parity/results/urma_phase_v1/urma_phase_v1_r01_instrumented/node0/normal/metrics.jsonl)
- [URMA r02](../urma_mode_parity/results/urma_phase_v1/urma_phase_v1_r02_instrumented/node0/normal/metrics.jsonl)
- [URMA r03](../urma_mode_parity/results/urma_phase_v1/urma_phase_v1_r03_instrumented/node0/normal/metrics.jsonl)

## 2. 为什么之前的阶段测试没有提供完整对照

逐文件核查所有现存结果中的transport timeline和非profiler GPU范围文件，得到：

| 证据 | 实际内容 | 不能支持什么 |
| --- | --- | --- |
| verbs_stage_01 diagnostic/node0/verbs_timeline.jsonl | 只有5个layout窗口，每个events为空；bench.log止于正确性测试开始，无成功status文件 | 没有verbs Dispatch逐请求时间线 |
| 同任务gpu_stages.jsonl | 只有5个layout窗口，version=1 | 没有成功的Dispatch内部GPU等待范围 |
| URMA notify_05 timeline，两端 | 各有FP8/BF16的index=0窗口，collect/prepare/post_begin/post_end/cqe | 只有首个诊断样本，不能提供轮间稳定性 |
| 上述URMA窗口 | 均没有ack_slot、tail_published事件 | 无法追到CPU完成退休及队列反馈，更没有GPU观察反馈的时间 |
| GPU范围v2 | test结果目录未发现version=2的gpu_stages.jsonl数据 | v2代码存在不代表硬件采集已成功 |

URMA现存日志的事件计数：

| 节点/负载 | collect | prepare | post_begin | post_end | cqe | dropped |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 202 FP8 | 430 | 430 | 398 | 398 | 398 | 0 |
| 202 BF16 | 430 | 430 | 398 | 398 | 398 | 0 |
| 204 FP8 | 430 | 430 | 398 | 398 | 336 | 0 |
| 204 BF16 | 396 | 396 | 364 | 364 | 279 | 0 |

collect含控制命令，不能要求其数量等于数据提交数。204窗口内缺少部分已提交请求的CQE记录；这不是硬件丢失CQE的证明，日志未覆盖完成观察也可能造成这种情况。不能补零或只选已完成请求来估计完整尾延迟。

两个内部控制操作使用相同的UINT64_MAX标识，不能把它们按同一个请求取最早/最晚时间拼成逻辑请求。去除这种重复标识，只保留五个CPU事件各出现一次且时间单调的链路，可恢复202两种负载各396条请求（144 WRITE、252 FADD），204分别334/277条。

当前运行driver对timeline的检查主要是文件存在；较严格的timeline_rows要求含ack_slot的完整链，所以不能用现存URMA日志通过完整链验收。旧日志按当时入口显示成功，不代表满足后来新增的细粒度证据要求。

## 3. 可以恢复的部分时间线，不能解释为后端差额

202节点，单个index=0诊断窗口，396条唯一完整五事件链，各段中位数，单位μs：

| 本地CPU边界 | FP8 | BF16 |
| --- | ---: | ---: |
| collect → prepare | 7.03 | 5.44 |
| prepare → post_begin | 3.75 | 2.32 |
| post_begin → post_end | 0.97 | 0.75 |
| post_end → cqe | 635.96 | 1179.80 |

它证明现有日志可以恢复提交前后的部分链路；它不证明“完成等待就是URMA相对verbs的瓶颈”。没有对应verbs时间线，CQE时间包含软件观察延迟，多个请求并行且互有依赖，各段中位数不能相加成Dispatch时延。

collect发生于CPU已经读到GPU命令之后，不是GPU发布时刻。现存日志没有测出GPU发布到CPU发现的等待；cqe也不是远端GPU已看到数据/通知的时刻。当前GPU范围v2明确publication_correlation=false，不能声称该版本已经解决这个关联缺口。

## 4. SDK提交CPU时间未形成一致劣势证据

202节点，单位为跨Proxy线程累计μs/API，存在嵌套和并行：

| 数据集 | FP8 sdk_post | BF16 sdk_post |
| --- | ---: | ---: |
| verbs_normal_campaign_01 CPU诊断，5次窗口 | 165.74 | 169.84 |
| URMA phase_v1 r01，50次窗口 | 80.63 | 82.46 |
| URMA phase_v1 r02，50次窗口 | 81.30 | 83.29 |
| URMA phase_v1 r03，50次窗口 | 180.99 | 185.40 |
| URMA notify_05 timeline，5次窗口 | 349.62 | 287.91 |

样本配置、构建和诊断方式不同，这不是严格阶段A/B；尤其timeline事件记录可能计入CPU scope。其用途是约束结论：不能挑一个高值就宣称SDK提交CPU耗时是稳定主因。CQ轮询累计时间也不能直接当作网络服务时间或关键路径占比。

## 5. 下一次实验前必须补的最小证据

不新增host回放和全量扫描。先在代码侧明确验收条件：

1. 必须实际得到两后端FP8/BF16 Dispatch窗口；只得到layout就拒绝标为阶段对照完成。
2. 同一测量调用的请求标识需唯一，提交、完成与退休记录需闭合；报告未闭合请求，不强制drain来改变生产执行语义。
3. 若比较GPU发布到CPU发现，必须提供对应发布边界和可验证的时钟关联；否则明确该段未测量。
4. 若比较网络后GPU等待，必须保留GPU观察/等待证据；本地CQE不能替代远端GPU可见性。
5. 记录测量扰动及实际运行库，未插桩整体时延用于验证代表性。v2 GPU打桩未硬件验证，不能直接要求再次全量运行。

在这些条件没有落实前，现有资料不足以断定585/1056μs具体落在哪一段；不能承诺当前发行版内核可完成URMA GPU现场对照。当前可交付的是上述粗定位和明确缺口，而非一个已证实的根因。
