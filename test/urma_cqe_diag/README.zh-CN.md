# URMA Normal 下一项：确认 204 上可用的 CQE 发布观测

已收到 `ddcac8a6` 的详细provider采集并确认私有库绑定。下一项双机CQE有效位观测的代码和运行步骤见 [CQ_READY.zh-CN.md](../fadd_path/CQ_READY.zh-CN.md)。本页保留前序主机采集步骤。

`tail-off` 下，204 采到的 36 条 FADD 都在 SDK 交付完成之前读到了本端返回值。中间还有约 **351 μs** 的中位等待下界，空轮询中位数为 **85 次**。下一项要区分设备晚写出有效 CQE，还是有效 CQE 已写出、SDK 晚交付。应用已能记录返回值和 SDK 交付，缺的是中间的 CQE 发布时刻。[本轮数据](../fadd_path/results/urma_fadd_tail_scope_01/summary_tail_scope/REPORT.zh-CN.md)

## 当前请执行详细采集

已收到 `c93fdd24` 的能力报告。204 与前轮同一次开机，URMA 主库指纹匹配。tracefs 返回权限错误，事件能力仍未知。私有和系统 `liburma-udma.so` 指纹不同，需确认所选设备绑定了哪个 provider，并取得该版本的轮询实现。下面的详细采集会完成这项工作，之前的简要采集步骤保留供查阅。

**只在 204 执行，202 无需启动。** 默认程序初始化 URMA，枚举设备，从 `udmac0d1e2` 的 provider 回调地址解析所属 `.so`，随后调用 `urma_uninit` 退出。收集前先协调 204 的 URMA 设备可用。该程序仅做本进程初始化和枚举，GPU 显存、Jetty、JFC 的创建和 benchmark 均不涉及。

```bash
cd /home/h00918771/uccl-ep-xpu-integration-new
git pull --ff-only https://github.com/LUOQIHuang/uccl.git codex/metax-urma-integration

export URMA_DIAG_PYTHON=/home/h00918771/metax_dev/venv-maca-py312/bin/python
UMDK_LIB=/home/h00918771/metax_dev/urma_after_kernel_20261029/diag/umdk_private_618957c3/lib
SDK_INCLUDE=/home/h00918771/metax_dev/urma_sdk_private_include
OUT="$PWD/test/fadd_path/results/urma_cqe_visibility_02/node1"

bash test/urma_cqe_diag/run.sh \
  --library-dir "$UMDK_LIB" --include-dir "$SDK_INCLUDE" \
  --device udmac0d1e2 --output "$OUT"
```

沿用上一轮已成功的私有动态库环境。脚本会在新子进程的库搜索路径前置 `UMDK_LIB`，用真实 SDK 编译主机端小程序，并使用私有 `liburma.so.0` 初始化。它同时检查主库及 SDK 是否与归档一致。默认输出目录应为新目录。失败保留已经生成的文件，换文件名不能解决缺库或版本不匹配，需要先看日志。

如果当前 204 的设备还不能协调，先运行同一命令并追加 `--inspect-only`，换用输出目录 `urma_cqe_visibility_02/static_node1`。这会收集反汇编、符号、SDK 和追踪事件格式，跳过 URMA 初始化。设备可用时再执行上面的完整命令。

成功标志为 `URMA_PROVIDER_DETAILS_COLLECTED ok=True`。读取 `manifest.json` 的 `device_selection`：

- `state=resolved` 给出本次初始化后设备 provider 回调所在库的路径和 SHA。它确认的是当前私有主库环境的设备绑定，历史 benchmark 的 provider 仍需结合版本和初始化环境核查。
- `matches_private=True` 表示设备绑定库与指定私有 provider 指纹一致。若为 False，脚本额外保存实际选中 provider 的反汇编，优先审查加载差异。
- `state=skipped_inspect_only` 表示只完成静态采集。可先据这些文件分析轮询和查询实现。

**请提供完整目录 `test/fadd_path/results/urma_cqe_visibility_02/`。** 关键文件是 `manifest.json`、`capabilities.json`、`provider_select.log`、`private_provider/` 中的五份 `.log`，以及 `sdk/`。若产生 `selected_provider/` 或错误，连同该目录和 `compile.log` 提供。临时编译的 `provider_select` 二进制无需提交。

`objdump` 和 `readelf` 来自系统 binutils。缺少工具、反汇编失败或初始化失败都会记录在 `manifest.json`。tracefs 仍无权限时，其他静态文件继续收集。若有读 tracefs 权限的人员可协助，使用相同命令和另一新输出目录采集即可。

这一步的验收目标是找到 provider 检查哪个 CQE 字段、如何推进队首，以及 JFC 地址／索引查询是否实际实现。拿到 CQE 格式与合法读取方法，才可实现对同一条请求的低扰动就绪观测。该 CPU 观测会给出 CQE 可读的时间界限；设备的精确发布时刻仍需设备 trace。下一轮双机程序据这里取得的实际实现准备。

## 1. 只在 204 执行能力采集

这一步可以先做，不需要 202 配合，也不占 GPU 测试窗口。脚本读取上次 `tail-off` 的日志，收集当前私有 UMDK、uDMA 内核模块和可读 tracefs 中的版本与事件格式。它不启动 GPU benchmark、不加载模块、不改 tracefs、设备或内核状态。

```bash
cd /home/h00918771/uccl-ep-xpu-integration-new
git pull --ff-only https://github.com/LUOQIHuang/uccl.git codex/metax-urma-integration

DIAG_HOME=/home/h00918771
DIAG_PYTHON="$DIAG_HOME/metax_dev/venv-maca-py312/bin/python"
UMDK_LIB="$DIAG_HOME/metax_dev/urma_after_kernel_20261029/diag/umdk_private_618957c3/lib"
OUT="$PWD/test/fadd_path/results/urma_cqe_visibility_01/provider_probe_node1.json"

"$DIAG_PYTHON" test/urma_cqe_diag/collect.py \
  --library-dir "$UMDK_LIB" \
  --output "$OUT"
```

如果上次实际使用的私有库目录不同，把 `UMDK_LIB` 改成对应路径。输出目录自动创建，已有 JSON 保留，重采时换文件名。成功标志是 `URMA_CQE_CAPABILITIES_COLLECTED`。

**请提供 `provider_probe_node1.json`。** 若输出为 `tracefs=unavailable`，也保留该文件。权限错误表示当前账户不可读取，不能据此判断系统有没有对应事件。若管理员已经批准读取 tracefs，可以由有权限的人员以相同参数另存一份；无需为此更改挂载或权限。

若 204 此时已有本人的 URMA benchmark worker 正在运行，可选用 `--pid` 并换输出文件名。脚本只读取指定进程的 `/proc/<PID>/maps`，这样可确认该进程是否实际映射了 `liburma-udma.so`。**无需为了得到 PID 启动额外 benchmark。** 上一轮 `runtime.json` 在初始化扩展阶段记录库映射，文件里出现 `liburma.so` 并不能证明实际加载的是哪个 uDMA provider。目录里的 `.so` 也只算候选，脚本分别记录两种来源。

## 2. 查收后如何决定实机测试

| 204 返回的证据 | 下一步 |
| --- | --- |
| 有确实记录有效 CQE 发布的设备事件，能关联请求并说明时钟域 | 固定上一轮 `tail-off`、4096-token BF16，做短采集。204 记录同一 FADD 的本端返回值可读、CQE 发布、SDK 交付三个点。202 仅作对端，保留一个关闭探针的性能控制 |
| 只有 `poll_jfc`、CQ 消费或 CQE owner 检查事件 | 先看匹配版本的 provider 源码。轮询时第一次读到 owner 只能给出软件观测时刻，不能当设备发布时间 |
| tracefs 无权限、无相关事件，或事件缺少请求身份和时钟定义 | 先按脚本记录的 provider 路径、SHA256、ELF Build ID 和 `udma` 模块版本找对应私有实现或厂商 trace。原 A/C 不安排补跑 |

如果需要找厂商或驱动同事，最小范围是与上述版本相符的 `liburma-udma.so` 对应的 **`poll_jfc`、CQE 布局及 owner/valid 规则**，或能提供 CQE 发布记录的设备 trace。设备记录需带**请求关联键和时钟信息**。可用的键包括 `user_ctx`，或者 JFC/Jetty ID 与 WQE/CQE 序号的对应关系，并能区分队列回绕。仅有所有请求混合的 CQE 总数或没有定义的设备时间戳不足以做逐请求拆分。

拿到可用的发布事件后，输出只分两段：**返回值可读到 CQE 发布**、**CQE 发布到 SDK 交付**。前段占主导，就查设备完成条件和请求依赖。若有效 CQE 早已在队列里，且确认被其他 Jetty 的队列项挡住，再做共享 JFC 与隔离 JFC 的 A/B。这一阶段的采集脚本用于找到可以实测的中间点，输出本身没有物理 CQE 时间戳。
