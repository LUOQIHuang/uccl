# v2_03 独立复核：功能通过，性能收益尚未成立

数据来自本目录的两端 production probe、Normal 和 LL 原始日志，读取结果提交为 `82310135`。历史参照为 `test/bench_results/urma_diag_home_01` 的 `r1_unpinned`，优先比较完整调用 CUDA-event 耗时。上传成功报告的功能结论成立，但应补充性能和内核事件方面的限制。

## 1. 对齐修复在真实 benchmark 中有效

两端 Normal 原始分配地址仍是 `0xa24a03600`，对齐后实际使用 `0xa24a04000`；LL 为 `0xa77e03600 -> 0xa77e04000`。均保留 `finegrained=1`，backing=90112B，逻辑注册长度=81960B，GPU atomic=1 注册成功，其余 transport 命中缓存。

这次并非仅“恰好分配到了对齐地址”：真实 benchmark 的原始地址仍未对齐，包装确实产生了新的内部对齐地址，随后完成正确性和退出检查。结合 v2_02 的失败地址，对齐修复已解决本组测试中的注册故障。此结论不覆盖所有输入和长期稳定性。

## 2. 两种模式的功能验收通过

- 两个 production probe 均 rc=0。
- 两端 Normal 均 rc=0，24 组正确性、4 个 profile/调优点、模式、设备和退出确认全部通过。
- 两端 LL 均 rc=0，原正确性矩阵、2 个 profile、带宽/hook 输出、模式、设备和退出确认全部通过。
- Normal 实际初始化为 channels=8 / endpoints=9；LL 为 channels=1 / endpoints=1，均 batch=linked、wire=2。
- 两端 Normal fingerprint 一致；每台 Normal/LL 使用相同构建清单和模块。构建选项包含 PER_EXPERT_BATCHING=0、MAX_NUM_GPUS=1、NUM_MAX_NVL_PEERS=1。
- 构建清单中的生产源码哈希与当前分支一致（比较时已归一化 Windows CRLF）。

日志支持预期 URMA 后端和模式被选中，但没有逐 WR 的运行计数，不能据此定量分解 WRITE、FADD、quiet/barrier 的执行成本。

## 3. Normal 性能：Dispatch 比历史旧 URMA 慢

输入：tokens=4096、hidden=7168、experts=256、topk=8、SM=24、NVL chunk=4。下表为完整调用 CUDA-event 平均值，单位 ms；最后一列以 204 为例。

| 操作 | RDMA chunk | 新版 202 | 新版 204 | 旧 URMA 204 unpinned | 新版变化 |
| --- | ---: | ---: | ---: | ---: | ---: |
| FP8 Dispatch | 32 | 4.7314 | 4.7415 | 4.1582 | +14.03% |
| BF16 Dispatch | 32 | 8.5467 | 8.5632 | 7.4452 | +15.02% |
| Combine | 12 | 8.5476 | 8.5148 | 7.9127 | +7.61% |
| Combine | 16 | 8.0958 | 8.1489 | 8.3741 | -2.69% |

202 的对应变化为 +13.87%、+14.73%、+8.00%、-3.36%，方向一致。因此目前不能宣称批处理/多通道改造带来了总体性能改善。

Combine 的优劣次序改变：旧版 chunk=12 优于 16，新版在这两个测点中 16 更好。但新版 204 chunk=16 的 15 次调用 min/max 为 7.3137/8.5151ms，跨度约平均值的 14.7%，2.69% 的历史差异不足以宣布稳定改善。两端结果相关，也不等价于两次独立实验。

历史参照使用同名 URMA 内核、相同私有库 SHA、相同输入和相同完整调用测量方法，具有参考价值；但 boot ID、模块和测试序列不同，且各只有一次 case。旧版测 22 个调优点，新版仅测 4 个点。因此这些是描述性差异，尚不能将约 14%–15% 的 Dispatch 变化因果归于某一项改动。

当前目录没有新版 verbs 结果，不能给出本轮 URMA/verbs 的可靠性能差距。

## 4. 不应只看单个 transmit kernel

BF16 Dispatch profiler 中，202 的 dispatch/notify 为 8.262/0.2169ms，204 为 5.979/2.426ms；而完整调用分别约 8.5467/8.5632ms。单个阶段耗时相差较大，完整调用却很接近，提示阶段划分中的同步等待位置会影响观测。

profiler 与完整调用来自不同测量循环，不能把它们当作同一次调用做精确相减。后续主要用完整调用比较快慢，profiler 用于寻找等待位置；当前没有证据指定 Proxy CPU 是主要瓶颈。

## 5. LL 功能通过，但无 hook 完整调用仍为毫秒级

默认 tokens=128、hidden=7168、experts=256、topk=8：

| 指标 | 202 | 204 |
| --- | ---: | ---: |
| 不带 hook 的 Dispatch+Combine 完整调用 | 21.3859ms | 21.3905ms |
| 不带 hook 的 Dispatch kernel | 6.699ms | 6.725ms |
| 不带 hook 的 Combine kernel | 14.590ms | 14.612ms |

benchmark 报告合计逻辑带宽约 1.03GB/s。Combine 在两项 kernel 耗时之和中约占 68.5%，可作为 LL 后续定位重点；这不是 ATOMIC 或网络传输占比。

带 hook 的 202 输出 Dispatch 280.07+34.68us、Combine 275.11+35.79us；204 为 216.54+35.10us、218.13+35.58us。这是另一种执行方式下的分段 kernel 耗时，不能替代上表完整调用：原 benchmark 在 hook 前会分配两个 8192×8192 矩阵并执行 GEMM，改变了工作负载和进度窗口，输出也没有给出该路径的完整调用时间。不能根据这些数值宣称 LL 端到端只需几十或几百微秒，或带 hook 获得相同倍数的加速。

## 6. 端口计数器无增长，但 LL 有新增 TP AE

四个双机 case 的 retry_req_sum、retry_ack_sum、crc_error_sum、num_phy_reinit、num_retry、phy_reinit_cnt、pfa_dfx_tx_norm_err_pkt_in_cnt、pfa_dfx_rx_err_pkt_in_cnt 前后增量均为 0。部分累计值在开跑前已经非零，不能算本次错误。

Normal 未发现快照中新增的 error/fail/timeout 相关行。LL 两端在 GPU 段 unmap 附近均出现：

```text
udma_ae_tp_level_error: 62 callbacks suppressed
trigger TP level AE, event type is 16, sub type is 0
```

各快照保留 8 条可见 TP 事件行。时间和顺序提示它可能与清理/连接销毁有关，但现有日志和缺少事件码定义不足以证明无害，也不能据此认定为测量期间的性能原因。当前 status 的 no_errors 检查针对用户态 bench.log，并不覆盖内核日志；功能 rc=0 与存在内核事件可以同时成立。

## 7. 下一步优先顺序

1. 在当前同一次开机环境、同一 v2_03 模块下测 verbs，两端使用新的结果 job。不要直接把早期不同内核的 verbs 数据当作本轮基线。
2. 至少补 3 次独立 case，使用不同 job 名称，观察 Normal Dispatch 的差异和 Combine=16 的波动是否重复。
3. 若重复确认新版 URMA 比同环境旧路径慢，分别隔离 Normal 通道数、提交批量和 CQ 回收策略；每次只改一项，维持相同分配、源码基线和输入。当前实现中多个优化同时变化，不能凭本轮数值选定单一根因。
4. LL 单独对照两后端的无 hook 完整调用，优先解释 Combine 的约 14.6ms，并核对 TP AE 出现阶段；不要用带 GEMM 的 hook 分段数据替代基线。

同环境 verbs 运行命令（202 RANK=0，204 RANK=1，沿用已检查的环境变量；两端 180 秒内启动）：

```bash
BUILD_DIR=/home/h00918771/metax_dev/urma_mode_parity_build/urma_mode_v2_03
bash test/urma_mode_parity/run.sh --action run \
  --node-rank "$RANK" --master-addr 141.61.17.202 \
  --job verbs_mode_v2_03 --build-dir "$BUILD_DIR" \
  --backend verbs --mode both
```

若当前环境不支持 verbs，保留原错误和环境信息。源码未改时不需要重新编译。后续提供两端完整结果目录（尤其 metrics.jsonl、bench.log、status.json、setup.json、host.json、kernel 快照），不只截取带宽行。
