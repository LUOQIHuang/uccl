# Normal scope smoke ACK超时：结论与恢复步骤

适用结果：`results/normal_scope_20261123/normal_scope_urma_01_smoke128/`，上传提交 `3d4d1752`。这是运行失败的排查，不是新一轮性能调参。

## 1. 本轮实际停在哪里

| 节点 | 原始证据 | 判断 |
| --- | --- | --- |
| 204 / node1 | `normal/bench.log:315–320`，首次BF16正确性检查，`URMA_COMPLETION_ERROR status=9 token=65536 wr_id=18446744073709551615 write=0 atomic=1`，随后SIGABRT | 首条明确的传输错误：内部控制原子请求ACK超时 |
| 202 / node0 | `normal/bench.log:373–398`，`internode_prepare`、`cmd:4`等待，最终 `DeepEP error: timeout (dispatch CPU)` | 与对端失败相符的屏障等待超时，不是独立的CPU性能测量 |
| 两端 | 仅有layout约132 μs，正确性未通过，无正式Dispatch采样 | 本轮不能更新URMA与verbs性能差距或阶段占比 |

SDK中的 `URMA_CR_ACK_TIMEOUT_ERR` 枚举值为9，含义是重传超过上限。可核对仓库归档的 `test/fadd_path/results/urma_cqe_visibility_02/node1/sdk/urma_opcode.h`；统一为Linux换行后的SHA256与本次build_manifest记录一致。日志 `rank=0` 来自本机GPU local_rank；204也只有GPU0，不能据此把错误归给202。

结合首次prepare阶段和代码，失败请求对应204向202提交的arrival屏障FADD：

```text
204第一次Dispatch prepare
  → CPU Proxy提交内部arrival屏障FADD
  → 目标：202注册的主机内存barrier_words[0]
  → 204得到错误完成：status=9 / ACK超时
  → 204进程中止
  → 202的prepare无法继续，最终CPU等待超时
```

代码位置：`ep/src/proxy.cpp` 的 `kArrivalSlot` / `post_barrier_atomic_add`，`ep/src/urma_transport.cpp` 的 `barrier_words_` 注册与 `post_barrier_atomic_add`。`UINT64_MAX` 是内部WR标记，不是正常数据tail FADD的WR编号；“具体是arrival屏障”来自阶段与调用路径联合判断，错误日志本身没有打印远端地址。

这纠正了上传报告中的两点：失败发生在首次BF16正确性prepare，不是FP8性能阶段；204是SIGABRT失败退出，不能称为正常退出。缺少trace文件是运行提前失败的后果。tail-off只修改Normal数据tail FADD，不修改内部Barrier的STRONG/fence，此错误不能直接归因于tail-off。

## 2. 已知环境变化与当前证据边界

| 项目 | 202 | 204 |
| --- | --- | --- |
| 本次GPU0 PCI | `0000:21:00.0` | `0000:21:00.0` |
| 本次管理网/OOB接口 | `enp40s0` | `enp34s0f5u1u4` |
| 管理网IP | `141.61.17.202` | `141.61.17.204` |
| URMA设备 | `udmac0d1e2` | `udmac0d1e2` |

当前202的 `a1:00.0` 是NIC，204没有这个端点，旧的GPU PCI默认值已不适用。历史PCI编号不能仅凭当前枚举反推过去一定指向同一硬件。GPU实际父桥必须从sysfs路径确认，不能继续只检查固定的 `a0:08.0`。

IP ping和MCCL/NCCL Socket bootstrap成功，仅证明相应IP通路可用，不能排除URMA EID/TP/远端原子通路问题。注册成功也不等于远端原子请求可完成。此次首先失败的是主机内存屏障FADD，现有证据尚不足以将其定为GPU P2P、GPU RAS、provider、驱动或某条链路中的特定故障。全启动周期的RAS消息还需和本次运行的增量对齐。

## 3. 本次代码修复

1. `run.sh`、`run_boundaries.sh`支持 `--ifname`、`--gpu-pci`、`--atomic-port`、`--case-timeout`，无需改driver源码。
2. 实际接口统一用于UCCL/MCCL/NCCL。两端接口名称允许不同，真实值仍记录在各自setup；跨端只取消本地接口名的相等要求，源码、库、工作负载和实验参数仍严格匹配。
3. GPU预检确认PCI端点是显示/加速器类，并核实Atomic端口属于该GPU的真实祖先。`--atomic-port auto`只选择并读取最近父桥，不写PCI配置；ReqEn未开仍明确失败。
4. 修复boundaries + batch参数被拒绝的问题，同时允许该控制保留本轮固定的peer-tail-off。此项是正式coverage将遇到的另一个脚本错误，不是此次ACK超时的原因。
5. 父进程每0.5秒增量检查日志；出现URMA错误完成、Dispatch CPU超时或Python异常，停止本次私有进程组，保存 `case_failure.json`，避免失败后长时间卡在退出。128门禁默认最多300秒；跨端配对另有原有180秒等待。这里不查找或终止其他任务。
6. 恢复入口自动记录运行前后URMA状态、GPU真实PCI父链、接口、模块、EID可见信息、服务和内核日志，区分ACK超时与对端CPU等待。

这些是脚本和预检修复，不代表设备ACK超时已被修好。没有改GPU kernel、URMA传输算子或fence语义。现有构建若通过源码/模块哈希检查可以复用，不需要因这些脚本修改重新编译。

## 4. 两端准备：保留原失败结果和旧worktree

以下两端都执行；202设RANK=0，204设RANK=1。原有失败目录和带手工修改的旧worktree保留，使用新源码目录，避免覆盖本地修改。两端固定同一个提交。若新目录已存在，换一个尚未使用的目录名。

```bash
cd /home/h00918771/uccl-ep-xpu-integration-new
git pull --ff-only https://github.com/LUOQIHuang/uccl.git codex/metax-urma-integration
set -euo pipefail
REPO="$PWD"
SNAPSHOT=$(git rev-parse HEAD)
printf 'SOURCE_COMMIT=%s\n' "$SNAPSHOT"

RANK=0                       # 204改为1
export URMA_DIAG_HOME=/home/h00918771
export VERBS_DIAG_HOME="$URMA_DIAG_HOME"
export URMA_DIAG_PYTHON="$URMA_DIAG_HOME/metax_dev/venv-maca-py312/bin/python"
export VERBS_DIAG_PYTHON="$URMA_DIAG_PYTHON"
export MACA_PATH=/opt/maca-3.9.0
export URMA_DIAG_LIBRARY_DIR="$URMA_DIAG_HOME/metax_dev/urma_after_kernel_20261029/diag/umdk_private_618957c3/lib"
export VERBS_DIAG_LIBRARY_DIR="$URMA_DIAG_LIBRARY_DIR"
export URMA_DIAG_INCLUDE_DIR="$URMA_DIAG_HOME/metax_dev/urma_sdk_private_include"
export URMA_DIAG_GPU_PCI=0000:21:00.0
export URMA_DIAG_ATOMIC_PORT=auto
if [ "$RANK" = 0 ]; then
  export URMA_DIAG_IFNAME=enp40s0
else
  export URMA_DIAG_IFNAME=enp34s0f5u1u4
fi
unset UCCL_SOCKET_IFNAME FADD_PATH_CAPTURE FADD_PATH_TAIL_SCOPE FADD_PATH_CQ_READY FADD_PATH_CQ_CAMPAIGN

TREE="$URMA_DIAG_HOME/metax_dev/normal_scope_urma_src_v2"
BUILD="$URMA_DIAG_HOME/metax_dev/normal_scope_build/urma_v1"
ROOT="$URMA_DIAG_HOME/metax_dev/normal_scope_results"
DEST="$REPO/test/reference_events/results/normal_scope_20261124_recovery"
JOB=normal_scope_urma_02
git worktree add --detach "$TREE" "$SNAPSHOT"
cd "$TREE"
uname -r
mx-smi
ip -br address show dev "$URMA_DIAG_IFNAME"
readlink -f "/sys/bus/pci/devices/$URMA_DIAG_GPU_PCI"
lspci -s "$URMA_DIAG_GPU_PCI" -nn
test -f "$BUILD/manifest.json"
```

先确认GPU0和接口与输出一致。GPU位置或接口又变化时，以实际输出修改相应环境变量，不直接改源码。这里使用上传报告中已完成的urma_v1构建；若实际目录不同，修改BUILD。若出现 `SOURCE_CHANGED_SINCE_BUILD`，停止并保留日志，不跳过检查；只有生产源码确实变化时才按主文档构建新目录。

## 5. 只做一次关闭采集的128-token连通检查

目的：确认首次Dispatch控制屏障是否在没有活动采集时也失败，并同时取得当前URMA运行前后状态。保留原Normal正确性检查，不拿此项数据计算正式性能。

两端依次启动同名任务，202先启动，204尽量在60秒内启动：

```bash
bash test/reference_events/recover.sh \
  --step paced --reference-profile boundaries \
  --normal-fadd-fence-plan peer-tail-off \
  --node-rank "$RANK" --master-addr 141.61.17.202 --port 33480 \
  --ifname "$URMA_DIAG_IFNAME" --gpu-pci "$URMA_DIAG_GPU_PCI" \
  --atomic-port auto --case-timeout 300 \
  --build-dir "$BUILD" --job "${JOB}_connect" \
  --output "$ROOT" --destination "$DEST"
```

自动采集是只读的，不重置设备、不重载驱动、不清理共享内存、不改EID或PCI配置。日志仍可能包含运行前已有错误；`dmesg_delta.txt`及JSON会标注能否取得精确追加差分。

两端均须得到 `CONNECTION_CONTROL_RESULT run_rc=0 snapshot_rc=0 collect_rc=0`。失败立即停止，不继续4096，也不循环重跑：

| 结果 | 后续操作 |
| --- | --- |
| `GPU_PCI_IS_NOT_DISPLAY_OR_ACCELERATOR` / `GPU_PCI_MISSING` | 按mx-smi确认GPU0 PCI，修正GPU变量 |
| `ATOMIC_PORT_NOT_GPU_ANCESTOR` | 不再用旧固定父桥；检查记录的祖先路径 |
| `PCIE_ATOMIC_REQUEST_NOT_ENABLED` | 记录错误指出的真实端口及只读lspci结果，请环境负责人确认该路径的原子支持和配置；本脚本不修改硬件 |
| `URMA_ACK_TIMEOUT` / `status=9` | 关闭采集仍失败，优先检查URMA EID、TP、对端主机原子可达性和本次内核增量；将两端connection目录交由设备/驱动负责人分析。当前应用日志不能区分故障在请求、远端执行还是ACK返回 |
| `DISPATCH_CPU_TIMEOUT_CHECK_PEER` | 先看对端第一条错误；不要把此CPU等待当成性能瓶颈证据 |
| 关闭采集通过、后续trace门禁失败 | 保留这两个case，才有依据针对活动采集或其时序影响继续排查 |

## 6. 连通通过后，回到原主线

只有两端上一步都通过才运行。先trace128门禁，成功后运行原五项coverage，不增加其他调参或tokens扫描：

```bash
scope() {
  bash test/reference_events/run_boundaries.sh \
    --part "$1" --backend urma --node-rank "$RANK" \
    --master-addr 141.61.17.202 --port 33480 \
    --normal-fadd-fence-plan peer-tail-off \
    --ifname "$URMA_DIAG_IFNAME" --gpu-pci "$URMA_DIAG_GPU_PCI" --atomic-port auto \
    --build-dir "$BUILD" --job "$JOB" --output "$ROOT" --destination "$DEST"
}
scope smoke
```

确认两端均为 `BOUNDARY_SUITE_PASSED ... part=smoke` 后，两端再执行：

```bash
scope coverage
```

coverage仍是batch_pre → paced → trace → post → batch_post。整体差距、FIFO等待与接收输出边界的判断标准沿用 [Normal主文档](NORMAL_REVIEW_RUN.zh-CN.md)，恢复排查不改变研究目标。

## 7. 需要提供的日志

提供完整 `test/reference_events/results/normal_scope_20261124_recovery/`，两端都要，包括：

- `${JOB}_connect/node0`、`node1` 和 `console/node*/connection/`：状态、bench、case_failure（若有）、前后connection JSON、内核日志及诊断JSON。
- 若继续运行，保留smoke和五项coverage的两端全部自动收集目录。
- 原始失败目录保留不覆盖。记录本轮两端SOURCE_COMMIT；不需要上传.so或objects。

当前最短路径是先恢复主机内存控制原子通路，再做同点性能比较。ACK错误持续时继续改性能参数不会得到可用结论。

## 8. 开发侧验证范围

本地没有MetaX GPU，未声称完成服务器运行或修复ACK故障。已验证真实上传日志分类、两种后端参数解析、跨端接口比较、shell编排和失败进程清理，包括torchrun worker单独创建session的情况。reference_events、verbs_baseline及URMA硬件辅助CPU测试通过。WSL仅有Python3.6，跑reference_events全套时在测试进程内补了 `perf_counter_ns`，不修改生产脚本；服务器仍使用原Python3.12。

另跑urma_mode_parity原有45项CPU测试，44项通过，旧 `test_annotations_preserve_original_kernel_statements` 失败；已确认在本次修改前的HEAD源码上同样失败，涉及旧kernel文本等价检查。本次没有修改 `ep/src/internode.cu` 或其他生产kernel，未将这项旧失败算作通过。
