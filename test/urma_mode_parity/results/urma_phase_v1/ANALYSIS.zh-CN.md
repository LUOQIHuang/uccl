# verbs_baseline_01 与 urma_phase_v1 初步分析

核对到远端提交 `aeddb24c`（verbs）和 `d72c41c8`（URMA）。本文只使用已上传原始数据，不以提交标题的通过次数代替文件检查。

## 数据范围

- verbs：只有 node0/master；三轮 baseline × Normal/LL，加一轮独立 GPU profiler × Normal/LL，共8个case，status全部通过。
- URMA：只有 node0/master；r02/r03 baseline 和 r01/r02/r03 instrumented，共5个job、10个模式case，status全部通过。**缺 r01_baseline，以及上述全部 node1 文件**。`overhead.csv` 自身也明确为 baseline_runs=2、instrumented_runs=3。
- 当前未收到可选的 current-source 纯verbs版本结果。因此不能用这批数据单独分离公共代码变化的影响。
- 两端握手、正确性、退出确认通过可说明双机程序运行成功，但 node1 的时延、内核日志和设备状态仍需补齐。

## 关闭插桩的完整调用时延

单位ms。verbs是三轮均值的中位数；URMA是已上传两轮均值的中位数（两数中点）。每轮都预热50次、计时50次，首个样本丢弃，实际49次。配置 Normal tokens=4096，LL tokens=128；hidden=7168、experts=256、topk=8。

| 测点 | verbs | URMA | URMA相对变化 |
| --- | ---: | ---: | ---: |
| Normal FP8 Dispatch chunk32 | 3.703 | 4.103 | +10.8% |
| Normal BF16 Dispatch chunk32 | 6.497 | 7.392 | +13.8% |
| Normal Combine chunk16 | 7.353 | 7.249 | -1.4% |
| Normal Combine chunk12 | 45.956 | 7.206 | -84.3% |
| LL 无hook Dispatch+Combine | 2.009 | 21.151 | 10.53倍 |

这次不能再概括为“URMA的Normal各部分都慢”。稳定的主要差距是Dispatch；Combine16已接近，约1.4%的差异不足以认定实现更优。verbs Combine12本身是明显坏配置，约为自身Combine16的6.25倍，不能用这一点代表verbs最优表现。

verbs三轮均值范围：FP8 3.698–3.712ms，BF16 6.491–6.514ms，Combine16 7.321–7.391ms，LL 2.008–2.023ms。URMA两轮范围：FP8 4.097–4.108ms，BF16 7.386–7.397ms，Combine16 7.247–7.251ms，LL 21.142–21.160ms。轮间波动远小于Dispatch和LL差距，普通随机波动不足以解释这些差距。

两组仍使用各自要求的不同内核和不同构建。比较反映实际两套实现栈的表现；不要求在不支持的内核上强行交叉运行，也不能把百分比全部归因到某一行算子代码。

## GPU阶段

使用各自首次GPU诊断，单位ms；profiler本身是辅助定位，不与未插桩基线混合取平均。

| 测点 | verbs | URMA r01 instrumented |
| --- | ---: | ---: |
| Normal FP8 dispatch kernel | 3.391 | 3.831 |
| Normal FP8 notify | 0.240 | 0.214 |
| Normal BF16 dispatch kernel | 6.189 | 7.123 |
| Normal BF16 notify | 0.228 | 0.217 |
| LL无hook dispatch kernel | 0.704 | 6.651 |
| LL无hook combine kernel | 1.238 | 14.700 |
| LL immediate-hook Dispatch send / receive | 0.208 / 0.473 | 0.297 / 6.306 |
| LL immediate-hook Combine send / receive | 0.201 / 1.085 | 0.290 / 14.764 |

Normal Dispatch 的额外耗时主要落在dispatch kernel范围，notify没有同步增长。LL的最大差距在receive/完成等待，Combine尤其明显。kernel时间包含等待，不能把它称为GPU计算变慢，也不能把重叠阶段加总作时延占比。

## URMA CPU与完成抽样

三次instrumented LL的无hook窗口显示：WRITE“提交前到CQE被软件观察到”的抽样均值约5.45、5.45、5.50ms；FADD约14.23、14.20、14.71ms。此指标包含排队、设备执行、依赖等待及Proxy轮询发现延迟，不是独立FADD硬件服务时间。

以r01为例，每次完整LL调用：约973个WRITE、256个FADD，平均每次SDK提交6.5个WR；4个Proxy累计SDK调用CPU范围约0.067ms，批次处理范围约0.249ms。非空队列收集范围约0.395ms；CQ轮询累计范围约9.23ms，空轮询约占98.8%。这些CPU范围跨线程、嵌套并与GPU/DMA重叠，不能相加成总时延或当作百分比。

Normal FP8/BF16每次SDK调用CPU范围分别约0.081/0.083ms；WRITE完成抽样均值约0.764/1.388ms，FADD约0.676/0.917ms。Normal的多lane分组扫描有额外CPU开销，但当前数据不能把约0.4/0.9ms的完整调用差距直接归因到它。

因此现阶段证据更支持先查**提交后的依赖、完成与可见性路径**，不足以把主因认定为SDK提交调用CPU开销。未计入的CPU调度间隙、远端推进、队列/设备和驱动仍可能影响完成时间；仅凭CQ空轮询也不能断言CPU完全无关。

当前代码Normal WRITE为RELAX，LL WRITE为STRONG，FADD保持STRONG及请求的fence。历史较快私有URMA LL曾使用RELAX。这个差别值得优先做同一新模块的单变量A/B，**尚未证明STRONG就是根因，更不能将本轮性能结果视为可直接关闭fence的依据**。

## 测量扰动和内核事件

- instrumented相对未插桩中位数：FP8 +0.46%、BF16 +0.24%、LL无hook +0.77%，而Combine12 +9.22%、Combine16 +14.65%。Combine尤其不能用插桩数据替代基线。r01 Combine16约7.297ms，r02/r03约8.314/8.311ms，也存在运行状态差异。
- 原URMA phase-suite 即使 `stage_profile=false`，仍先运行GPU profiler再计时对应Normal调用；新verbs基线则完全不启用profiler。下一轮新增 `--api-only`，使URMA正式样本也跳过Kineto，并另开诊断case。
- verbs计数器未见新增错误；URMA共享UB端口中的CRC、明确retry_req/ack及FEC等错误计数未见增加。ACK流量和retry buffer指针变化不是重传错误计数，不能据此声称发生丢包。
- **每个已上传LL case后均新增TP level AE告警**（type16/subtype0），同秒出现GPU unmap；Normal没有对应新增。它可能属于销毁阶段，但现有日志不能证明无害，也不能证明它造成计时内劣化。下一版在最后计时结束、benchmark销毁前新增内核快照，以区分运行期与销毁期。

## 下一步，按服务器资源优先级执行

1. 不占GPU：补传已有node1数据和URMA r01_baseline；不因文件缺失要求重跑。如果当前仍只有RDMA资源，可选纯verbs当前源码对照仍有价值，但不必再次大扫历史verbs参数。
2. 下次协调到URMA环境：同一新模块、同一内核，LL WRITE STRONG/RELAX交替三轮。保持原子STRONG/fence、单lane、批量提交、GPU分配、输入及quiet/barrier不变。正式样本使用API-only，无阶段插桩。
3. 两个策略各加一次独立的API-only阶段诊断，以检查WRITE/FADD完成抽样是否同步改善；不重复GPU trace。资源紧张可先跳过诊断。
4. 同一URMA窗口内，Normal使用API-only关闭插桩再测三轮四个固定点，以公平核对Dispatch差距。Normal不改变WRITE策略，先修正测量方法，不同时引入lane、批量或fence变化。
5. 若RELAX显著改善且正确性通过，继续校验完成/可见性语义及稳定性后再讨论默认策略；若无明显改善，则把GPU目标FADD、WRITE+FADD依赖和远端可见性做独立微测。暂不扩大shape扫描，也不同时修改fence，避免失去因果判断。

运行步骤见 [ORDER_AB.zh-CN.md](../../ORDER_AB.zh-CN.md)。
