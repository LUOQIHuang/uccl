# SPLIT 测试错误记录 (urma_normal_split)

**测试**: URMA Normal 单向WRITE与GPU观察成本分解 (SPLIT.zh-CN.md)
**模块**: urma_normal_split_v2 (micro_version=2)
**节点**: 202 (141.61.17.202, RANK=0) + 204 (141.61.17.204, RANK=1)
**代码版本**: commit b8f63fdb
**时间**: 2026-11-06 18:23 ~ 18:56

## 编译与检查 (第3节) — 通过

双端 BUILD_PASSED + MODE_PARITY_CHECK_PASSED，micro_version=2，无问题。

| 节点 | module_sha | 结果 |
|------|-----------|------|
| 202 (node0) | 0df3fa7ba6ce516b566a607c3b50001900cc72f062cbbdf2b88e3867b77b66c1 | BUILD_PASSED + CHECK_PASSED |
| 204 (node1) | e1fe820432f3bceb8c6081ffa9b6c81d7f7edcefcdc37d830b6ad29e7d923ea1 | BUILD_PASSED + CHECK_PASSED |

## 环境事件

### 202 服务器重启
- **时间**: 2026-11-06 ~18:10 (编译完成后、套件运行前)
- **现象**: 202 从本机和 204 均不可达 (100% ping 丢包, "No route to host")
- **恢复**: ~18:22 自动恢复，uptime 确认为 1 分钟 (刚重启)
- **影响**: PCIe atomic 重置为 0020 (需 0060)，已修复

### 202 重启后环境修复
- PCIe atomic: `setpci -s 0000:a0:08.0 CAP_EXP+28.w=0060` (从 0020 修复)
- runpm_link: 仍为 0 (正常，未受影响)
- libstdc++.so 软链接: 仍存在 (正常)
- MACA_PATH: /opt/maca-3.7.0 (正常)
- GPU runtime_status: suspended → 通过 mx-smi + `echo on > power/control` 唤醒为 active

## 套件运行失败记录

共尝试 5 次 (JOB=01~05)，全部失败。失败模式如下：

### JOB=01 (18:23) — r01_t1 通过，r01_t4 失败

- **r01_t1** (1线程): rc=0, 所有 checks 通过 ✅
- **r01_t4** (4线程): rc=1, 所有 checks=False ❌

**错误 (r01_t4, 双端相同)**:
```
File "split_worker.py", line 58, in <module>
    sample=json.loads(session.step(sequence,point['fadd'],point['messages']))
RuntimeError: micro completion error
```

bench.log 显示 4 个线程均注册成功 (URMA_REG stage=success)，但在 `session.step()` 测量步骤中失败。

### JOB=02 (18:31) — r01_t1 失败

**错误 (r01_t1, 202侧)**:
```
File "split_worker.py", line 58, in <module>
    sample=json.loads(session.step(sequence,...))
RuntimeError: micro completion error
```

204侧: `RuntimeError: micro peer disconnected` (202 崩溃断开)

### JOB=03 (18:38, GPU唤醒后) — r01_t1 失败

**错误 (r01_t1, 202侧)**:
```
File "split_worker.py", line 42, in <module>
    session.connect([bytes.fromhex(b) for b in remote])
RuntimeError: urma_bind_jetty failed: rc=4096 (Unknown error 4096)
```

204侧: `RuntimeError: micro peer disconnected`

preflight.json 记录 `runtime_status: "suspended"` (GPU 挂起状态)。

### JOB=04 (18:45, 模块重载后) — r01_t1 失败

**操作**: 重载 metax/udma 模块 (rmmod + modprobe runpm_link=0)

**错误 (r01_t1, 202侧)** — 与 JOB=03 相同:
```
RuntimeError: urma_bind_jetty failed: rc=4096 (Unknown error 4096)
```

dmesg 记录:
```
ubase 0000a: UDMA: udma alloc jetty id buffer failed, ret = -22.
ubase 0000a: UDMA: active jetty detail failed, ret = -22.
URMA|ubcore|...|ubcore_create_jetty[2618]|[DRV]failed to create jetty.
```

### JOB=05 (18:53, udma模块重载后) — r01_t1 失败

**操作**: 完整重载 udma + metax + udma_metax_p2p_bridge (rmmod udma → modprobe udma + metax + bridge)

**错误 (r01_t1, 202侧)** — 错误变化:
```
File "split_worker.py", line 58, in <module>
    sample=json.loads(session.step(sequence,...))
RuntimeError: micro completion error
```

urma_bind_jetty 错误消失 (绑定成功)，但 step() 仍失败。

dmesg 记录:
```
URMA|ubcore|...|ubcore_bind_jetty_reuse_compat[2268]|Failed to exchange tp info, ret: -1.
URMA|uburma|...|uburma_cmd_bind_jetty_ex:[3756]|bind jetty failed, ret: -1.
```

## 错误分类

| 错误类型 | 出现次数 | 可能原因 |
|---------|---------|---------|
| `micro completion error` (session.step) | 3次 (JOB=01 r01_t4, JOB=02, JOB=05) | URMA WRITE 完成轮询超时，数据路径问题 |
| `urma_bind_jetty failed: rc=4096` | 2次 (JOB=03, JOB=04) | udma jetty ID 分配失败，重启后驱动状态脏 |
| `Failed to exchange tp info` | 1次 (JOB=05 dmesg) | URMA 传输路径交换失败，链路配置问题 |

## dmesg 关键错误汇总

```
# jetty ID 分配失败 (JOB=04)
[ 1710.215987] ubase 0000a: UDMA: udma alloc jetty id buffer failed, ret = -22.
[ 1710.224107] ubase 0000a: UDMA: active jetty detail failed, ret = -22.
[ 1710.231608] URMA|ubcore|180997|...|ubcore_create_jetty[2618]|[DRV]failed to create jetty.

# TP 信息交换失败 (JOB=05)
[ 2122.777027] URMA|ubcore|189117|...|ubcore_bind_jetty_reuse_compat[2268]|Failed to exchange tp info, ret: -1.
[ 2122.788269] URMA|uburma|189117|...|uburma_cmd_bind_jetty_ex:[3756]|bind jetty failed, ret: -1.

# 重复 jetty ID 创建 (多次出现)
ipourma2/4/5: create redundant jetty id: N by eid: 0!
```

## 尝试过的修复

| 修复操作 | 结果 |
|---------|------|
| 修复 PCIe atomic (0020→0060) | 环境正确但测试仍失败 |
| 唤醒 GPU (suspended→active) | 环境正确但测试仍失败 |
| 重载 metax + udma_metax_p2p_bridge | urma_bind_jetty 仍失败 |
| 完整重载 udma + metax + bridge | urma_bind_jetty 解决，但 step() 仍失败 |
| 使用新 JOB 名 (避免覆盖) | 每次重试均使用新 JOB，结果一致 |

## 当前环境状态

- URMA 链路: port 4=ACTIVE, port 1=DOWN (202 和 204 一致)
- 网络: 双向 ping 正常 (0% 丢包)
- 设备: 双端 uburma + mxcd 空闲
- GPU: 双端 active
- 模块: 双端 udma + metax + p2p_bridge 已重载，runpm_link=0

## 结论

202 服务器重启后 URMA 子系统未完全恢复。虽然编译检查通过、网络连通、设备空闲、模块可重载，但 URMA 数据路径在运行时持续失败 (`micro completion error`)。jetty 绑定和 TP 信息交换问题在模块重载后部分缓解但未根治。可能需要更深入的 URMA 驱动重置或联系系统管理员处理。
