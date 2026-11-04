# v2_02 结果分析与原子控制段对齐修复

## 数据结论

两端四组探针共 8 项全部成功，Normal 双机仍在注册阶段失败。原始日志确认：

- 主数据段 `0xa04c00000`、483892608B 注册成功，其他线程命中注册缓存。
- 失败的是 GPU 原子控制段 `0xa24a03600`、81960B、atomic=1，其地址对 4096 取余为 `0x600`。
- 探针控制段 `0xa0c000000` 同为 81960B，注册成功。探针来自 torch 分配，benchmark 来自 `mcExtMallocWithFlags(..., mcDeviceMallocFinegrained)`，分配路径确有差异。

这支持优先修复生产控制段未对齐的问题。`errno=0` 只表示 provider 没有设置 errno，不能据此单独断言是用户态校验或排除资源问题。探针主数据长度为 117506432B，benchmark 为 483892608B，因此也不能宣称两组测试的唯一差异是地址。

## 修复内容

1. URMA MetaX 原子控制段继续使用细粒度 GPU 内存；申请 90112B backing，返回其中 4KiB 对齐的地址作为 81960B 控制段。
2. GPU、CPU、URMA 注册和所有 Proxy 使用同一个对齐地址，逻辑偏移和控制段长度不变。
3. 共享注册的所有权记录保留原始分配地址，最后一个注册/所有者释放后才调用对应释放接口；不向 mcFree 传内部对齐指针。
4. 检查分配状态和空指针，申请及地址运算检查溢出；未对齐 GPU 原子段在调用 SDK 前明确拒绝。
5. probe 默认改用与 benchmark **同一个生产控制段分配函数**，可用 `--probe-control torch` 保留旧分配方式作对照。

verbs 分配路径不变。Normal 的 8 数据+1控制通道配置也不变。当前是有针对性的代码修复，真实设备是否通过须以下面复测结果确认。

## 1. 两端重新构建

两台拉取本次提交，在同一 Bash 终端执行。202 设置 `RANK=0`，204 设置 `RANK=1`：

```bash
cd /home/h00918771/uccl-ep-xpu-integration-new
set -o pipefail
export URMA_DIAG_HOME=/home/h00918771
export URMA_DIAG_PYTHON=/home/h00918771/metax_dev/venv-maca-py312/bin/python
export URMA_DIAG_LIBRARY_DIR=/home/h00918771/metax_dev/urma_after_kernel_20261029/diag/umdk_private_618957c3/lib
export URMA_DIAG_INCLUDE_DIR=/home/h00918771/metax_dev/urma_sdk_private_include
RANK=0  # 204 改成 1
BUILD_DIR=/home/h00918771/metax_dev/urma_mode_parity_build/urma_mode_v2_03
LOG_DIR=/home/h00918771/metax_dev/urma_mode_parity_console/urma_mode_v2_03
mkdir -p "$LOG_DIR"

bash test/urma_mode_parity/run.sh --action build \
  --node-rank "$RANK" --job urma_mode_v2_03 --build-dir "$BUILD_DIR" \
  2>&1 | tee "$LOG_DIR/build_node${RANK}.log"
```

预期 `BUILD_PASSED`。本次涉及生产实现，不能复用 v2_02 模块。

## 2. 每台各跑一次生产分配探针

无需双机配对；本轮只需一个 4 transport / 8 channel case，主数据长度改为与真实 Normal 相同：

```bash
bash test/urma_mode_parity/run.sh --action probe \
  --node-rank "$RANK" --job reg03_production --build-dir "$BUILD_DIR" \
  --mode normal --probe-channels 8 --probe-transports 4 \
  --probe-bytes 483892608 --probe-control production \
  2>&1 | tee "$LOG_DIR/probe_node${RANK}.log"
```

预期 `REGISTRATION_PROBE_PASSED`。`probe.log` 中应有 `[URMA_ALLOC]`，显示 `finegrained=1`、`alignment=4096`，其 aligned 地址末三位为 0；随后 GPU `atomic=1` 注册成功，其他 transport 命中缓存。

如果没有 finegrained=1，记录日志；这说明编译平台宏没有进入原生产细粒度路径，需先核对构建环境，不能把这项当作该路径已验证。

## 3. 两端在 180 秒内启动 Normal

两端探针通过、GPU 空闲后，先 202 再 204：

```bash
bash test/urma_mode_parity/run.sh --action run \
  --node-rank "$RANK" --master-addr 141.61.17.202 \
  --job urma_normal_v2_03 --build-dir "$BUILD_DIR" \
  --backend urma --mode normal \
  2>&1 | tee "$LOG_DIR/normal_node${RANK}.log"
```

预期两端最终 `URMA_MODE_PARITY_ALL_PASSED`。如果注册成功后出现新的建链、正确性或完成处理错误，保留完整日志；此时不要再归为同一个注册故障。

## 4. Normal 通过后，两端再跑 LL

仍先 202 再 204，在 180 秒内执行：

```bash
bash test/urma_mode_parity/run.sh --action run \
  --node-rank "$RANK" --master-addr 141.61.17.202 \
  --job urma_ll_v2_03 --build-dir "$BUILD_DIR" \
  --backend urma --mode low_latency \
  2>&1 | tee "$LOG_DIR/ll_node${RANK}.log"
```

URMA 两种模式通过后再按 [双机运行手册](RUNBOOK.zh-CN.md) 做同环境 verbs 对照，`BUILD_DIR` 指向本次 v2_03 构建。暂不增加其他调优变量。

## 需要提供的日志

两端对应 `node0 / node1` 的实际生成文件：

- `metax_dev/urma_mode_parity_results/reg03_production/node<RANK>/`：`probe.log`、`status.json`、`preflight.json`、`build_manifest.json`、内核快照与主机信息。
- `metax_dev/urma_mode_parity_results/urma_normal_v2_03/node<RANK>/` 及运行过的 `urma_ll_v2_03/node<RANK>/`：完整文本/JSON 结果，尤其是 `bench.log`、`runtime.json`、`metrics.jsonl`、`status.json` 和内核快照。
- 本次 `metax_dev/urma_mode_parity_console/urma_mode_v2_03/` 终端日志；构建失败时另提供构建目录中的 `host_tests.log`、`build.log` 和 `build_request.json`。

不需要 `.so`、`objects/`、`cache/` 或 `tmpdir/`。结果目录不能覆盖；重跑时使用新 job 名称，源码未变可以继续复用本次 `BUILD_DIR`。
