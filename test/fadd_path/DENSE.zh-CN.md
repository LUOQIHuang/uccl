# 下一步：URMA返回值与SDK轮询的局部分解

## 1. 目的和数据依据

已完成同口径verbs/URMA对照：202的WRITE→对应FADD完成观测间隔中位数4.165/682.990μs；204为0/427.350μs（verbs零值源于同一批SDK返回）。URMA204至少34.71%的所采请求间隔发生在返回值已可见之后，但49.39%仍落在采样不确定窗口。旧观测每64次轮询采样，返回值窗口宽度中位数249.23μs；旧“最大检查点间隔141.74μs”也不能作为CPU真正停顿时间。

本轮只解决两个缺口：

1. 每次相关SDK轮询前读取现有FADD返回槽，收紧R的最后0/首次非0界限，观察不确定范围是否明显缩小。
2. 记录同一局部窗口每次SDK调用入口/返回，将W→A，以及实际首次读到R之后→A，逐请求精确分成SDK调用内时间与调用之间时间。两者在该本端观测区间内可相加，不能加到整体Dispatch。

不重新测verbs，不扩展tokens、LL、绑核、chunk、FIFO数量或顺序开关矩阵。无需修改provider源码就可做本轮；它仍不能直接取得物理CQE就绪时刻。

## 2. 与上一轮的唯一采集变化

`--poll-stride 1`取代64：仍只跟踪每个Proxy的ring0第6条大WRITE/相邻FADD，在相关请求存续期间记录。每窗口4对、9个有效窗口，每端36对，记录上限仍每Proxy4096条。没有增加GPU打点/主机采样时钟线程、请求或控制marker，不写返回槽，不改fence/STRONG、窗口8、ordered回收、提交策略和原算法。

SDK时间戳包围实际查询调用；两次SDK调用之间的时间包含其他Proxy工作、调度和新探针本身的开销，不称为CPU空闲。稠密观测也可能扰动内部时序，必须保留同模块pre/probe/post控制。5%门槛失败则停止正常性能归因，保留现有日志，不自动降采样或重新跑矩阵。

SDK包围时间也包含少量计时包装开销。调用间隙的累计占比大，不等于CPU造成了同样多的净延迟：设备尚未产生可交付完成时，CPU处理其他工作会与设备等待重叠。判断CPU是否显著推迟发现完成，应看单次长间隙及其位置，不能把累计占比直接当根因占比。

使用新TREE、新BUILD、新JOB；旧工作区不要改写。新源码叠加会核查采样步长、bundle及所有插入文件SHA，两端必须一致。默认步长仍64，旧JSON仍可按旧配方分析。

## 3. 两端设置

仅在已验证可运行URMA的环境进行正式构建、检查及测试。当前若仍是RDMA内核，可以先prepare，待URMA环境可用再继续；不要求为本轮额外复测RDMA。

202的RANK=0，204的RANK=1。下面路径沿用上轮成功环境；私有SDK路径使用你实际已验证的目录。

```bash
cd /home/h00918771/uccl-ep-xpu-integration-new
set -euo pipefail
export VERBS_DIAG_HOME=/home/h00918771
export URMA_DIAG_HOME="$VERBS_DIAG_HOME"
export VERBS_DIAG_PYTHON="$VERBS_DIAG_HOME/metax_dev/venv-maca-py312/bin/python"
export URMA_DIAG_PYTHON="$VERBS_DIAG_PYTHON"
export FADD_PATH_PYTHON="$VERBS_DIAG_PYTHON"
export MACA_PATH=/opt/maca-3.9.0
export URMA_DIAG_LIBRARY_DIR="$URMA_DIAG_HOME/metax_dev/urma_after_kernel_20261029/diag/umdk_private_618957c3/lib"
export VERBS_DIAG_LIBRARY_DIR="$URMA_DIAG_LIBRARY_DIR"
export URMA_DIAG_INCLUDE_DIR="$URMA_DIAG_HOME/metax_dev/urma_sdk_private_include"
export PYTHONDONTWRITEBYTECODE=1
RANK=0                       # 204改为1
JOB=fadd_dense_01
TREE="$URMA_DIAG_HOME/metax_dev/fadd_dense_src_v1"
BUILD="$URMA_DIAG_HOME/metax_dev/fadd_path_build/urma_dense_v1"
ROOT="$URMA_DIAG_HOME/metax_dev/fadd_path_results"
DEST="$PWD/test/fadd_path/results"
uname -r
mx-smi
test -f "$URMA_DIAG_INCLUDE_DIR/urma_api.h"
test -d "$URMA_DIAG_LIBRARY_DIR"

fadd_dense() {
  bash test/fadd_path/run.sh --action "$1" \
    --backend urma --poll-stride 1 --node-rank "$RANK" --job "$JOB" \
    --master-addr 141.61.17.202 --port 33980 --gid-index 3 \
    --tree "$TREE" --build-dir "$BUILD" --output "$ROOT" --destination "$DEST"
}
```

其他运行库环境沿用上轮成功设置，不混用其他MACA版本。不能与其他GPU测试同时运行。

## 4. 准备、构建、检查

两端分别执行；这些步骤不用同时启动：

```bash
fadd_dense prepare
fadd_dense build
fadd_dense check
```

依次应看到FADD_PATH_PREPARE_PASSED、FADD_PATH_BUILD_PASSED、FADD_PATH_CHECK_PASSED。失败时停止，提供已有日志，不绕过预检查。若TREE已存在但来源不匹配，保留目录，换新的TREE/BUILD，不覆盖旧证据。

## 5. 正式运行

202先执行，180秒内在204执行同一命令（RANK不同）：

```bash
fadd_dense run
```

自动执行pre（关）→probe（BF16局部开启）→post（关）。每项仍保留原正确性检查；5次预热、10次计时、丢首值后9个有效样本，不额外跑128 smoke或LL。两端最终应看到FADD_PATH_RUN_PASSED。

collector自动保留两端日志。失败会收集已有记录后停止。若手动中断后需要补收：

```bash
fadd_dense collect
```

## 6. 提供哪些结果

提供完整目录：

```text
test/fadd_path/results/fadd_dense_01/urma/
```

包括两端overlay/capabilities、build/check/pre/probe/post及console。核心是probe下两端`fadd_path.jsonl`；还需要api_samples、sample_summary、status/runtime/setup和构建manifest验证代表性及来源。无需提供.so、object、cache或临时目录。

两端文件齐全后可分析，分析不需要GPU：

```bash
"$FADD_PATH_PYTHON" test/fadd_path/analyze.py \
  --root "$DEST/$JOB" --backend urma --poll-stride 1 \
  --output "$DEST/$JOB/summary_urma_dense"
```

输出目录必须不存在。提供summary_urma_dense内REPORT、pairs、bounds、api_controls、decision及context。不要把stride1结果传给默认stride64配方。

## 7. 输出和停止判据

先看`mechanism_usable`和BF16 pre/probe/post门槛，失败保留原始记录，不给正常程序根因结论。随后看有效R覆盖数、采样窗口宽度和以下新字段：

| 字段 | 含义 |
| --- | --- |
| dense_sdk_us / dense_between_sdk_us | W→A内SDK调用累计时间／两次SDK调用之间累计时间 |
| dense_max_sdk_us / dense_max_between_sdk_us | 最长单次SDK调用／最长调用间隙 |
| after_seen_elapsed_us | 首次实际读到非0返回值后仍等待的时长；不冒充物理返回时刻 |
| after_seen_sdk_us / after_seen_between_sdk_us | 上述后续等待内，SDK调用／调用之间各占多少 |
| after_seen_max_sdk_us / after_seen_max_between_sdk_us | 上述后续等待内的最长调用／最长间隙 |

逐请求必须有SDK调用时间＋调用间隙＝同一观测区间，否则分析失败。任何缺失调用序号、记录溢出或两端stride不同都不填补时间。缺少R分界仍保留完整W→A分解，但不编造R前后数据。反向完成顺序仍保留，不强行改成正值。

| 新证据 | 下一步方向 |
| --- | --- |
| R已见后SDK单次调用很长，SDK内时间占比大 | 重点查SDK/provider查询函数内部路径 |
| SDK调用短，但调用之间出现同量级长间隙 | 重点查Proxy其他工作、调度及采集开销；不能直接叫CPU空闲 |
| R已见后密集短轮询仍多次返回0，间隙也短 | 收敛到完成发布/可见性/provider可交付条件，需要匹配私有实现才能继续拆 |
| R窗口仍宽且对应长查询间隙 | 采样不确定性本身已定位到服务间隙，使用间隙证据，不盲目加点 |
| 代表性失败或返回分界覆盖差 | 保留失败证据，停止扩大矩阵，不原样反复运行 |

本轮不是新增优化，也不会证明所有返回后等待都能等额改善整体Dispatch。后续若需要区分CQE物理发布与provider识别，仍需要实际运行版本的provider/驱动实现。

## 8. 开发端验证

已通过13项测试覆盖的CPU检查（Windows下native项跳过后，在WSL单独通过stride64/1两版C++记录器与Python分析联测）。检查包括逐调用区间守恒、缺失/重叠时间戳拒绝、同批完成零间隔、返回值删失、完整报告及扰动门槛失败保留。旧URMA/verbs五份报告/CSV/判定输出均可逐字节复现；文档5段Bash命令语法通过。没有本地MetaX GPU，本轮仍需实机验证，不预先保证密集探针扰动通过门槛。
