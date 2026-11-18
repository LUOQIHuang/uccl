# 下一步：三队列，每队列深度1与4的等量对照

## 1. 只回答这个问题

`urma_pair_load_01`已完成，不再重测：单队列四对时，靠前两对W→A观测间隔约65–73μs，靠后两对约7–10μs；四队列各一对时全部同次轮询返回。单队列第0对间隔中，约95%–97%位于“尚未取得同批全部WRITE完成”的区间。与此同时，整批并发比串行更快，未复现原Normal的427–683μs间隔。

下一步只增加**同样三队列上的每队列未完成对数**，检查这种批内分离能否随负载放大。不测排序开关、不扫tokens、不改绑核。

原Normal每端4个Proxy、12个活跃数据通道，约每Proxy3个活跃通道；每FIFO预算8条未回收命令。若只考虑WRITE/FADD成对，可对应最多4对，但完整程序还有head反馈等其他命令，不能视作实际每时刻恰好4对。本实验只恢复一个线程、三队列及这个负载上界，不能等价整个Proxy。

| 点 | 活跃数据队列 | 每队列最多未完成对数 | 每次迭代操作 |
| --- | ---: | ---: | --- |
| lane3_w1 | 3 | 1 | 每轮三队列各1对，全部完成再发下一轮，共4轮 |
| lane3_w4 | 3 | 4 | 三队列各4对，一轮提交，共12对 |

两点**每次都是12条WRITE＋12条FADD**，共5511168B（12×459264B，384个token payload），总数据量相同。每FADD向对应队列的远端tail计数器加32；每队列每迭代总加128。

两点均创建3条数据Jetty与1条控制Jetty；原Normal每Proxy创建8条数据Jetty、其中约3条活跃，本轮没有还原另外5条空闲端点。两点之间端点数量保持相同。

全部固定：同一主注册区/存储槽、GPU数据与GPU计数器、1线程绑核、WRITE RELAX、FADD STRONG、per_request fence、completion all。每队列内仍先该批全部WRITE再FADD；深度1是W/A，深度4是W/W/W/W/A/A/A/A。两个方向分别单向运行。

“未完成”按本端尚未取得该对全部完成计数，不等于设备内部同时执行数。本轮不包含完整GPU dispatch、FIFO/Proxy主循环、head反馈或同时双向负载，结果不能直接当完整dispatch的优化收益。

## 2. 两端公共环境

取得本次代码后，在仓库根目录执行。目录必须是本机已成功的真实私有SDK位置；如迁移过，只替换相应路径。使用新TREE和新BUILD，不能复用v1扩展。

```bash
export URMA_DIAG_HOME=/home/h00918771
export VERBS_DIAG_HOME="$URMA_DIAG_HOME"
export URMA_PAIR_LOAD_PYTHON="$URMA_DIAG_HOME/metax_dev/venv-maca-py312/bin/python"
export URMA_DIAG_PYTHON="$URMA_PAIR_LOAD_PYTHON"
export VERBS_DIAG_PYTHON="$URMA_PAIR_LOAD_PYTHON"
export URMA_DIAG_LIBRARY_DIR="$URMA_DIAG_HOME/metax_dev/urma_after_kernel_20261029/diag/umdk_private_618957c3/lib"
export VERBS_DIAG_LIBRARY_DIR="$URMA_DIAG_LIBRARY_DIR"
export URMA_DIAG_INCLUDE_DIR="$URMA_DIAG_HOME/metax_dev/urma_sdk_private_include"
export JOB=urma_pair_depth12_01
export TREE="$URMA_DIAG_HOME/metax_dev/urma_pair_load_src_v2"
export BUILD="$URMA_DIAG_HOME/metax_dev/urma_pair_load_build/urma_v2"
export ROOT="$URMA_DIAG_HOME/metax_dev/urma_pair_load_results"
export DEST="$PWD/test/urma_pair_load/results"
test -x "$URMA_PAIR_LOAD_PYTHON"
test -f "$URMA_DIAG_INCLUDE_DIR/urma_api.h"
test -d "$URMA_DIAG_LIBRARY_DIR"
uname -r
mx-smi
```

要求仍为`6.6.0-gdr_f00955680_diag`可运行URMA环境。沿用上一轮成功MACA运行库与设备配置。脚本只检查，不修改内核、PCIe Atomic或runpm。

202单独执行：

```bash
export RANK=0
export CPU=168
```

204单独执行：

```bash
export RANK=1
export CPU=192
```

CPU沿用上轮成功值；不可用时停止，不静默换核。

## 3. 准备、构建、检查

两端定义函数。关键新增参数是`--suite depth12`：

```bash
urma_depth() {
  bash test/urma_pair_load/run.sh --action "$1" \
    --backend urma --suite depth12 --node-rank "$RANK" --job "$JOB" \
    --master-addr 141.61.17.202 --port 34480 \
    --worker-cpu "$CPU" --rounds 3 --warmups 20 --samples 101 \
    --tree "$TREE" --build-dir "$BUILD" --output "$ROOT" --destination "$DEST"
}
urma_depth prepare
urma_depth build
urma_depth check
```

这三步不要求两端同时启动。依次出现 `URMA_PAIR_LOAD_PREPARE_PASSED`、`URMA_PAIR_LOAD_BUILD_PASSED`、`URMA_PAIR_LOAD_CHECK_PASSED`。prepare在固定基线`fed2a437`的独立树上叠加v2代码，保存哈希；build并行数1，沿用原构建/host检查；check验证真实SDK、设备及GPU Atomic条件。

TREE首次必须不存在且在仓库外。若v2目录已经由其他代码准备过并提示哈希变化，保留原目录，将TREE/BUILD都改新后缀，不手改来源文件。失败停止，保留已有日志。每次新终端需重设变量和函数。

## 4. 正式运行

确认没有其他GPU benchmark。202先启动，204在180秒内启动相同命令：

```bash
urma_depth run
```

本轮仅12个点/端：2种深度×2方向×3轮；偶数轮倒序。每点20次预热、101次记录丢首值。最终有1200有效发送迭代、14400有效请求对（每个方向7200对）。每次迭代均校验GPU payload和计数器；校验、TCP同步和JSON输出不在计时内。

最终应看到 `URMA_PAIR_LOAD_RUN_PASSED`，bench.log末尾有 `URMA_PAIR_LOAD_ALL_PASSED`。已有结果不覆盖；失败不自动重跑。

如果中断后需要补收已产生的文件：

```bash
urma_depth collect
```

## 5. 提供哪些结果

保留完整两端目录：

```text
test/urma_pair_load/results/urma_pair_depth12_01/urma/
```

需要overlay_node0/1、cases下build/check/run_gate/run、console；run内需要samples.jsonl、paths.jsonl、plan、affinity、status、driver_status、worker_request/request、manifest、preflight/runtime、bench.log与主机/内核快照。失败也提供已经生成的全部日志。不需要.so、object、cache或独立源码树。

双端文件齐全后，无需GPU即可分析：

```bash
"$URMA_PAIR_LOAD_PYTHON" test/urma_pair_load/analyze.py \
  --root "$DEST/$JOB" --output "$DEST/$JOB/summary_urma"
```

输出目录首次必须不存在；summary_urma也保留。分析器同时兼容已上传的v1 basic4原始结果，不必重跑旧实验。

## 6. 读数与停止条件

优先看同数据量的整批SDK跨度及同轮深度4对深度1的增减，再看每队列各个请求位置的W→A。positions.csv按lane和请求位置列出；pairs.csv新增`wave`、`until_wave_last_write_us`、`after_wave_last_write_us`，将本对W→A按“本轮全部WRITE完成观测”分界。后一个值可为负，表示本对FADD先于其他队列最后WRITE完成被观测，不能截成0或解释为负执行时间。

| 结果 | 如何推进 |
| --- | --- |
| 深度4复现数百μs间隔，且主要在其他WRITE尚未完成的区间 | 负载下的批内推进/通知排序已足以复现观测现象；下一步限定在此最小场景判断是否拖慢整批与远端消费 |
| 深度4间隔变大，但整批更快 | 仍不能宣称性能退化；需区分吞吐改善与通知被推后，不能单看W→A改fence |
| 深度4整批明显变慢，且尾部等待仍很大 | 在这个固定三队列场景内查依赖、设备资源与完成交付，不再叠加参数 |
| 两种深度都未复现 | 一个线程、三队列各最多4对仍不足，下一步回到原Proxy持续服务/多线程/GPU反馈；不重复单对或四对实验 |
| 正确性或稳定性失败 | 保留原日志，先看具体失败，不扩展矩阵、不自动重跑 |

稳定门槛沿用每轮整批CV≤5%、轮均值跨度/中位数≤5%。软件完成观测不能分开设备排队、远端执行、响应生成和provider交付，结论必须保留这一边界；本轮仍未补verbs，因此也不能单独证明URMA特有。

## 7. 开发验证

v2已通过旧basic4与新depth12的真实TCP双端worker/报告检查、遗漏样本拒绝、跨轮次重叠和每队列链式提交约束；实际C++ session在模拟GPU/provider下跑通五种形态、两个方向、各3次，共30次迭代并校验数据/计数器。原URMA host回归及本文7段Bash语法检查通过，v2分析器成功重算已上传v1数据。模拟测试单独把临时测试shim容量从16增为64，以容纳24条请求；生产容量min(2048,1024)未修改，代码还会检查实际容量足够。

开发机没有MetaX GPU，模拟验证不保证真实私有SDK ABI，服务器构建检查和实机校验仍为必要步骤。
