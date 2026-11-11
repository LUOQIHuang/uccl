# 下一轮：编译期精简打桩的关闭代价验证

**本轮已完成：[分析报告](results/urma_dispatch_rolesoff_01_summary/ANALYSIS.zh-CN.md)。Dispatch关闭代价仍超过门槛，暂不启动采集。下一步按 [CODEGEN.zh-CN.md](CODEGEN.zh-CN.md) 只读取现有编译产物，不重跑以下benchmark。**

## 为什么还需要这一步

最新compiled-off结果确认：完整打桩模块即使不启动采集，FP8 Dispatch仍慢约92.7%、BF16慢约56.0%，Combine慢约28.5%。这属于测量模块引入的扰动，原始URMA/verbs瓶颈尚未证明。不能继续分析旧trace内部时间。

此次不是原样重复。新开关 `UCCL_GPU_STAGE_ROLES_ONLY=1` 在编译前删除详细探针及其参数求值，只保留Dispatch整体46和五类角色53～57；去掉该构建的算子标记与诊断launch检查，保留范围强制内联。未改变通信算法、WRITE/atomic顺序或队列策略。**开发端没有MetaX GPU，尚不能保证精简版扰动已低于5%。**

本轮先不启动采集，仅验证编入6个低频范围后的关闭代价。入口会拒绝roles-only active trace。两端完成base → off → post后提供数据即可，不追加其他测试。

## 1. 两端准备

拉取更新后，两端执行以下设置，202的RANK为0，204改为1：

```bash
cd /home/h00918771/uccl-ep-xpu-integration-new
set -euo pipefail
export URMA_DIAG_HOME=/home/h00918771
export VERBS_DIAG_HOME="$URMA_DIAG_HOME"
export URMA_DIAG_PYTHON=/home/h00918771/metax_dev/venv-maca-py312/bin/python
export VERBS_DIAG_PYTHON="$URMA_DIAG_PYTHON"
export MACA_PATH=/opt/maca-3.9.0
export URMA_DIAG_LIBRARY_DIR=/home/h00918771/metax_dev/urma_after_kernel_20261029/diag/umdk_private_618957c3/lib
export URMA_DIAG_INCLUDE_DIR=/home/h00918771/metax_dev/urma_sdk_private_include
# SDK头文件路径及EXTRA_LIBRARY_DIRS/LIBRARY_PATH沿用此前验证通过的值。

RANK=0  # 204改为1
BASE=/home/h00918771/metax_dev/dispatch_diag_build/urma_base_roles_v1
TRACE=/home/h00918771/metax_dev/dispatch_diag_build/urma_roles_v1
ROOT=/home/h00918771/metax_dev/dispatch_diag_results
DEST="$PWD/test/dispatch_diag/results"
JOB=urma_dispatch_rolesoff_01
mkdir -p "$ROOT" "$DEST"
uname -r
mx-smi
```

URMA仍需已验证的 `6.6.0-gdr_f00955680_diag`；不切内核、不改驱动。保持本轮MACA3.9环境及私有库一致。上面SDK include只是已有文档路径，若实际成功路径不同，使用实际路径。

## 2. 两端分别构建两份模块，各一次

**本次修改了GPU源文件，必须使用新的build目录。**旧base/trace不能混入，脚本会核对源码。base和roles必须在同一提交、同一MACA/SDK下构建；每份仅1个编译任务。

```bash
bash test/dispatch_diag/run.sh --action build --backend urma \
  --step baseline --node-rank "$RANK" --job urma_roles_build_base_01 \
  --build-dir "$BASE" --output "$ROOT"

bash test/dispatch_diag/run.sh --action build --backend urma \
  --step trace --probe-set roles --node-rank "$RANK" --job urma_roles_build_trace_01 \
  --build-dir "$TRACE" --output "$ROOT"
```

每次成功标志 `BUILD_PASSED`，复用同一构建则 `BUILD_ALREADY_PASSED`。编译前包含CPU模拟和compile-only precision probe。若编译失败停止后续测试，保留console、build.log、host_tests.log、manifest/build_request、precision_probe日志；不要改用旧模块跳过失败。

构建失败时可收集现有日志（BUILD_JOB填写失败项）：

```bash
BUILD_JOB=urma_roles_build_trace_01
"$URMA_DIAG_PYTHON" test/gpu_stage_recovery/collect.py "$ROOT/$BUILD_JOB" \
  --node-rank "$RANK" --base-build "$BASE" --trace-build "$TRACE" \
  --destination "$DEST/$BUILD_JOB"
```

## 3. 两端各一条测试命令

202先启动，180秒内启动204，JOB必须相同：

```bash
bash test/dispatch_diag/run_compiled_off.sh \
  --backend urma --probe-set roles --node-rank "$RANK" \
  --master-addr 141.61.17.202 --port 32880 \
  --base-build "$BASE" --trace-build "$TRACE" \
  --job "$JOB" --output "$ROOT" --destination "$DEST"
```

自动完成三个独立进程的base/off/post，失败停止并收集当前run日志；成功末尾为 `DISPATCH_COMPILED_OFF_ALL_PASSED`。off仍完全不调用GPU采集接口，不应产生gpu_stages.jsonl，不要求dispatch_coverage。

固定Normal、4096 tokens、hidden7168、experts256、topk8、每节点GPU0；每点50次预热、50次计时，丢首样本后49个有效样本。保留FP8/BF16 Dispatch、layout与Combine12/16控制点，不跑LL。新JOB不覆盖旧结果；如重跑两端一起改JOB尾号。

## 4. 两端齐全后汇总

```bash
"$URMA_DIAG_PYTHON" test/dispatch_diag/summarize_compiled_off.py \
  --baseline "$DEST/${JOB}_base" --compiled-off "$DEST/${JOB}_off" \
  --post "$DEST/${JOB}_post" --output "$DEST/${JOB}_summary"
```

输出compiled_off_controls.csv与context.json，后者明确记录probe_set=roles。汇总校验源码、模块SHA、稀疏编译标记、运行库、boot、采样边界及正确性，拒绝错配模块。

Dispatch前后漂移、CV和off相对前后基线均值变化均在5%以内，才表示通过大扰动筛选，不等同于统计显著性或内部采集已可用。若off仍明显变慢，保留结果并停止；若接近基线，再由下一版入口启用少量角色采集，验证active状态代表性。不要自行打开旧的全量trace。

## 5. 需要提供的数据

```text
test/dispatch_diag/results/urma_dispatch_rolesoff_01_base/
test/dispatch_diag/results/urma_dispatch_rolesoff_01_off/
test/dispatch_diag/results/urma_dispatch_rolesoff_01_post/
test/dispatch_diag/results/urma_dispatch_rolesoff_01_summary/  # 能汇总则提供
```

三个JOB均需两端node0/node1、console及**collector生成的build/node0、build/node1目录**。上一轮未包含build日志，这次请保留：build.log、host_tests.log、manifest、build_request、precision_probe的request与compile日志。它们有助于继续检查编译路径；如日志未给出寄存器/资源报告，不能据此确认spill。

不需.so、objects，不需重新提供旧全量trace。若在最开始的check失败，参考[关闭采集文档](COMPILED_OFF.zh-CN.md)的手动collector命令收集对应JOB。构建失败则只提供第2节收集的构建失败目录，无需继续跑benchmark。

已修复全局build/忽略规则隐藏collector日志的问题，选定构建日志和manifest可正常纳入结果，二进制仍被忽略。开发端通过74项Python测试、两后端CPU模拟、稀疏探针参数不求值测试，以及两端入口参数/失败停止/日志收集模拟；本文5个Bash代码块通过语法检查。未在本地进行MetaX GPU编译或benchmark。
