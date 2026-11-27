# REVERSE_LOAD 测试错误报告 (第二次复现)

## 测试信息

- **测试名称**: REVERSE_LOAD (同内核减少反方向载荷的 Normal Dispatch 对照)
- **文档**: `test/reference_events/REVERSE_LOAD.zh-CN.md`
- **提交版本**: `03dec158` (test(normal): compare backend gap under reduced reverse payload)
- **测试日期**: 2026-11-28 (第二次复现，两节点重启后)
- **节点**: 141.61.17.202 (RANK=0, master) + 141.61.17.204 (RANK=1, worker1)
- **内核**: `6.6.0-gdr_f00955680_diag`
- **构建**: 复用 `verbs_same_kernel_build/current_v1` + `writes_first_batch_build/urma_v1`

## 执行结果

| 阶段 | 202 (rank0) | 204 (rank1) | 状态 |
| --- | --- | --- | --- |
| check (预检) | SAME_KERNEL_REVERSE_CHECK_PASSED | SAME_KERNEL_REVERSE_CHECK_PASSED | ✅ 通过 |
| run rdma_pre (全量) | VERBS_BASELINE_ALL_PASSED | VERBS_BASELINE_ALL_PASSED | ✅ 通过 |
| run rdma_half (反向减半) | rc=1 error=unexpected timing labels | rc=1 error=unexpected timing labels | ❌ 失败 |
| run rdma_post / urma_* | 未执行 (PAIR_STOP) | 未执行 (PAIR_STOP) | ⛔ |

与第一次运行结果完全一致，错误可稳定复现。

## 路由修改证据 (两节点对比)

### 202 (rank0) — 执行掩码

```
NORMAL_REVERSE_LOAD {"load":"half_reverse","rank":0,"local":4080,
  "remote_before":4076,"remote_after":2046,"eligible":4060,
  "original_sha":"24598f953f0fa97931c38f43f95bffffaba89169632186f60b63ff308c2367bc",
  "active_sha":"58c3ba3ccee459602c1c6f13fb8b2f42cc2fef0a5e8b214c122c2cadd5f4732d"}
```

- remote_before=4076 → remote_after=2046，减少 49.8% ✅
- original_sha ≠ active_sha（路由被修改）

### 204 (rank1) — 不执行掩码

```
NORMAL_REVERSE_LOAD {"load":"half_reverse","rank":1,"local":4086,
  "remote_before":4080,"remote_after":4080,"eligible":4070,
  "original_sha":"d240b87df9fe0f207d1f5190d9478612692a33f416fd3b3413df134214e04e1a",
  "active_sha":"d240b87df9fe0f207d1f5190d9478612692a33f416fd3b3413df134214e04e1a"}
```

- remote_before=4080 = remote_after=4080（无变化）
- original_sha == active_sha（路由未修改）

**两节点对比确认**: `prepare_reverse_load` 仅对 rank 0 做非对称掩码，rank 1 完全不修改路由。

## 错误详情

### 错误位置

`test_internode.py` 第 421 行:

```python
assert (
    calc_diff(check_topk_weights, ref_topk_weights) < 1e-9
), f"{calc_diff(check_topk_weights, ref_topk_weights)}"
```

**错误值**: `AssertionError: 0.06550608343267184` (与第一次运行完全一致)

### 上下文

```python
check_topk_weights = combined_topk_weights / is_token_in_rank.sum(dim=1).unsqueeze(1)
ref_topk_weights = topk_weights  # 本地常量 torch.ones * rank = 0.0 (rank 0)
assert calc_diff(check_topk_weights, ref_topk_weights) < 1e-9
```

- `combined_topk_weights` 来自 Combine 内核，使用 rank 1 发送的未掩码权重
- `ref_topk_weights = topk_weights` = 0.0 (rank 0 常量)
- 0.0655 ≠ 0.0 → 断言失败

### 数据正确性检查通过

`check_x < 5e-6` 在 `topk_weights` 断言之前通过 → Dispatch/Combine 数据输出正确。

## 根因分析

`prepare_reverse_load` (`test/verbs_diag/profile_support.py`) 的 `half_reverse` 路径**仅对 rank 0** 屏蔽远端路由:

```python
if load == 'half_reverse' and rank == 0:
    remove = eligible & (eligible.to(torch.int64).cumsum(dim=0) % 2 == 0)
    topk_idx = topk_idx.masked_fill(remove.unsqueeze(1) & (owners == 1), -1)
```

但 **rank 1 不做任何修改**，仍发送完整的 token 和权重。因此:

1. rank 0 的 `topk_idx` 被修改，`topk_weights` 仍是常量 0.0
2. rank 1 发送未修改的完整权重
3. rank 0 接收 `recv_topk_weights` 来自 rank 1 的完整路由 (非零)
4. Combine 内核用接收的权重计算 `combined_topk_weights` (非零)
5. `combined_topk_weights / is_token_in_rank.sum` ≠ `ref_topk_weights` (0.0)

**核心矛盾**: 参考权重 `ref_topk_weights = topk_weights` 没有考虑非对称掩码对接收权重的影响。

## 建议修复方向

1. **更新参考权重**: `ref_topk_weights` 应考虑非对称掩码对接收权重的影响
2. **对称掩码**: 让 rank 1 也同步屏蔽对应 token (但改变测试语义)
3. **跳过 with_topk 检查**: 在 `half_reverse` 模式下跳过 `topk_weights` 断言
4. **掩码接收权重**: 在 rank 0 接收侧屏蔽对应权重

## 复现步骤

```bash
bash test/reference_events/run_same_kernel_gap.sh --study reverse --action check --node-rank "$RANK"
bash test/reference_events/run_same_kernel_gap.sh --study reverse --action run --node-rank "$RANK"
# rdma_pre 通过，rdma_half 在 test_internode.py:421 处 AssertionError: 0.06550608343267184 失败
```

## 环境快照

- 202 (rank0): HCA=rocep161s0f0, IFACE=enp40s0, DEVICE=udmac1d1e2, uptime ~2h (被重启过)
- 204 (rank1): HCA=rocep40s0f0, IFACE=enp34s0f5u1u4, DEVICE=udmac0d1e2, uptime ~20min (被重启过)
- 两节点 post-reboot 修复: setpci 0060, /dev 666, URMA 设备已恢复
- GPU PCI: 0000:21:00.0, MPS/NIC 桥 0060
