# Normal：从实现与硬件差异审查剩余回退

审查基于生产源码提交d0b5475b、历史verbs匹配采样和当前202c1/204c0的URMA记录。不占用GPU机时，不改变正在运行的8→32实验。本页区分源码事实、记录事实与待验证推测，不把所有差异都称为瓶颈。

最重要的修正：**数据WRITE/FADD请求数一致，不代表整个命令通路、完成等待条件和硬件访问路径一致。GPU端QUIET发布本身也受编译宏影响。**

## 1. 先排除几种容易误判的解释

离线重新统计两端各9个有效BF16 Dispatch窗口，均为4096 tokens/rank、chunk32。历史verbs与当前URMA不是新的同口径时延对照，这里只比较实际发出的数据操作种类和数量。

| 每端、每次调用 | verbs | 当前URMA |
| --- | ---: | ---: |
| 数据WRITE逻辑请求 | 144 | 144 |
| 数据FADD逻辑请求 | 252 | 252 |
| 上述请求的物理提交数 | 144 / 252 | 144 / 252 |
| collect后回收、无该WR数据post的本地控制命令 | 6 | 34 |

36个窗口逐个一致。排除UINT64_MAX内部transport请求后统计；verbs opcode为WRITE=0/FETCH_ADD=6，私有URMA为WRITE=0/FADD=34。当前负载实际使用原生verbs FADD，没有走WRITE_WITH_IMM的原子模拟路径。源码中确有模拟分支，但不能把未执行分支当作此轮差异。

当前URMA每窗202记录252个fence=1；204记录132个fence=0、120个fence=1。它与204仅tail-off的配置相符，**tail-off并没有关闭202全部及204 head反馈的fence**。verbs trace没有atomic_fence事件，不能用其零记录数当作测得的flag值；其实际Normal原生FADD构造代码只置IBV_SEND_SIGNALED。

缓冲区也已核对：MetaX分支两边远端计数器均使用mcDeviceMallocFinegrained GPU显存；verbs日志将81960字节control buffer以DMA-BUF注册，URMA日志以kind=gpu、atomic=1注册。本端FADD old-value返回缓冲则两边都在主机内存。不是“一边原子操作访问主机、一边访问GPU”的简单对照。

源码入口：`ep/src/uccl_proxy.cpp:18/117/122`；`ep/src/rdma.cpp:3507/3638/3679`；`ep/src/urma_transport.cpp:461/684`。额外URMA host barrier words不等于Normal token计数器。

## 2. 顺序和完成推进：已有最强因果证据的差异

| 环节 | verbs实际分支 | URMA实际分支 | 如何可能影响Normal |
| --- | --- | --- | --- |
| WRITE | IBV_WR_RDMA_WRITE | URMA_OPC_WRITE、Normal RELAX placement | 两套设备/API的完成与顺序语义不能按名称直接等同 |
| FADD | IBV_WR_ATOMIC_FETCH_AND_ADD，显式flag为SIGNALED | URMA_OPC_FADD、STRONG placement，按角色保留或关闭fence | 顺序条件可能延长通知/反馈完成推进，继而延长占额 |
| 完成读取 | ibv_poll_cq，一次可请求kMaxOutstandingSends条 | 每次SDK最多请求16条；满批继续drain，之后token/generation→逻辑WR | provider、CQ发布和轮询交付路径不同 |
| pending释放 | 共用按队头完成顺序回收 | 相同 | 同样8个名额，在完成推进不同的情况下可能形成不同背压 |

准确地说，RDMA RC也有自身的操作顺序规则；没有IBV_SEND_FENCE不等于毫无依赖。URMA的STRONG placement、fence和completion ordering是不同设置，不能把它们统称一个“强排序开关”，也不能按flag数量推算额外耗时。

已有全204数据fence关闭的严格ABBA：BF16完整调用8.08357→7.80605ms，约省277.52μs、3.433%。这给出顺序约束具有可消除代价的实际证据。它不是“只关tail”的同一组，也不是最新配置剩余差额的分段占比。

历史匹配204大WRITE提交→CQE观测P95为2.290/3.450ms；单对请求URMA又可以比verbs更快。这种组合更符合持续混合负载下的排队/顺序/完成推进差异，而非“URMA每次基本操作都更慢”。当前FIFO重放又显示204主要由未见CQE的大WRITE占住名额。

此前tail-off下FADD返回值先于SDK完成观测可读，二者仍差约351～356μs，说明仅把全部等待解释为远端原子尚未执行也不充分。但未观测设备真实CQE就绪时刻，不能断言这段全部由provider或CPU造成。CQE少20.6%而完整调用仅改善约0.38%的阴性结果，也限制了“单纯减少CQE数量”方向。

实现引用：`ep/src/rdma.cpp:2416/3638`；`ep/src/urma_transport.cpp:661/683/875`；`ep/src/proxy.cpp:925/1285`。Normal两个后端都按ring选数据QP/lane，不能称URMA全挤在一个发送队列。

## 3. 新确认的重要差异：QUIET数量和完成条件

`ep/include/uccl_ibgda.cuh:326`：

- USE_URMA构建：kQuietStride=1，每个Proxy的每个ring都发布QUIET。4个Proxy×8个ring，一次quiet共32条本地命令。
- 当前verbs构建USE_URMA=0：kQuietStride=kChannelPerProxy，每个Proxy只发一条，共4条。
- 同一段等待代码随后依次等待各条QUIET被消费。这里是GPU↔CPU控制交互，不是32条网络QUIET包。

实际记录中每端每窗本地无数据post控制命令为URMA34、verbs6，与32/4个QUIET加控制屏障的差异相符。原始事件未逐条记录cmd_type，因此统计表仅将它们标作本地控制，不伪造逐条QUIET/BARRIER标签。

CPU侧也不同：

- URMA在处理QUIET/BARRIER前调用`ensure_urma_capacity(limit)`，要求该Proxy的真实urma_outstanding_ops_降到0。等待会在CPU当前控制处理过程中轮询所有相关transport。
- verbs `quiet_cq()`中`outstanding_batches`是固定返回0的lambda，连续三次空CQ即可返回；这个函数自身未使用真实在途计数证明全完成。是否由上层其他边界保证安全，还需分析完整协议，不能只凭这一处宣布RDMA错误。

这是实质差异，可能增加控制开销或改变前后调用间完成等待的位置；现有数据尚未单独测出其对正常运行完整API的净贡献。不能仅凭32/4就声称QUIET耗时8倍，或已经解释多数回退。

**不能直接删URMA的全ring QUIET或真实完成等待来“对齐”。** sibling ring里可能还有已发布但未被Proxy读取的命令；buffer复用前必须保证原本要求的完成条件。若后续证明确有成本，应研究减少重复排空/交互而保留所有先前命令的完成保证。

引用：`ep/include/uccl_ibgda.cuh:326/337/381`；`ep/src/proxy.cpp:1473/1748/1787`；`ep/src/internode.cu:169/264/1930`。这部分包含prepare/notify等阶段，不能只靠主Dispatch kernel内部事件解释所有开销。

## 4. 硬件路径：不只是把verbs API换个名字

| 项目 | 归档verbs路径 | 当前URMA路径 |
| --- | --- | --- |
| 网络设备 | rocep40s0f0，PCIe HCA | 202 udmac1d1e2、204 udmac0d1e2，UB设备 |
| 传输对象 | RC QP、CQ | RC jetty、JFC |
| GPU数据注册 | DMA-BUF MR，rkey/IOVA | is_gpu_seg=1的segment，导入NOMAP、non-cacheable，TID/MVA/VA路径 |
| 驱动依赖 | 归档stock 6.6内核 | 6.6.0-gdr_f00955680_diag，私有UMDK及GPU pin/映射支持 |
| GPU型号/版本 | C500、MACA3.9.0.29、GPU驱动3.10.14 | 本组记录相同 |
| GPU BDF | 历史记录a1:00.0 | 当前记录21:00.0 |

URMA源码调用urma_register_seg，GPU字段is_gpu_seg=1；远端segment导入采用URMA_SEG_NOMAP、URMA_NON_CACHEABLE，提交地址优先使用mva。verbs走ibv_reg_dmabuf_mr及独立control MR。这些是地址转换、GPU peer访问和设备推进栈的真实差异；**注册发生在初始化，不是每个token重新注册**。可能的性能差异在已注册映射上的运行时访问，而非反复注册开销。

归档verbs ibv_devinfo记录active_mtu=1024、Ethernet，报告记录100Gb/s；不能把这些数值套到UB设备。UB实际协商速率/分包粒度及两套设备的内部原子资源、完成调度目前未核实。也不能仅凭URMA需要开启上游PCIe Atomic Request，就断言verbs原子完全不经过GPU/PCIe或不会受同一链路限制。

BDF改变不足以证明GPU物理迁移，但提示不能直接套用旧boot拓扑。技术报告曾记录204 GPU Gen5 x4、202 Gen5 x16；当前记录没有同口径链路宽度快照，不能当作本轮已验证配置。UDMA c0/c1设备选择曾影响通信正确性，也不证明它是历史性能回退根因。

NUMA同样保留边界：源码URMA按GPU locality选择绑核位置，verbs按NIC locality；URMA还在该绑核调用之前初始化JFC/host scratch。但**当前两套诊断driver均设置UCCL_EP_DISABLE_CPU_AFFINITY=1、adaptive sleep=0**，不能据此写成当前“verbs绑核而URMA没绑核”。实际CPU/内存页归属没有足够记录确认。

硬件记录：`results/verbs_ref_4096_paced_01/node0/ibv_devinfo.txt`、两端host.json；`results/urma_device_pair_01/urma_c1_c0_01_batch_pre/node*/normal/urma_preflight.json`及gpu_before.txt。源码：`ep/src/urma_transport.cpp:407/449/551/644`、`ep/src/rdma.cpp:178/1428/1517`。

## 5. 需要纠正“WRITE本身一定更快”的概括

本地原技术报告`文档/uccl-ep-xpu.md`的同条件、双向GPU WRITE短时微测（细粒度显存、一个连接、depth32）记录：

| 消息大小 | verbs 202 / 204 GB/s | URMA 202 / 204 GB/s |
| --- | --- | --- |
| 64KiB | 9.535 / 9.007 | 8.927 / 8.928 |
| 256KiB | 9.986 / 9.674 | 9.042 / 9.043 |
| 1MiB | 10.011 / 9.821 | 9.334 / 9.054 |

因此，单对延迟或4MiB单向端口速率优势，不能推出Normal常见数百KiB、多队列、双向、混合atomic形态的WRITE也占优。256KiB报告值对应URMA载荷吞吐低约9.45%/6.52%，量级值得保留为独立硬件/内存路径候选；**不是已经证明它解释Normal同等比例差距**。

这些是早期报告值，不是本次重测；归档目录gpu_peak_scan_7084e9c2/gpu_peak_sustain_672eafe5目前包含微测源文件，但没有提供上述每轮原始结果日志。不同MACA/内核/boot及计时条件也限制了外推，不拿它们重新计算最新URMA/RDMA净差额。

## 6. 候选优先级和当前实验的关系

| 候选 | 现有支持 | 还缺什么 |
| --- | --- | --- |
| 持续混合负载下的顺序/完成推进差异，经过共享pending机制放大 | 有fence整体因果收益、WRITE长尾、当前实际队头重放 | 最新配置剩余差距中贡献多少；设备、传输和SDK交付内部划分 |
| QUIET控制图与完成条件不等价 | 明确源码差异，实际本地控制数6/34 | 正常运行中该差异是否占关键路径及完整API净成本 |
| 双向中等消息GPU P2P/映射/设备吞吐差异 | 设备和注册栈确实不同，早期报告有同条件WRITE吞吐劣势 | 当前环境下匹配负载与原始对照，不能用大包单向峰值替代 |
| 提交组链/lane遍历和本端完成解析开销 | 源码确有差异 | CPU提交独立中位仅数μs；混合组历史P95反而较短，优先级较低 |

当前8→32只检验第一项中的软件准入放大器；它不改变顺序标志、quiet语义、注册方式或设备。阳性表示可用更多并发隐藏等待，不代表修复全部硬件差异；阴性后不继续额度扫描。正在运行的实验保持原样，不中途拉新代码改变四项输入，也不并行启动quiet删减或微测矩阵。

本次没有新增实机任务或生产代码修改。后续应由当前对照结果决定只进入一个分支，不把本表变成必跑清单。

## 7. 操作计数复算

```bash
python3 tools/reference_events/inventory_normal_ops.py \
  --verbs test/reference_events/results/verbs_ref_4096_matched_on_01 \
  --urma test/reference_events/results/urma_device_pair_01/urma_c1_c0_01_trace \
  --output /tmp/normal_backend_operation_inventory
```

输出目录必须不存在。归档计数及原输入SHA见`results/backend_code_audit_20261125/`。这项工具明确timing_comparison=false，不修改原trace验收状态。
