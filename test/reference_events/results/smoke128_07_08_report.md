# smoke128_07~08 错误报告

## 测试时间
2026-11-12 04:20 ~ 04:35

## smoke128_07 结果：PAIR_STOP（已定位根因并修复）

### 错误现象
```
RuntimeError: PAIR_STOP local={...,diagnostics_hash='b3c0232b...'} peer={...,diagnostics_hash='790d6d66...'}
```
两端 `diagnostics_hash` 不一致导致 pair 中止。`sources_hash` 一致（代码相同），但 `diagnostic_manifest()` 包含的测试框架文件不同。

### 根因
204 git 仓库停留在旧 commit `aa5accb0`，缺少 3 个新文件：
- `test/reference_events/connection_diag.py`
- `test/reference_events/recover.sh`
- `test/reference_events/test_connection.py`

202 已在 `7ecab45a`。两端不同步导致 `diagnostics_hash` 不匹配。

### 修复
204 执行 `git pull luoqi codex/metax-urma-integration`，现已同步至 `7ecab45a`。
两端验证：
```
diagnostics_hash=b3c0232b16acf89cb980a27221591a499a5fbb0918070870ea8dd49a856b3443  (一致)
sources_hash=75c06eec94f73033c5ff4cca3531e8bd6327bfb689a8d24e20a397172bef9407  (一致)
files=191  (一致)
```

---

## smoke128_08 预检结果：URMA EID 丢失

### 错误现象
修复 hash 一致性后，预检发现两端 URMA EID 均为空：
```
202: rc=0 eid_count=0 NO_EIDS  (uptime 2h12min)
204: rc=0 eid_count=0 NO_EIDS  (uptime 24min)
```

### 环境
- 内核：`6.6.0-gdr_f00955680_diag`（两端一致）
- PCIe AtomicOp：`0060`（两端一致）
- URMA 模块：全部已加载（ubcore/uburma/udma/metax/ubase 等）
- sysfs 设备：`/sys/devices/ub_bus_controller0/00001/00007/uburma/udmac0d1e2` 存在
- GPU：`0000:a1:00.0` 存在（runtime_status=suspended，正常 PM 状态）

### 根因
URMA EID 配置丢失。已知问题：`udma` 模块 rmmod 后 EID 无法通过 `mami.service` 重启或 `ubbase.sh` 重载恢复，**唯一恢复手段是重启服务器**。

### 当前状态
- 用户已明确禁止重启服务器
- 无法继续 128-token smoke 测试及 4096-token 测试
- 需等待 URMA 子系统自行恢复或系统管理员介入

## 已推送提交
- `32546001` — smoke128_01 失败报告（urma_bind_jetty rc=4096）
- `7ecab45a` — smoke128_02~06 重试结果 + 失败报告
- 本报告待推送
