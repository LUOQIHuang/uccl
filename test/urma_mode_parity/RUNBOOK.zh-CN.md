# 双机操作手册：URMA 改造验收与 verbs 对照

适用代码：`codex/metax-urma-integration` 分支，包含 `27054f25` 及后续提交。两台服务器均已拉取同一版本代码。本手册只描述运行与需要提供的日志，不包含文件传输步骤。

## 1. 本轮任务与执行顺序

本轮验证 Normal / Low Latency 两种模式的 URMA 通道分流、WRITE/ATOMIC 批量提交、完成处理及 quiet/barrier，并测量 dispatch/combine。先完成 URMA 的构建、检查和双机运行，再尽量在**相同内核、同一次开机、相同源码与参数**下测 verbs。

之前的 verbs 数据用于历史参考；本轮判断性能差距，以这里重新运行的 verbs 数据为主要对照。首次通过代表本组输入通过验收，性能是否改善还需要分析日志。

| 阶段 | 执行位置 | 是否需要两端同时运行 |
| --- | --- | --- |
| 环境准备、构建 | 两台各自执行 | 否 |
| URMA 环境检查 | 两台各自执行 | 否 |
| URMA benchmark | 两台均执行 | 是，间隔不超过 180 秒 |
| verbs 环境检查 | 两台各自执行 | 否 |
| verbs benchmark | 两台均执行 | 是，间隔不超过 180 秒 |

## 2. 机器与环境要求

本入口对应**两节点、每节点一个 MetaX C500、每节点一个 benchmark 进程**：

| 项目 | 202 节点 | 204 节点 |
| --- | --- | --- |
| `RANK` | `0` | `1` |
| master 地址 | `141.61.17.202` | `141.61.17.202` |
| 仓库目录 | `/home/h00918771/uccl-ep-xpu-integration-new` | 同左 |
| Python | `/home/h00918771/metax_dev/venv-maca-py312/bin/python` | 同左 |
| GPU | GPU 0，`0000:a1:00.0` | 同左 |
| URMA 设备 | `udmac0d1e2` | 同左 |
| Socket 网卡 | `enp33s0` | 同左 |
| verbs HCA | `rocep40s0f0` | 同左 |

URMA 环境沿用此前检查通过的条件：内核 `6.6.0-gdr_f00955680_diag`，已加载 `udma_metax_p2p_bridge`，`runpm_link=0`，GPU 上游 `0000:a0:08.0` 的 Atomic Request Enable 已打开。`check` 会验证这些条件；本手册不自动修改内核、驱动或 PCIe 配置。

两端需要相互访问 TCP `31280`（脚本握手）与 `31281`（torchrun rendezvous），以及现有分布式通信所需网络。运行期间不要同时启动其他 GPU 或 UCCL benchmark。

本次重新编译，除已有运行库外，还需要：

- 与私有 UMDK 库匹配的真实 SDK 头文件，包括 `urma_api.h`，其 `urma_seg_cfg_t` 含 `is_gpu_seg`。
- 现有 MetaX torch、`nanobind`、`ninja`、C++ 编译器，以及 `/opt/maca-3.7.0` 的编译工具链。

不能用普通上游 SDK 补占位字段代替真实私有 SDK。两端都要构建新模块；不要选择历史 `ep_native_relax_*` 或 `ep_host_atomic_*` 的 `.so`。

## 3. 两端准备终端变量

后续命令均在 Bash 中执行。代码块中的反斜杠 `\` 表示续行，必须位于该行最后，后面不要加空格。建议每端保留同一个终端，避免变量丢失。

两端先执行：

```bash
cd /home/h00918771/uccl-ep-xpu-integration-new
set -o pipefail

export URMA_DIAG_HOME=/home/h00918771
export URMA_DIAG_PYTHON=/home/h00918771/metax_dev/venv-maca-py312/bin/python
export MACA_PATH=/opt/maca-3.7.0

# 如果之前通过检查时使用其他私有库目录，改成那个已验证的实际目录。
export URMA_DIAG_LIBRARY_DIR=/home/h00918771/metax_dev/urma_after_kernel_20261029/diag/umdk_private_618957c3/lib

BUILD_JOB=urma_mode_v2_01
URMA_JOB=urma_mode_v2_01
VERBS_JOB=verbs_mode_v2_01
BUILD_DIR="$URMA_DIAG_HOME/metax_dev/urma_mode_parity_build/$BUILD_JOB"
RESULT_ROOT="$URMA_DIAG_HOME/metax_dev/urma_mode_parity_results"
LOG_DIR="$URMA_DIAG_HOME/metax_dev/urma_mode_parity_console/$BUILD_JOB"
mkdir -p "$LOG_DIR"
```

然后只执行对应机器的这一行：

202：

```bash
RANK=0
```

204：

```bash
RANK=1
```

两端分别确认：

```bash
printf 'RANK=%s\nBUILD_DIR=%s\nURMA_JOB=%s\nVERBS_JOB=%s\n' \
  "$RANK" "$BUILD_DIR" "$URMA_JOB" "$VERBS_JOB"
git branch --show-current
git rev-parse HEAD
git status --short
uname -r
mx-smi
test -x "$URMA_DIAG_PYTHON"
ls -l "$URMA_DIAG_LIBRARY_DIR"/liburma.so*
"$URMA_DIAG_PYTHON" -c 'import sys, torch, nanobind, ninja; print("PYTHON=", sys.executable); print("TORCH=", torch.__version__); print("TORCH_PATH=", torch.__file__)'
```

两端 HEAD 应一致，待编译源码不应有未提交改动。Python 应为已有 MetaX 环境，而非 `/usr/bin/python3`；`mx-smi` 应显示 GPU Available 且无其他进程。若某个检查失败，先解决该项，不继续 benchmark。

构建入口自动查找私有库相邻 `include` 及常见系统 include 目录。若自动查找不到，或需要明确选择已知 SDK，在两端设置真实路径：

```bash
# 将下面的路径替换为真实路径，不要原样执行占位路径。
export URMA_DIAG_INCLUDE_DIR=/实际匹配的SDK/include
test -f "$URMA_DIAG_INCLUDE_DIR/urma_api.h"
```

如果变量已设置，确认它没有指向历史测试用的 mock 头文件。不要为了通过编译而手工改 SDK 结构体。

## 4. 两端分别构建新模块

不需要等另一端，可以各自执行：

```bash
bash test/urma_mode_parity/run.sh --action build \
  --node-rank "$RANK" \
  --job "$BUILD_JOB" \
  --build-dir "$BUILD_DIR" \
  --build-jobs 4 \
  2>&1 | tee -a "$LOG_DIR/build_node${RANK}.log"
```

程序会先调用 `ep/tests/run_urma_tests.py`，用真实 SDK 做 CPU 模拟测试；通过后调用 `ep/setup.py build_ext` 编译生产实现。默认构建 `PER_EXPERT_BATCHING=0`，本轮两端都保持默认。

成功输出 `BUILD_PASSED module=...`；输入相同且已经成功构建时输出 `BUILD_ALREADY_PASSED module=...`。产物与日志为：

```text
$BUILD_DIR/
  build_request.json
  host_tests.log
  build.log
  manifest.json          # 构建完成后生成
  lib/ep*.so             # 新扩展；不覆盖已安装或历史模块
  objects/               # 中间产物，无需提供
```

构建时可能暂时没有终端输出，可在另一个终端查看：

```bash
tail -n 80 /home/h00918771/metax_dev/urma_mode_parity_build/urma_mode_v2_01/host_tests.log
tail -n 80 /home/h00918771/metax_dev/urma_mode_parity_build/urma_mode_v2_01/build.log
```

只有 CPU 测试通过才开始 GPU 编译，因此早期失败可能还没有 `build.log`。输入未变的失败构建可以原命令重试，日志追加保存。若源码、SDK 或编译选项改变，应使用新的 `BUILD_JOB` 和 `BUILD_DIR` 重新构建；不要删除 manifest 来绕过检查。

## 5. 两端分别检查 URMA 环境

两端构建成功后，各自执行，无需同时启动：

```bash
bash test/urma_mode_parity/run.sh --action check \
  --node-rank "$RANK" \
  --job "$URMA_JOB" \
  --build-dir "$BUILD_DIR" \
  --backend urma \
  2>&1 | tee -a "$LOG_DIR/urma_check_node${RANK}.log"
```

成功标志：

```text
MODE_PARITY_CHECK_PASSED
```

这一步会检查构建来源、新模块导入、v2 版本、私有 URMA 库实际加载路径，以及前述硬件条件。它不运行 GPU benchmark，也不证明设备收发正确。两端都通过后才继续。

## 6. 双机运行 URMA：先 Normal，再 Low Latency

先在 202 启动，随后在 204 启动，间隔不超过 180 秒。两端执行相同命令，区别由各自的 `RANK` 决定：

```bash
bash test/urma_mode_parity/run.sh --action run \
  --node-rank "$RANK" \
  --master-addr 141.61.17.202 \
  --job "$URMA_JOB" \
  --build-dir "$BUILD_DIR" \
  --backend urma \
  --mode both \
  2>&1 | tee -a "$LOG_DIR/urma_run_node${RANK}.log"
```

不用手工执行 `test_internode.py`、`test_low_latency.py` 或另起 torchrun。脚本负责双端握手、加载新扩展、启动每节点一个进程，并依次执行：

| 模式 | 实际 benchmark | 默认输入 | 本轮测量 |
| --- | --- | --- | --- |
| Normal | `ep/bench/test_internode.py` 的受控副本 | tokens=4096，hidden=7168，experts=256，topk=8 | 24 组正确性；SM=24、NVL chunk=4；FP8/BF16 Dispatch chunk=32；Combine chunk=12/16 |
| Low Latency | `ep/bench/test_low_latency.py` 的副本 | tokens=128，hidden=7168，experts=256，topk=8 | 原正确性矩阵及 dispatch/combine 测量，保留 recv hook 相关结果 |

Normal 同时记录 profiler 与完整调用 CUDA-event 耗时，后者使用 5 次 warmup、15 次测量。两个后端均关闭 CPU affinity 和 adaptive sleep；MCCL 使用 Socket。这里是双机功能与性能的第一轮验收，不是压力测试。

终端会出现 `CASE_BEGIN normal`、`CASE_END normal`，随后是 `low_latency`。两端最后都应输出：

```text
URMA_MODE_PARITY_ALL_PASSED output=...
```

可在另一终端观察详细 benchmark 日志。204 将下列 `node0` 改为 `node1`：

```bash
tail -f /home/h00918771/metax_dev/urma_mode_parity_results/urma_mode_v2_01/node0/normal/bench.log
```

查看 LL 时，将路径中的 `normal` 改为 `low_latency`。文件在对应阶段启动后才生成。Normal 日志应包含 `mode=normal channels=8 endpoints=9 batch=linked`，LL 应包含 `mode=low_latency channels=1 endpoints=1 batch=linked`，两种模式都应有 `TRANSPORT_MODE_OK`。

每个 case 默认最多运行 1800 秒。失败时停止后续测试，保留两端日志；不要立即重复启动同一个 job。

## 7. 同一次开机运行 verbs 对照

URMA 成功后，若当前内核环境同时支持 verbs，直接复用刚才构建的同一模块。保持源码、SDK、GPU 状态和测试参数一致，不切内核、不另开 GPU 作业。

先在两端确认所选 HCA 存在：

```bash
ls -ld /sys/class/infiniband/rocep40s0f0
ibv_devinfo -d rocep40s0f0
```

然后各自执行导入与构建来源检查：

```bash
bash test/urma_mode_parity/run.sh --action check \
  --node-rank "$RANK" \
  --job "$VERBS_JOB" \
  --build-dir "$BUILD_DIR" \
  --backend verbs \
  2>&1 | tee -a "$LOG_DIR/verbs_check_node${RANK}.log"
```

预期仍为 `MODE_PARITY_CHECK_PASSED`。verbs 的 `check` 不验证 URMA 硬件条件，也不验证实际 verbs 收发；后续运行前的 idle gate 会检查 HCA 对应设备。因为复用的是包含 URMA 支持的新扩展，仍保留前面的私有库环境。

两端检查通过后，先 202、再 204，在 180 秒内执行：

```bash
bash test/urma_mode_parity/run.sh --action run \
  --node-rank "$RANK" \
  --master-addr 141.61.17.202 \
  --job "$VERBS_JOB" \
  --build-dir "$BUILD_DIR" \
  --backend verbs \
  --mode both \
  2>&1 | tee -a "$LOG_DIR/verbs_run_node${RANK}.log"
```

这会选择 verbs 后端，运行与 URMA 相同的 Normal/LL 输入。当前脚本对 verbs 成功也输出 `URMA_MODE_PARITY_ALL_PASSED`；实际后端以 `setup.json` 和 `TRANSPORT_MODE_OK backend=verbs` 为准。

如果当前环境不支持 verbs，保留错误并告知。先完成 URMA 验收，不为获得 verbs 结果盲目更换内核；若必须换环境，应记录变化，分析时不能当作同环境对照。

## 8. 重跑与常见问题

- **某次 run 失败或已运行过**：结果目录不允许覆盖。先保存旧结果，下一次两端一起改成 `URMA_JOB=urma_mode_v2_02` 或 `VERBS_JOB=verbs_mode_v2_02`；源码未改时保持 `BUILD_DIR` 不变，重用新模块。为分清终端输出，把相应 `tee` 的文件名也改为新的编号。
- **`PRIVATE_SDK_HEADERS_MISSING` / 编译提示没有 `is_gpu_seg`**：提供与私有库对应的真实 SDK 路径。若已尝试过构建且更换了 SDK，改用新的构建目录。
- **`PRIVATE_UMDK_MISSING` / `PRIVATE_URMA_LIBRARY_REQUIRED`**：检查 `URMA_DIAG_LIBRARY_DIR` 是否为之前验证过的目录及实际加载路径；不要改用系统库来绕过检查。
- **`SOURCE_CHANGED_SINCE_BUILD` / `BUILD_INPUTS_CHANGED`**：源码或构建输入与记录不一致，换新 `BUILD_JOB`、更新 `BUILD_DIR`，两端重新构建。
- **`PAIR_STOP`**：同时查看两端终端日志和 `setup.json`。另一端失败、源码/SDK/内核/参数不一致都可能触发，单端日志不够判断。
- **连接超时**：确认两端已经启动，202/204 的 `RANK` 分别为 0/1，master 地址正确，31280/31281 未被其他作业占用。如果端口确需更改，两端同加 `--port 31380`，对应 rendezvous 使用 31381。
- **`GPU_NOT_IDLE_OR_UNAVAILABLE` / `DEVICE_BUSY_OR_CHECK_FAILED` / `EXISTING_BARRIER`**：查看 `gpu_before.txt`、`preflight_error.txt` 和终端输出，确认占用进程与屏障所属作业。脚本不删除已有屏障，不要直接清空 `/dev/shm`。
- **Atomic、内核、P2P bridge 等硬件检查失败**：提供完整 check 日志，按已验证的机器配置处理；不要跳过检查强行跑。
- **只有一端报错，另一端仍等待**：不要另开一组测试；等脚本退出或停止本轮仍在运行的对应进程，收齐两端日志后再处理。

首次失败的日志通常最有价值。不要修改代码、SDK、硬件设置后继续往同一份结果中追加 benchmark。

## 9. 需要提供哪些日志

**需要两端的数据。`node0` 对应 202，`node1` 对应 204。不要只提供某一端或只截取性能行。** 本节只列文件，不要求运行任何传输命令。

### 9.1 成功运行后

提供两端以下结果目录中的 `.json`、`.jsonl`、`.log`、`.txt` 和根目录的 benchmark `.py` 副本。若使用新 job 编号，路径也改为实际编号：

```text
/home/h00918771/metax_dev/urma_mode_parity_results/urma_mode_v2_01/node0/
/home/h00918771/metax_dev/urma_mode_parity_results/urma_mode_v2_01/node1/
/home/h00918771/metax_dev/urma_mode_parity_results/verbs_mode_v2_01/node0/
/home/h00918771/metax_dev/urma_mode_parity_results/verbs_mode_v2_01/node1/
```

每台服务器只会生成自己对应的 `node`。其中关键文件为：

| 文件 | 用途 |
| --- | --- |
| `setup.json` | 后端、输入、版本指纹和初始化错误 |
| `preflight.json` | 私有库、设备检查与导入信息 |
| `build_manifest.json` | 构建源码、SDK、选项和本机模块 SHA |
| `host.json`、`lscpu.txt`、verbs 的 `ibv_devinfo.txt`（若生成） | 内核、boot ID、CPU 与 HCA 信息 |
| `normal.py`、`low_latency.py` | 本次实际运行的 benchmark 副本 |
| `normal/bench.log`、`low_latency/bench.log` | 完整正确性、模式、性能及错误输出 |
| 两种模式各自的 `metrics.jsonl` | 结构化耗时数据 |
| 两种模式各自的 `status.json` | 退出码和各项验收结果 |
| 两种模式各自的 `runtime.json` | 实际模块、torch 与加载库 |
| 两种模式各自的 `counters_before.json`、`counters_after.json` | 设备计数器前后对照 |
| 两种模式各自的 `gpu_before.txt`、`urma_preflight.json`（URMA 若生成） | 开跑前设备状态 |

另外提供两端本手册保存的终端日志：

```text
/home/h00918771/metax_dev/urma_mode_parity_console/urma_mode_v2_01/
  build_node<RANK>.log
  urma_check_node<RANK>.log
  urma_run_node<RANK>.log
  verbs_check_node<RANK>.log
  verbs_run_node<RANK>.log
```

### 9.2 构建或运行失败时

- 构建失败：两端已生成的 `build_node<RANK>.log`，以及 `$BUILD_DIR/host_tests.log`、`build.log`、`build_request.json`；成功一端若已有 `manifest.json`，也一并提供。早期失败不存在的文件无需补造。
- check 失败：对应 `urma_check_node<RANK>.log` 或 `verbs_check_node<RANK>.log` 的完整输出，包含 traceback；同时提供该端构建 `manifest.json`（若存在）。check 本身不创建运行结果目录。
- run 失败：两端对应 `*_run_node<RANK>.log`，以及该轮结果目录中已生成的上述文件；额外包含 `preflight_error.txt`（若生成）。不要只提供最后的 `PAIR_STOP`。

不需要提供 `.so`、`objects/`、`cache/`、`tmpdir/`、虚拟环境、SDK 本体或 GPU 数据。若 verbs 暂未运行，只提供已有 URMA 数据，并说明 verbs 未运行的原因。
