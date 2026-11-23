# Normal tail-off 的 CQE 就绪观测

2026-11-23优先级调整：本专项已修复构建错误，但暂不作为下一项必跑实验。先按[Normal大区间复盘](../reference_events/NORMAL_REVIEW_20261123.zh-CN.md)及[匹配运行步骤](../reference_events/NORMAL_REVIEW_RUN.zh-CN.md)确定优化后剩余差距的主要位置。只有证据继续指向提交后完成链路时，再启用下方专项。本页保留代码和步骤，不需要同时执行两套任务。

## 2026-11-23 构建错误修复与重跑

已查收提交 `49fd92a6`。两端prepare成功，build的CPU transport检查都被 `sizeof(urma_jfc_t)==248` 静态断言阻断，实际值为256。正式benchmark尚未运行，本次没有新增时延数据。precision编译日志中的MACA warning不是这次停止的原因。

此前探针误把x86_64 glibc下的公共结构大小用于AArch64。归档SDK的结构含 `pthread_mutex_t`，它在x86_64为40字节、服务器AArch64为48字节，使公共JFC分别为248和256字节。两端此次SDK头文件指纹与归档一致，并非已确认的SDK换版。修复按架构校验大小及 `urma_jfc_opt` 偏移，未关闭ABI保护，无需替换私有SDK和UMDK。

不能因公共结构大小差8字节就把私有偏移整体平移。实际provider在 `c024` 保存JFC指针到x26，`c054` 设置x25=x26+256；`c0f0–c148` 使用以下绝对偏移。它本来就针对256字节的AArch64公共结构。保留这些偏移，并继续核对实际provider Build ID、队列布局及CI消费数。

| 私有字段 | 相对实际JFC指针的偏移 | 指令依据 |
| --- | ---: | --- |
| CQ base | 264 | x25+8 |
| consumer index | 300 | x26+300 |
| CQE shift | 328 | x25+72 |
| depth | 332 | 运行时与SDK jfc_cfg.depth及mask交叉检查 |
| mask | 336 | x25+80 |
| log depth | 432 | x26+432 |

两端直接从下方第1节重新开始，使用更新后的 **JOB=urma_cq_ready_02、TREE=cq_ready_src_v2、BUILD=urma_cq_ready_v2**。旧目录保留，不手改旧worktree，不复用失败构建。仍是prepare → build → check → run；无需重新采集provider，不重跑此前fence矩阵。仅probe阶段204运行时应新增 `[CQ_READY_LAYOUT] sdk_jfc_bytes=256 opt_offset=176 mutex_bytes=48 ...` 日志。pre/check不启用owner读取，因此没有这行属于正常现象。若运行时仍报布局不符，保留日志，不平移偏移或移除校验。

本地验证覆盖归档私有SDK上的x86_64 C++探针、owner回绕和队首阻塞模拟、Python分析和overlay检查；AArch64构建和真实设备读数仍需按下述流程在服务器确认。本轮目的不变，区分返回值已见之后“目标CQE尚不可读”和“目标已有效但SDK尚未交付”。

本轮用实际 provider 的有效位规则，检查 204 的 FADD 返回值已可读后，目标 CQE 是否已可读取、是否排在无效队首之后。固定 **URMA、Normal、4096 tokens/rank、BF16 Dispatch、hidden7168、experts256、top-k8、SM24、NVL/RDMA chunk4/32**。两端都运行，202 为对端，只有204开启CPU探针。每端原有24组Normal正确性检查仍执行。

运行顺序为 **pre → probe → post**，三项全部是202全开、204只关tail fence，保持FADD STRONG、窗口8和原提交回收策略。每项5次预热、10次计时、丢首值后9个有效样本。pre/post关闭记录，probe在204的4个Proxy各选ring0第6对WRITE/FADD。202不启动GPU事件或映射时钟线程。

## 代码如何找到对应 CQE

探针在原 `urma_poll_jfc` 之前，只读接下来16条CQE首字的owner位，并记录消费索引。SDK照常查询和消费。随后将SDK实际返回的请求身份、返回序号和消费索引增量核对，把目标FADD关联到实际被消费的CQ位置，再回查先前的有效位记录。该过程不根据请求先后推测CQ位置。

有效位规则来自实机私有provider `udma_u_poll_jfc` 的 `0xc0f0–0xc148`，库SHA为`18ea061e598330ba56ac07d7f9b4691414884aaf805c7f7a1973b1bc5c028967`、Build ID为`a94dc33688722b1604ae5dc2d8f06b783b8e474a`。运行器核对库SHA，实际JFC创建后再检查轮询回调所属库的Build ID、SDK ABI、stride/depth/mask等布局。任何不匹配立即停止，不套用其他版本的私有偏移。SDK消费数与CI增量不一致也会报错。

只有目标进入16条前视范围时才判断它的有效性。若目标在SDK返回前始终未被采到有效，分析保留删失；不能因此宣称设备此前没有发布CQE。首次观测有效和最后一次观测无效给出软件可读的时间界限，硬件精确发布时间仍需设备trace。

## 1. 两端拉取并设置环境

在已验证可跑URMA的 `6.6.0-gdr_f00955680_diag` 内核执行。协调202/204的GPU0和URMA设备可用。202设置 `RANK=0`，204设置 `RANK=1`，其余变量相同。需要新的TREE和BUILD，旧模块不包含CQE观测代码。

```bash
cd /home/h00918771/uccl-ep-xpu-integration-new
git pull --ff-only https://github.com/LUOQIHuang/uccl.git codex/metax-urma-integration
set -euo pipefail

export URMA_DIAG_HOME=/home/h00918771
export VERBS_DIAG_HOME="$URMA_DIAG_HOME"
export URMA_DIAG_PYTHON="$URMA_DIAG_HOME/metax_dev/venv-maca-py312/bin/python"
export VERBS_DIAG_PYTHON="$URMA_DIAG_PYTHON"
export FADD_PATH_PYTHON="$URMA_DIAG_PYTHON"
export MACA_PATH=/opt/maca-3.9.0
export URMA_DIAG_LIBRARY_DIR="$URMA_DIAG_HOME/metax_dev/urma_after_kernel_20261029/diag/umdk_private_618957c3/lib"
export VERBS_DIAG_LIBRARY_DIR="$URMA_DIAG_LIBRARY_DIR"
export URMA_DIAG_INCLUDE_DIR="$URMA_DIAG_HOME/metax_dev/urma_sdk_private_include"

RANK=0                   # 204改为1
JOB=urma_cq_ready_02
TREE="$URMA_DIAG_HOME/metax_dev/cq_ready_src_v2"
BUILD="$URMA_DIAG_HOME/metax_dev/fadd_path_build/urma_cq_ready_v2"
ROOT="$URMA_DIAG_HOME/metax_dev/fadd_path_results"
DEST="$PWD/test/fadd_path/results"

cq_ready() {
  bash test/fadd_path/run.sh --action "$1" --tail-scope --cq-ready \
    --backend urma --poll-stride 1 --node-rank "$RANK" --job "$JOB" \
    --master-addr 141.61.17.202 --port 33980 \
    --tree "$TREE" --build-dir "$BUILD" --output "$ROOT" --destination "$DEST"
}
uname -r
mx-smi
```

其他MACA及私有运行库环境沿用上轮成功设置。端口33980和33981须可用，如需变更，两端统一修改函数中的 `--port`。

## 2. 两端分别准备、构建和预检

```bash
cq_ready prepare
cq_ready build
cq_ready check
```

成功标志依次为 `FADD_PATH_PREPARE_PASSED`、`FADD_PATH_BUILD_PASSED`、`FADD_PATH_CHECK_PASSED`。prepare把固定生产基线和本轮探针写到隔离worktree；构建使用真实私有SDK，并执行已有CPU transport检查。预检确认模块、环境和tail角色能力。运行中的实际JFC布局还会在run启动后核对。

若提示 `CQ_READY_REQUIRES_RECORDED_PRIVATE_PROVIDER`，保留错误并核对UMDK目录和文件SHA。`CQ_READY_PROVIDER_BUILD_ID_MISMATCH` 表示实际回调库与本轮布局不符。`CQ_READY_LAYOUT_REJECTED` 表示真实JFC字段校验失败。均应提供日志后排查。不能为通过测试关闭这些校验。

## 3. 两端正式运行

202先启动，204在180秒内启动同一命令，两端JOB相同。

```bash
cq_ready run
```

自动执行同模块pre/probe/post。最终两端均应出现 `FADD_PATH_RUN_PASSED`。预检或任一case失败后停止，脚本收集已有日志。手动中断后如需补收：

```bash
cq_ready collect
```

## 4. 上传结果

提供完整目录 `test/fadd_path/results/urma_cq_ready_02/urma/`。保留两端构建、console、setup、host、runtime、status、bench日志及三项API原始采样。关键新增数据为204 probe下的 `normal/fadd_path.jsonl`，其中 `cq_ready` 保存每次poll的CI、16位有效掩码、时钟、SDK返回数及请求关联。该文件的原有FADD记录同时提供返回值分界。

两端结果汇齐后可离线分析：

```bash
"$FADD_PATH_PYTHON" test/fadd_path/analyze_cq_ready.py \
  --root "$DEST/$JOB" --output "$DEST/$JOB/summary_cq_ready"
```

同时提供 `summary_cq_ready/` 的报告、decision、controls、pairs、paired_calls和context。输出目录应为新目录。每端每点9个有效调用，204共36对目标请求；这些请求属于同一组调用窗口，不当作36次独立benchmark。

## 5. 验收和下一步选择

先看 `controls.csv` 和 `decision.json`。两端及对应调用取大值主指标都检查轮内CV、前后漂移、probe相对控制均值变化，均以已有5%门槛判定。门槛失败时保留带探针现象和原始日志，再分析扰动来源，不自动重跑或扩展矩阵。

| 记录到的状态 | 能支持的结论与下一步 |
| --- | --- |
| `target_valid_head_invalid_polls > 0` | 对应FADD的CQE已有效、队首尚无效。观察到完成队列消费阻塞，下一轮验证隔离JFC能否让完整Dispatch更快 |
| 目标CQE在队首已有效，SDK仍较晚返回 | 检查同次轮询内对其他完成的处理及目标解码/映射路径，并核对观测成本 |
| 目标进入前视范围后，多次无效，直到交付前才有效 | 返回后等待至少部分延续到目标CQE不可读阶段，优先查设备完成条件、发布及内存可见性 |
| 目标始终不在范围内或未捕获有效瞬间 | 保留未知，关注覆盖数，不能把零条就绪记录解释成没有提前发布 |

局部几百微秒的等待不能直接加到完整Dispatch上。只有修改完成队列或请求策略后，无探针的完整调用也改善，才算取得新的净收益。
