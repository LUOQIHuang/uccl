# CQ_READY ABM 不匹配报告

## 测试

`test/fadd_path/CQ_READY.zh-CN.md` — URMA Normal CQE owner readiness 观测

- JOB: `urma_cq_ready_01`
- TREE: `metax_dev/cq_ready_src_v1` (from `d822cf31`)
- BUILD: `metax_dev/fadd_path_build/urma_cq_ready_v1`
- 内核: `6.6.0-gdr_f00955680_diag` (两端)
- 私有 UMDK: `umdk_private_618957c3/lib`
- Provider Build ID: `a94dc33688722b1604ae5dc2d8f06b783b8e474a` (已由 urma_cqe_visibility_02 确认与设备绑定匹配)

## 结果

`FADD_PATH_PREPARE_PASSED` 双端通过。

`build` **失败**，两端相同错误：

```
cq_ready_probe.hpp:51:35: error: static assertion failed:
  CQ readiness requires the recorded private SDK ABI
  static_assert(sizeof(urma_jfc_t)==248, ...)
  the comparison reduces to (256 == 248)
```

## 根因

`cq_ready_probe.hpp` 中的 `static_assert` 和硬编码偏移是为 `sizeof(urma_jfc_t)==248` 的旧 SDK ABI 编写的。当前私有 SDK (`urma_sdk_private_include/urma_types.h`) 中 `urma_jfc_t` 为 **256 字节**。

### 当前 SDK 的 urma_jfc_t 布局 (256 字节)

| 字段 | 偏移 |
|------|------|
| urma_ctx | 0 |
| jfc_id | 8 |
| jfc_cfg | 32 |
| handle | 64 |
| event_mutex | 72 |
| event_cond | 120 |
| comp_events_acked | 168 |
| async_events_acked | 172 |
| urma_jfc_opt | 176 |
| (总大小) | 256 |

### 探针硬编码偏移

探针在 `urma_poll_jfc` 之前读取 CQE 槽位，需要 CQ base/shift/depth/mask：

| 偏移 | 探针用途 | 与结构体的关系 |
|------|---------|--------------|
| 264 | CQ base | 超出 248 字节结构体 16 字节（私有数据） |
| 328 | shift | 超出 248 字节结构体 80 字节 |
| 332 | depth | 超出 248 字节结构体 84 字节 |
| 336 | mask | 超出 248 字节结构体 88 字节 |
| 432 | log_depth | 超出 248 字节结构体 184 字节 |

在当前 256 字节 SDK 中，这些字段可能在 `urma_jfc_opt_t`（起始于偏移 176）内部，该结构体包含：
- `urma_jfc_cqe_base_addr` (CQ Queue Address)
- `urma_jfc_db_addr` (Doorbell Address)
- `urma_jfc_pi` / `urma_jfc_ci` (PI/CI values)
- `reserved[URMA_OPT_REVERSED_NUM]`

但探针的偏移指向结构体之后的私有内存区域，而非 `urma_jfc_opt_t` 内部字段。这表明 provider 内部在 `urma_jfc_t` 之后分配了私有数据，其布局也随 SDK 版本变化。

## 判定

**CQ_READY_LAYOUT_REJECTED** — 实际 JFC 字段校验失败。

按 README 要求：
> 任何不匹配必须停止并报告版本与私有偏移、SDK 来源和CI变化。不能因为通过测试就关闭这些校验。

探针代码需要由了解当前 256 字节 SDK provider 内部布局的人员更新 `static_assert` 和偏移量。

## 建议的下一步

1. 从 `private_provider/disassembly.log`（urma_cqe_visibility_02 中已采集）中反汇编 `udma_u_poll_jfc`，确定 provider 如何从 JFC 指针访问 CQ base/shift/depth/mask
2. 根据反汇编结果更新 `cq_ready_probe.hpp` 中的 `static_assert(sizeof(urma_jfc_t)==256)` 和偏移量
3. 验证新偏移在运行时读取到正确的 CQ base 地址后，重新构建并运行
