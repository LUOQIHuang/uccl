# URMA 替换边界诊断

目标：先解释 WRITE/FADD 提交与完成行为，避免把忙轮询直接当成 CPU 瓶颈。当前优先运行本页命令，不必先跑旧的 `urma_focus_01 / urma_cpu_01`，本轮也不依赖 perf。

## 本轮测什么

两端固定原来的 4096 tokens、hidden=7168、topk=8、experts=256、SM=24、NVL chunk=4、继承 affinity。保留每个 case 的 24 组正确性检查，只测 4 个调优点：

| 操作 | RDMA chunk | 用途 |
| --- | ---: | --- |
| FP8 Dispatch | 32 | 与既有 verbs 较优配置对齐 |
| BF16 Dispatch | 32 | 同上，观察载荷增加后的差异 |
| Combine | 12 | 本轮 URMA 扫描中的较优点 |
| Combine | 16 | 与既有 verbs 较优配置对齐 |

`run_boundaries.sh` 自动顺序运行两个 job：

1. `<job>_control`：原私有模块，无诊断库。记录常规 profiler/API 计时，以及每配置额外 3 次预热、30 次调用的对照计时。
2. `<job>_trace`：同一个私有模块，加载 CPU 诊断库。常规计时阶段关闭计数；只在随后相同的 3 次预热、30 次调用中启用诊断。预热不计数。

每个配置分别统计，不混入正确性、其他算子或初始化阶段。四个 Proxy 使用各自的计数区，无逐操作日志，无全局热路径互斥锁；启停阶段等待已进入的诊断调用退出再读快照。仍有额外开销，所以两个 job 都保留额外批次计时，不能把插桩耗时当成原始性能。

诊断内容：

- WRITE/FADD/其他 WR 数、成功提交数、WRITE 字节数、提交链最大长度、完成通知/fence 标志、WRITE/FADD 的顺序标志分布。
- WRITE 完整 SGL 总长度，以及首个源/目标 SGE 地址的 mod256 类别：0/64/128/192/其他。包含实际尾块，不以源码名义 chunk 长度替代。
- CQ 轮询次数、空轮询比例、完成数、非空轮询平均完成数，以及阶段内已跟踪请求的最大/剩余在途数量。
- API 调用耗时采样：按首个 opcode 分类，每类每 256 次提交采一次；轮询每 4096 次采一次。
- 被采样的请求，从提交 API 进入到首次轮询观察到完成的延迟分布，按 WRITE/FADD 分开。原始 JSON 保留均值所需总量、样本数、最大值和 log2 ns 直方图。
- 注册段的地址、长度、访问标志、GPU 段标志和成功状态。注册发生在初始化中，不计入稳态传输耗时。

## 覆盖边界与解释限制

本轮直接观察 `urma_post_jetty_send_wr / urma_poll_jfc / urma_register_seg`，不修改请求参数、顺序或完成语义。沿用已经通过的 RELAX WRITE 私有扩展，并核对实际 WRITE 顺序标志、私有 liburma 解析路径、实际调用线程是否来自已识别的 Proxy。只加载但未拦截到 WRITE/FADD/完成会失败，不会输出假成功。

**不覆盖**：GPU 命令产生/读取的内部时间、Proxy 的 capacity 等待和完成回收内部时间、单独的 quiet/barrier 次数与等待时间、最终通知 GPU 的时间。这些位于 C++ 内部，当前二进制的 API 拦截无法完整区分，需要后续匹配源码的插桩构建。网络侧 FADD 也不能仅凭 opcode 自动分成数据通知与 barrier。

“提交到观察到完成”包括 API 执行、诊断开销、设备/显存路径、轮询频率及调度延迟，**不是设备内部执行时间**。最大在途数只涵盖阶段内、同一 Proxy 提交并观察到完成的可跟踪请求，不等同硬件队列深度。`unknown_completions` 和剩余 `inflight` 用于检查阶段边界覆盖；有尾部或跨线程完成时不能据此做精确延迟归因。

空轮询比例高不能单独证明设备慢；API 采样耗时高也不能单独证明它占端到端的同等比例。先结合请求数、字节数、在途数和对照计时判断，再决定下一步插桩位置。

本轮只测 URMA，不能用旧的不同内核 verbs 数字完成纯传输单因素归因。若当前开机环境支持 verbs，可另用 `test/verbs_diag/run.sh --suite boundaries --affinity-mode unpinned --repeats 1`（新 job、对应 verbs 模块）做同开机环境完整调用对照；本诊断库不拦截 verbs。

## 1. 两端准备并本地预检

两边在自己的仓库根目录拉取分支后执行。202 设置 `RANK=0`；204 设置 `RANK=1`：

```bash
git pull --rebase https://github.com/LUOQIHuang/uccl.git codex/metax-urma-integration
export URMA_DIAG_HOME=/home/h00918771
export URMA_DIAG_PYTHON=/home/h00918771/metax_dev/venv-maca-py312/bin/python
RANK=0  # 204 改成 1

bash test/urma_diag/run_boundaries.sh \
  --node-rank "$RANK" --master-addr 141.61.17.202 \
  --bench-dir "$PWD/ep/bench" --job urma_boundary_01 --check-only
```

保留此前已通过的 `URMA_DIAG_LIBRARY_DIR` 等自定义配置。仍需原来的私有模块、私有库、内核、原子和 GPU 空闲条件。

新增依赖仅为主机 `g++` 和匹配私有 UMDK 的头文件，不需要重编译 GPU 扩展。脚本查找私有库同前缀的 include 和常见系统 include；如找不到 `urma_api.h`，先查：

```bash
find /usr/include /usr/local/include /home/h00918771/metax_dev \
  -name urma_api.h -print 2>/dev/null
```

选择与当前私有库匹配的 SDK，将**包含该头文件的目录**设为：

```bash
export URMA_DIAG_INCLUDE_DIR=/实际SDK/include
```

不要使用仓库 `tests/mock_sdk`；它只用于本地 CPU 测试，生产构建会拒绝它。自定义主机编译器可传 `--host-cxx /path/to/g++`。两端会比较诊断源码、实际 SDK 头文件依赖哈希与编译器版本，不比较各自绝对目录或 ELF build ID。

预检先编译小型 CPU 库并运行 4 线程模拟提供者自检（8000 条 WR），验证提交链、完成关联、计数和阶段清零，再核对真实私有 liburma 解析及扩展导入。整个预检无需另一端启动，不运行 GPU benchmark，也不访问设备进行传输。预期看到 `TRACE_BUILD_SELFTEST_OK`、`TRACE_REAL_LIBRARY_OK`、`CHECK_ONLY_PASSED`。

实际 SDK/二进制 ABI 与真实 API 拦截仍由后续正确性、运行路径和统计检查验收；CPU 模拟自检不是硬件兼容性证明。

## 2. 两端同时启动一次正式命令

两端在 180 秒内启动，使用相同 job。脚本会等两端 control 均通过后再进入 trace，不需要手动切换阶段。

```bash
bash test/urma_diag/run_boundaries.sh \
  --node-rank "$RANK" --master-addr 141.61.17.202 \
  --bench-dir "$PWD/ep/bench" --job urma_boundary_01
```

每端应出现两次 `URMA_DIAG_ALL_PASSED`；trace 的日志中应有 4 条 `DIAG_TRANSPORT_OK`，两个 job 各有 4 条 `DIAG_BOUNDARY_OK`。默认一轮，不继续扫其他 chunk。需要复测时换新 job，不覆盖已有目录。

默认输出：

```text
/home/h00918771/metax_dev/urma_diag_results/urma_boundary_01_control/node0 或 node1
/home/h00918771/metax_dev/urma_diag_results/urma_boundary_01_trace/node0 或 node1
```

`transport.jsonl` 是逐配置、逐线程的完整诊断；`boundary_timings.jsonl` 是额外批次对照计时。`metrics.jsonl` 保留原常规计时。build 目录中的 manifest、CPU 自检和构建日志用于复现；`.so` 和可执行文件留在服务器。

## 3. 汇总、收集和 Git 回传

两端都执行，保留本机 `RANK`：

```bash
export VERBS_DIAG_HOME=/home/h00918771
PY=/home/h00918771/metax_dev/venv-maca-py312/bin/python
BASE=urma_boundary_01
for SUFFIX in control trace; do
  JOB="${BASE}_${SUFFIX}"
  RESULT="/home/h00918771/metax_dev/urma_diag_results/$JOB/node$RANK"
  "$PY" test/verbs_diag/summarize.py "$RESULT"
  "$PY" test/verbs_diag/summarize_transport.py "$RESULT"
  "$PY" test/verbs_diag/collect_results.py --backend urma --job "$JOB" --node-rank "$RANK"
done
git add -- "test/bench_results/urma_diag/${BASE}_control/node$RANK" \
  "test/bench_results/urma_diag/${BASE}_trace/node$RANK"
git commit -m "[test] Add URMA API boundary diagnostics"
git pull --rebase https://github.com/LUOQIHuang/uccl.git codex/metax-urma-integration
git push https://github.com/LUOQIHuang/uccl.git HEAD:refs/heads/codex/metax-urma-integration
```

失败时也回传：可跳过两个 summarize，直接对已经生成的 job 执行 collect，再提交。不要删除原日志或自行跳过拦截验证；若 setup 在编译阶段失败，collector 也会带上已生成的 build.log。

本地已用 Linux x86 CPU 模拟提供者验证库的编译和多线程行为，并测试 Python 校验与收集；没有实际 MetaX/URMA 设备执行结果。
