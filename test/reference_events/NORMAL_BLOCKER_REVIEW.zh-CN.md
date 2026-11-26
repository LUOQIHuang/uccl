# Normal BF16 细拆：204哪些WRITE占住发送队列

下一轮实机实验已准备好。使用[实际阻塞WRITE采集步骤](../normal_completion/BLOCKERS.zh-CN.md)，在同一调用里合并FIFO可读性、队首WR和CQ状态。设备内部时间戳是再下一层的证据。

先复算已经上传的FIFO和CQ可读性记录。本次新增两份离线拆解，实机新增用时为零。

## 204第6块实际在等前面的哪条命令

在202c1/204c0、204 tail-off的旧全阶段记录中，逐次重放`collect → ack_slot`，在软件窗口满时按实际队首WR ID确定阻塞者，再查其通道内WRITE序号。以下比例以`窗口满且队首为已提交、尚未观测到完成的payload WRITE`这一状态的累计时长为分母。每个位置为9次调用×12条通道。各通道的区间会重叠。[请求序号复算表](results/urma_device_pair_01/urma_c1_c0_01_fifo_candidates/payload_blocker_ordinals.csv)

| 204等待位置 | GPU尾通知发布后到Proxy读取WRITE，中位数 | 期间上述状态的累计占比 | 实际占住队首的WRITE序号 |
| --- | ---: | ---: | --- |
| 第6块 | 943.82 μs | 99.59% | 第1条33.67%，第2条66.33% |
| 末块 | 3427.50 μs | 99.58% | 第2～7条，贡献较大的是第3条22.86%、第5条22.33%、第4条20.05% |

这份旧采集的性能门槛未通过，仅用于选择后续探针对象。数值说明当时实际占住了哪个软件名额，不能直接折算为正常Dispatch或URMA/RDMA差距占比。第6条WRITE占末块这类等待的16.22%，它也发生在第6块已被读取之后。围绕第6条WRITE的探针覆盖不了阻止第6块被读取的前序WRITE。

## 第6条WRITE/FADD已能细到返回值和CQE之间

在较低扰动的`normal_completion_01`原始记录里，每端9个调用×4个Proxy，选ring0第6条WRITE及对应通知FADD。将同一FADD的本端旧值采样与实际消费的CQ位置关联。`R`是本端返回槽变得可读，`Q`是对应CQE变得可读。各请求使用最后无效、首次有效或SDK实际交付约束时间；下表为逐请求上下界分别取中位数。[逐请求数据](../normal_completion/results/normal_completion_01/raw_review/fadd_readability_bounds.csv) · [汇总](../normal_completion/results/normal_completion_01/raw_review/fadd_readability_summary.csv)

| 小阶段 | 202 | 204 |
| --- | ---: | ---: |
| FADD提交结束→R | 2067.50～2071.64 μs | 2320.40～2324.61 μs |
| R→Q | 10.47～18.78 μs | **481.52～490.14 μs** |
| Q→SDK返回，对应上界的中位数 | ≤4.63 μs | ≤4.44 μs |

204有36/36对在返回值已经读到后，仍观测到WRITE和FADD两个CQE都无效。两个CQE最终由同一次SDK poll取回，所以W→A=0仍可能伴随约0.48 ms的完成可读前等待。202对应FADD有21/36条具备正的R→Q下界，分布存在长尾。FADD提交→R仍包含设备排队/依赖、远端执行和响应返回。现有记录不能判断WRITE和FADD的完成是否因为同一个设备条件而推迟，也没有证据将该约0.48 ms归因于设备内部的某个具体模块。两轮记录未逐请求连接。原始数值前后控制通过，完整来源仍缺42条元数据路径，补交已有文件即可。[来源检查](../normal_completion/results/normal_completion_01/raw_review/context.json)

## 下一次实验应带来什么新增证据

优先204发送侧实际挡住名额的WRITE。第6块取第1～2条作为对象，末块优先取第3～5条。至少需要同一WR的**设备开始执行WRITE**或**设备确认WRITE完成/收到ACK**之一，带jetty和WQE序号等可归属到该请求的标识，以及可与本端CQE对齐的时间。两者同时具备时，才能拆出提交后设备等候、开始执行至设备确认、设备确认至CQE发布三个区间。只有SQ consumer index时只报告设备消费到该位置，不能称已经开始实际传输。只有SDK提交和完成时钟时，重复测量无法把这几个区间分开。

完整阶段对照暂停。先用当前私有UDMA对应的设备trace能力或厂商调试接口确认上述事件。已归档的`urma_cr_t`结构没有设备时间戳，旧`available_events`读取因权限失败，事件支持情况仍待确认。证据可来自对应provider/驱动源码或厂商事件清单及字段定义。仅有聚合次数或不可归属到请求的事件时，不为拆阶段安排benchmark。若获得可用事件，再做目标WRITE的稀疏采集、保留无采集控制，并验证它确实占住FIFO且释放后后续命令推进。候选要解释多少URMA/RDMA差距，仍须verbs同口径记录或能同时改善无探针Dispatch的单变量干预。[已有能力记录](../fadd_path/results/urma_fadd_tail_scope_01/urma/capabilities_node1.json)

离线复算可直接复用旧输入，输出目录须为新目录。

```bash
python3 tools/reference_events/review_completion_raw.py \
  --root test/normal_completion/results/normal_completion_01 \
  --output /tmp/normal_completion_fadd_detail
python3 tools/reference_events/review_fifo_candidates.py \
  --trace test/reference_events/results/urma_device_pair_01/urma_c1_c0_01_trace \
  --output /tmp/normal_fifo_blocker_ordinals
```
