# 下一步：三队列，每队列深度1与4的等量对照

当前窗口只有 RDMA 时，先按[RDMA 运行步骤](DEPTH12_VERBS.zh-CN.md)采集同工作量对照。下文是 URMA 运行步骤。两边默认使用同一个 JOB，结果按后端保存在独立子目录。

URMA 双端 `urma_pair_depth12_01` 已完成。当前 URMA 窗口优先做完整 Normal 密集采样，有余量再做同深度的链内顺序对照。[实测结果](results/urma_pair_depth12_01/ANALYSIS.zh-CN.md) · [当前 URMA 窗口步骤](ORDER12.zh-CN.md)

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

取得本次代码后，在仓库根目录执行。使用本机已验证的私有 SDK 路径。v4 的源码树可由两后端复用，构建路径按后端区分。已准备 v3 的用户可按原提交 `026522c9` 运行，已采集的 v2/v3 结果可直接参加对照。

```bash
export URMA_DIAG_HOME=/home/h00918771
export VERBS_DIAG_HOME="$URMA_DIAG_HOME"
export URMA_PAIR_LOAD_PYTHON="$URMA_DIAG_HOME/metax_dev/venv-maca-py312/bin/python"
export URMA_DIAG_PYTHON="$URMA_PAIR_LOAD_PYTHON"
export VERBS_DIAG_PYTHON="$URMA_PAIR_LOAD_PYTHON"
export MACA_PATH=/opt/maca-3.9.0
export URMA_DIAG_LIBRARY_DIR="$URMA_DIAG_HOME/metax_dev/urma_after_kernel_20261029/diag/umdk_private_618957c3/lib"
export VERBS_DIAG_LIBRARY_DIR="$URMA_DIAG_LIBRARY_DIR"
export URMA_DIAG_INCLUDE_DIR="$URMA_DIAG_HOME/metax_dev/urma_sdk_private_include"
export JOB=urma_pair_depth12_01
export TREE="$URMA_DIAG_HOME/metax_dev/urma_pair_load_src_v4"
export BUILD="$URMA_DIAG_HOME/metax_dev/urma_pair_load_build/urma_v4"
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
export CPU=192
```

204单独执行：

```bash
export RANK=1
export CPU=192
```

两端都用 CPU192，和 RDMA 对照对齐。202 的 GPU 本地 CPU 列表在两个内核下不同，旧 URMA 串行使用的 CPU168 在 RDMA 内核下属于其他 NUMA 节点。以本次实际绑定和 GPU NUMA 记录为准。已有 v2 URMA 结果如果沿用 CPU168，仍可用于该后端内部的深度对照，跨后端对照时单列该差异。

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

这三步各节点可独立执行。依次出现 `URMA_PAIR_LOAD_PREPARE_PASSED`、`URMA_PAIR_LOAD_BUILD_PASSED`、`URMA_PAIR_LOAD_CHECK_PASSED`。prepare 在固定基线 `fed2a437` 的独立树上叠加代码并保存哈希。build 沿用原构建和 CPU 检查。check 验证真实 SDK、设备及 GPU Atomic 条件。

TREE首次必须不存在且在仓库外。若已由其他源码准备过并提示哈希变化，保留原目录，将TREE/BUILD都改新后缀。失败时保留已有日志。每次新终端需重设变量和函数。

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

优先看同数据量的整批 SDK 跨度及同轮深度 4 对深度 1 的增减。再看各队列请求位置的 W→A，以及 P95、最大值和超过 100/400 μs 的对数。`pairs.csv` 同时保留该批同队列及全队列最后 WRITE 完成观测两个分界。`after_*_last_write_us` 的负值表示 FADD 完成记录先于该分界被读到。

| 结果 | 如何推进 |
| --- | --- |
| 深度4复现数百μs间隔，且主要在其他WRITE尚未完成的区间 | 负载下的批内推进/通知排序已足以复现观测现象；下一步限定在此最小场景判断是否拖慢整批与远端消费 |
| 深度4间隔变大，但整批更快 | 仍不能宣称性能退化；需区分吞吐改善与通知被推后，不能单看W→A改fence |
| 深度4整批明显变慢，且尾部等待仍很大 | 在这个固定三队列场景内查依赖、设备资源与完成交付，不再叠加参数 |
| 两种深度都未复现 | 一个线程、三队列各最多4对仍不足，下一步回到原Proxy持续服务/多线程/GPU反馈；不重复单对或四对实验 |
| 正确性或稳定性失败 | 保留原日志，先看具体失败，不扩展矩阵、不自动重跑 |

稳定门槛沿用每轮整批CV≤5%、轮均值跨度/中位数≤5%。软件完成观测混合设备排队、远端执行、响应生成和 provider 交付。RDMA 同工作量对照用于区分两个后端是否复现同一种现象。各自的内核、设备、完成 API 和顺序语义仍列入比较条件。

## 7. 开发验证

已通过真实 TCP 双端流程及报告检查。C++ 会话在模拟设备下做了 URMA 30 个、verbs 12 个迭代的 payload 和计数器检查。分析器能读取已上传的 v1 结果。模拟测试使用容量 64 的临时 shim 以容纳深度 4 的 24 条请求。

开发机没有MetaX GPU，模拟验证不保证真实私有SDK ABI，服务器构建检查和实机校验仍为必要步骤。
