# REVERSE_LOAD 测试错误报告

## 测试信息

- **测试名称**: REVERSE_LOAD (同内核减少反方向载荷的 Normal Dispatch 对照)
- **文档**: `test/reference_events/REVERSE_LOAD.zh-CN.md`
- **提交版本**: `03dec158` (test(normal): compare backend gap under reduced reverse payload)
- **测试日期**: 2026-11-27
- **节点**: 141.61.17.202 (RANK=0, master) + 141.61.17.204 (RANK=1, worker1)
- **内核**: `6.6.0-gdr_f00955680_diag`
- **构建**: 复用 `verbs_same_kernel_build/current_v1` + `writes_first_batch_build/urma_v1` (无需重新编译)

## 执行结果

| 阶段 | 202 (rank0) | 204 (rank1) | 状态 |
| --- | --- | --- | --- |
| check (预检) | SAME_KERNEL_REVERSE_CHECK_PASSED | SAME_KERNEL_REVERSE_CHECK_PASSED | ✅ 通过 |
| run rdma_pre (全量) | VERBS_BASELINE_ALL_PASSED | VERBS_BASELINE_ALL_PASSED | ✅ 通过 |
| run rdma_half (反向减半) | rc=1 error=unexpected timing labels | rc=1 error=unexpected timing labels | ❌ 失败 |
| run rdma_post | 未执行 | 未执行 | ⛔ PAIR_STOP |
| run urma_pre / urma_half / urma_post | 未执行 | 未执行 | ⛔ PAIR_STOP |

## 错误详情

### 1. 路由修改成功

`NORMAL_REVERSE_LOAD=half_reverse` 注入 (`test/verbs_diag/profile_support.py::prepare_reverse_load`) 成功修改了路由：

```
NORMAL_REVERSE_LOAD {"load":"half_reverse","rank":0,"local":4080,
  "remote_before":4076,"remote_after":2046,"eligible":4060,
  "original_sha":"24598f953f0fa97931c38f43f95bffffaba89169632186f60b63ff308c2367bc",
  "active_sha":"58c3ba3ccee459602c1c6f13fb8b2f42cc2fef0a5e8b214c122c2cadd5f4732d"}
```

- remote_before=4076 → remote_after=2046，减少 49.8%（在 0.40–0.60 断言范围内 ✅）
- 仅 rank 0 (202) 对同时有本地和远端目标的 token 隔一个取消远端路由
- rank 1 (204) 不做任何修改

### 2. 正确性断言失败

**错误位置**: `test_internode.py` 第 421 行

```python
assert (
    calc_diff(check_topk_weights, ref_topk_weights) < 1e-9
), f"{calc_diff(check_topk_weights, ref_topk_weights)}"
```

**错误值**: `AssertionError: 0.06550608343267184`

**上下文** (test_internode.py 第 406–422 行):

```python
if with_topk:
    check_topk_weights = (
        combined_topk_weights
        if (current_x is x_pure_rand)
        else (
            combined_topk_weights
            / is_token_in_rank.sum(dim=1).unsqueeze(1)
        )
    )
    ref_topk_weights = (
        topk_weights_pure_rand
        if (current_x is x_pure_rand)
        else topk_weights
    )
    assert (
        calc_diff(check_topk_weights, ref_topk_weights) < 1e-9
    ), f"{calc_diff(check_topk_weights, ref_topk_weights)}"
```

其中:
- `check_topk_weights = combined_topk_weights / is_token_in_rank.sum(dim=1).unsqueeze(1)` — 来自 Combine 内核的输出权重除以每 token 的 rank 归一化
- `ref_topk_weights = topk_weights` — 本地常量 `torch.ones((num_tokens, num_topk)) * rank`（rank 0 上全为 0.0）

### 3. 数据正确性检查通过

在 `topk_weights` 断言之前，数据正确性检查通过了：

```python
check_x = (combined_x.float() - bias_0.float() - bias_1.float()) / is_token_in_rank.sum(dim=1).unsqueeze(1)
ref_x = x_pure_rand if current_x is x_pure_rand else x
assert calc_diff(check_x, ref_x) < 5e-6  # ✅ 此断言通过
```

说明 Dispatch + Combine 的数值输出 (`combined_x`) 是正确的。只有 `topk_weights` 参考值不匹配。

## 根因分析

### 非对称掩码导致参考失效

`prepare_reverse_load` 的 `half_reverse` 路径**仅对 rank 0** 屏蔽远端路由：

```python
if load == 'half_reverse' and rank == 0:
    remove = eligible & (eligible.to(torch.int64).cumsum(dim=0) % 2 == 0)
    topk_idx = topk_idx.masked_fill(remove.unsqueeze(1) & (owners == 1), -1)
```

但 **rank 1 不做任何修改**，仍发送完整的 token 和权重。因此：

1. rank 0 的 `topk_idx` 被修改（部分远端 expert 设为 -1）
2. rank 0 的 `topk_weights` 仍是常量 `torch.ones * 0.0`（未随路由修改而调整）
3. rank 1 发送的是未修改的完整权重
4. rank 0 接收到的 `recv_topk_weights` 来自 rank 1 的完整路由
5. Combine 内核用 `recv_topk_weights` 计算 `combined_topk_weights`
6. `combined_topk_weights / is_token_in_rank.sum` ≠ `topk_weights`（0.0），因为接收的权重非零

**核心矛盾**: `ref_topk_weights = topk_weights`（本地常量 0.0）没有考虑非对称掩码对接收权重的影响。`combined_topk_weights` 反映了 rank 1 发送的实际权重（非零），而参考值是 0.0。

### 为什么 `check_x` 通过但 `check_topk_weights` 失败

- `check_x` 检查的是数据输出 `combined_x`，经过归一化后与参考 `x` 匹配 — Dispatch/Combine 的数据路径正确
- `check_topk_weights` 检查的是权重输出，参考 `topk_weights`（0.0）与实际 `combined_topk_weights / is_token_in_rank.sum`（非零，因为接收了 rank 1 的权重）不匹配

## 建议修复方向

1. **更新参考权重**: `ref_topk_weights` 应考虑非对称掩码对接收权重的影响，而非使用本地常量
2. **对称掩码**: 如果设计允许，让 rank 1 也同步屏蔽对应的 token（但这会改变测试的"减少反向载荷"语义）
3. **跳过 with_topk 检查**: 在 `NORMAL_REVERSE_LOAD=half_reverse` 模式下跳过 `topk_weights` 正确性断言（但文档要求"保持原有正确性检查"）
4. **掩码接收权重**: 在 rank 0 接收侧也屏蔽对应的权重，使 `combined_topk_weights` 与参考一致

## 附注：URMA 设备消失

在首次运行失败后、准备重新运行收集日志时，发现 202 上 URMA 设备 `udmac1d1e2` 从 `/dev/uburma/` 消失（仅剩 `bonding_dev_0`）。URMA 驱动模块仍加载 (`uburma`, `ubcore`, `ipourma`)，但功能设备节点不存在。此为独立的硬件/驱动问题，与 `rdma_half` 正确性断言失败无关。未重启机器（遵守不重启约定）。

## 复现步骤

```bash
# 两端同步到 03dec158
bash test/reference_events/run_same_kernel_gap.sh --study reverse --action check --node-rank "$RANK"
# 两端均看到 SAME_KERNEL_REVERSE_CHECK_PASSED
bash test/reference_events/run_same_kernel_gap.sh --study reverse --action run --node-rank "$RANK"
# rdma_pre 通过，rdma_half 在 test_internode.py:421 处 AssertionError 失败
```

## 环境快照

- 202 (rank0): HCA=rocep161s0f0, IFACE=enp40s0, DEVICE=udmac1d1e2 (已消失)
- 204 (rank1): HCA=rocep40s0f0, IFACE=enp34s0f5u1u4, DEVICE=udmac0d1e2
- GPU PCI: 0000:21:00.0 (两端)
- MPS/NIC 桥: 0060 (两端均已设置)
- /dev 权限: mxcd/dri 均为 666
- 构建: verbs current_v1 (USE_URMA=0) + urma writes_first_batch_build/urma_v1
