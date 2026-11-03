# URMA Normal 初筛结果与下一步

数据来源：本目录 node202、node204；读取提交 e0b6adbd。每端 1 轮，继承 affinity 与绑核各 1 个 case。固定输入 tokens=4096、hidden=7168、topk=8、experts=256，SM=24、NVL chunk=4。

## 数据验收

- 两端 4 个 case 全部 rc=0，正确性、调优计数、指标、URMA 初始化与设备、私有动态库、线程 affinity、退出确认均通过。
- 每 case 24 组正确性检查、22 个调优点；合计 88 行指标。两端 fingerprint 一致。
- 使用 RELAX WRITE 私有模块 SHA256 `5826d74b98e5f80843ad419e09dc15737a79aa9f3d3854405b553da56db1fe2e`；实际 liburma 来自自己目录下的私有库。host-atomic 开关未启用。
- 内核 `6.6.0-gdr_f00955680_diag`；GPU 原子上游控制值 0060。runpm_link=0。
- 每个 Proxy 启动 group 恰好新建 1 个 TID；绑核 case 的 4 个线程全程快照为 CPU 4/5/6/7。继承 case 的允许集合为 0-383，确实形成对照。

## 当前结果

下表为独立完整调用的 CUDA-event 平均耗时（ms），非仅 transmit kernel。变化为“绑核 / 继承 - 1”，负数代表绑核更快；只有 1 次 case，不能解释为统计显著。

| 节点 | 操作 | RDMA chunk | 继承 affinity | 绑核 | 变化 |
| --- | --- | ---: | ---: | ---: | ---: |
| 202 | FP8 Dispatch | 32 | 4.1550 | 4.1345 | -0.49% |
| 204 | FP8 Dispatch | 32 | 4.1582 | 4.1403 | -0.43% |
| 202 | BF16 Dispatch | 32 | 7.4496 | 7.4115 | -0.51% |
| 204 | BF16 Dispatch | 32 | 7.4452 | 7.4019 | -0.58% |
| 202 | Combine | 16 | 8.3771 | 8.2447 | -1.58% |
| 204 | Combine | 16 | 8.3741 | 8.2433 | -1.56% |

这轮优先级发生两个变化：

1. “URMA 跳过了 native 绑核，因此导致主要差距”不受这组对照支持。固定 chunk=32 的 Dispatch 完整调用变化不到 1%；Combine 的变化约 1–2%。这只约束本次所选 CPU 和继承 mask，不能排除其他 NUMA 放置效果。
2. URMA 没出现上一轮 verbs 的巨大 chunk 周期阶跃。以 204 绑核为例，FP8 Dispatch 的 chunk=4/8/16/32 为 5.6009/4.4362/4.2184/4.1403 ms；BF16 为 8.4385/7.8613/7.6566/7.4019 ms。URMA 的 chunk=4 相对慢，大 chunk 后总体趋缓，不能把 verbs 的 16/32 特征直接套成 URMA 慢的原因。

URMA Combine 随 chunk 增大反而变慢：204 绑核 chunk=12/16/32 为 7.8420/8.2433/8.8637 ms，202 对应 7.8427/8.2447/8.8626 ms。两端一致，提示需要把 Dispatch 与 Combine 的调优拆开看；不能统一认为 chunk 越大越好。这仍未区分 GPU 流水线、网络排队和完成等待的贡献。

UB 端口原始快照中，4 个 case 的 `retry_req_sum`、`retry_ack_sum`、`crc_error_sum`、`num_phy_reinit`、`num_retry`、`phy_reinit_cnt`、`pfa_dfx_tx_norm_err_pkt_in_cnt`、`pfa_dfx_rx_err_pkt_in_cnt` 前后差值均为 0。204 的 CRC 历史累计值为 3，测试前后保持 3；不能当成此次新增错误。目前这些字段不支持“本次明显链路重试/错误增长”的解释。它们是共享端口快照，不覆盖所有拥塞、信用等待或设备内部事件，也不用于换算进程带宽。

上一轮 verbs 在另一个内核/开机环境中测试。即使当前数值仍有差距，也不能据这两组数据把差距完全归因于传输实现；如果当前开机环境支持 verbs，后续用 focused 同环境补对照。

## 下一轮只做必要定位

沿用当前私有模块，不重新编译，新增 `--suite focused --affinity-mode unpinned`：FP8/BF16 Dispatch 固定 chunk=32、Combine 固定 chunk=16。Combine=16 用于对齐已有 verbs 配置，并不宣称是本轮 URMA 最优点。

先做无 perf 对照 `urma_focus_01`，再做 `urma_cpu_01 --profile-cpu`，只对 BF16 Dispatch 的 4 个原生 Proxy TID 采样。CPU-only 预检先确认 perf/调用栈可用；采样收集函数自身 CPU 热点和调用者。运行与回传命令见 [URMA README](../../urma_diag/README.md#第二轮固定配置与-proxy-cpu-采样)。

目标是给下一次代码改动排优先级：WR 构造/提交、CQ 轮询及 WR 回收、读取 GPU 命令/同步等待分别占多少 CPU 样本。忙轮询热点不能直接换算为等待时间或证明根因；定位候选后，还要通过分阶段计时/计数和单变量修改验证因果。当前没有足够证据宣布某一算子或设备带宽就是根因。

本地验证只涵盖 CPU 汇总、源码 AST 变换、硬件/affinity/perf mock 与 Python 语法，没有运行 GPU benchmark 或实际服务器 perf。
