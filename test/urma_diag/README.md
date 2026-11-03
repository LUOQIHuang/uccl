# URMA Normal 初步定位

当前已进入生产实现对齐阶段，请使用 [Normal/LL v2 构建与验收](../urma_mode_parity/README.md)。下面及 [替换边界诊断](BOUNDARIES.zh-CN.md) 保留历史 v1 私有模块的诊断流程；这些流程要求匹配的旧 benchmark 源码，不能与当前 v2 benchmark 混用。

本入口复用 test/verbs_diag 的 24 组正确性检查、22 个固定调优点、独立 API CUDA-event 计时、原始日志和线程快照。只在结果目录生成 benchmark/utils 副本，不修改原文件或私有二进制，不修改 WRITE/FADD/fence/quiet/barrier 的语义。

## 准备

两端先 git pull 到同一提交。在自己的仓库根目录执行（例如 /home/h00918771/uccl-ep-xpu-integration-new）：

```bash
export URMA_DIAG_HOME=/home/h00918771
export URMA_DIAG_PYTHON=/home/h00918771/metax_dev/venv-maca-py312/bin/python
```

需要的是历史通过的 RELAX WRITE 私有模块，和此前 verbs 模块不同：

```text
202: /home/h00918771/metax_dev/urma_after_kernel_20261029/diag/ep_native_relax_from_204_4f3b30a6/module/ep.abi3.so
204: /home/h00918771/metax_dev/urma_after_kernel_20261029/diag/ep_native_relax_4f3b30a6/module/ep.abi3.so
```

预期 SHA256：5826d74b98e5f80843ad419e09dc15737a79aa9f3d3854405b553da56db1fe2e。

还需要自己目录下 `metax_dev/urma_after_kernel_20261029/diag/umdk_private_618957c3/lib/`。必须能真正加载这里的 liburma；脚本不接受悄悄回退到 /usr/lib64/liburma。可设置 URMA_DIAG_LIBRARY_DIR 或 --urma-lib-dir，脚本将核对实际映射路径，并比较两端该目录动态库的哈希。

历史 libtrace.so 的源码包含旧目录绝对路径，本测试不会加载它，也不要求原用户的 trace 库。运行中清除 host-atomic 开关，使用指定的 GPU 原子段私有模块；不把“链接了 liburma”当成 URMA 已初始化。

## 环境检查

202：

```bash
bash test/urma_diag/run.sh --node-rank 0 --master-addr 141.61.17.202 \
  --bench-dir "$PWD/ep/bench" --job urma_home_check --check-only
```

204：将 --node-rank 0 改为 1。检查无需同时启动，不运行 benchmark。

默认检查历史已验证的配置：

- 内核 6.6.0-gdr_f00955680_diag（不绑定历史 boot ID）。
- /sys/class/uburma/udmac0d1e2、/dev/uburma/udmac0d1e2 及 udma_metax_p2p_bridge。
- runpm_link=0；GPU 0000:a1:00.0 runtime_status 为 active/suspended。
- 上游 0000:a0:08.0 的 PCIe AtomicOp 请求使能位。
- 本次开机没有已知 GPU 队列故障记录；有权限时检查 debugfs 队列状态。
- 私有模块、根目录 benchmark 源码哈希、私有 liburma 实际加载位置。

setpci 在这里仅用于读取，不修改 PCIe、内核或设备配置。等效的新 URMA 环境可显式设置 --expected-kernel、--gpu-pci、--atomic-port、--urma-device；需要先确认这些值对应已验证的配置，不以改参数掩盖未启用的 P2P/原子能力。

CHECK_ONLY_PASSED 只表示环境/导入检查，不证明数据传输已经通过；真正验收由后续正确性与双端退出完成。

## 双端测试

先用 1 轮确认环境，再视结果决定是否扩到 3 轮，不预先占用更多测试时间。每轮含继承 affinity 和显式绑核两个 case；首轮先跑继承 affinity 的历史基线，再测绑核。两端 180 秒内启动，使用同一个新 job；默认控制端口 31180，torchrun 31181。

202：

```bash
bash test/urma_diag/run.sh --node-rank 0 --master-addr 141.61.17.202 \
  --bench-dir "$PWD/ep/bench" --job urma_diag_home_01 --repeats 1
```

204：

```bash
bash test/urma_diag/run.sh --node-rank 1 --master-addr 141.61.17.202 \
  --bench-dir "$PWD/ep/bench" --job urma_diag_home_01 --repeats 1
```

如果只做一轮，只能初筛，不能据此下稳定性或统计结论。确认无环境问题后，换新 job 并用 --repeats 3 进行正式比较。

## 本轮具体比较什么

- 输入、SM、NVL chunk、RDMA chunk、预热和计时方法与 verbs_diag 相同。
- pinned：默认指定 CPU 4、5、6、7，可用 --urma-cpus 修改成四个不同的在线 CPU。它们是前一轮 verbs 的对照 CPU，不宣称是 URMA 的最优 NUMA 选择。
- unpinned：保留父线程的 affinity，不等于取消整个 cgroup/运行库的 CPU 限制。
- URMA 初始化原本跳过 native Proxy 绑核调用，因此这里不靠切换旧环境变量“假装绑核”。生成的 utils.py 只把 proxy.start_dual() 替换为诊断回调：临时限制发起线程，创建原生 Proxy std::thread 使其继承指定 mask，然后立即恢复发起线程。
- 从 Proxy 内继续派生的辅助线程也可能继承该限制。因此这是线程启动位置控制实验，并非只改变一个已命名 Proxy 线程的绝对隔离实验。
- 记录创建时新 TID；每个配置检查仍存活的已观察线程 group 是否保持指定 mask。如果其他库覆盖 affinity 或无法识别，会失败而非把无效 pin 当作成功。
- 固定 URMA TM=rc、指定 UB 设备、GPU 原子模式，关闭自适应休眠，MCCL 使用 Socket。
- 收集 UB 端口原始快照（ubctl 默认 selector=0,1,1，可通过 --ubctl-selector 修改）。端口共享，未验证 flit 到字节换算；ubctl 缺失/失败只记录错误，不伪造带宽。

默认单 case 超时 1200 秒，可用 --case-timeout 指定。每个 case 结束都确认两端 rc=0 才继续，不自动删除旧共享屏障。

若当前 URMA 开机环境也支持 verbs，建议随后在同一开机环境运行原 test/verbs_diag/run.sh（新 job）。这样可比较同内核、同计时框架的两种后端，避免把这轮 URMA 与上一轮不同内核的 verbs 直接归因对照。

## 汇总和 Git 回传

默认结果在 /home/h00918771/metax_dev/urma_diag_results/urma_diag_home_01/node0（202）和 node1（204）。最后应有 URMA_DIAG_ALL_PASSED。每个 case 保留 runtime.json、proxy_starts.jsonl、affinity.jsonl、metrics.jsonl、bench.log、status.json、预检与端口快照。

202：

```bash
export VERBS_DIAG_HOME=/home/h00918771
PY=/home/h00918771/metax_dev/venv-maca-py312/bin/python
JOB=urma_diag_home_01
RANK=0
"$PY" test/verbs_diag/summarize.py "/home/h00918771/metax_dev/urma_diag_results/$JOB/node$RANK"
"$PY" test/verbs_diag/collect_results.py --backend urma --job "$JOB" --node-rank "$RANK"
git add -- "test/bench_results/urma_diag/$JOB/node$RANK"
git commit -m "[test] Add URMA diagnostics $JOB node$RANK"
git pull --rebase https://github.com/LUOQIHuang/uccl.git codex/metax-urma-integration
git push https://github.com/LUOQIHuang/uccl.git HEAD:refs/heads/codex/metax-urma-integration
```

204 改 RANK=1。失败时也收集原始结果，summarize 拒绝失败 case 不影响 collect。私有 .so 和运行库不上传。

## 第二轮：固定配置与 Proxy CPU 采样

`urma_diag_home_01` 两端已完成，分析见 [ANALYSIS.zh-CN.md](../bench_results/urma_diag_home_01/ANALYSIS.zh-CN.md)。目前优先查 Proxy 热点，不扩大全量扫参。

两端先拉取最新提交，保留上面已经通过的 Python、私有模块与库配置。202 设置 `RANK=0`，204 设置 `RANK=1`。先各自执行：

```bash
export URMA_DIAG_HOME=/home/h00918771
export URMA_DIAG_PYTHON=/home/h00918771/metax_dev/venv-maca-py312/bin/python
# 202 使用 0；204 使用 1
RANK=0
bash test/urma_diag/run.sh --node-rank "$RANK" --master-addr 141.61.17.202 \
  --bench-dir "$PWD/ep/bench" --job urma_cpu_check --check-only \
  --suite focused --affinity-mode unpinned --profile-cpu
```

这个预检额外做约 0.3 秒纯 CPU perf 采样，并验证文本报告可生成，不运行 GPU benchmark。需要当前内核可用的 `perf`。如果只是不支持 DWARF/callgraph，两端均加 `--perf-callgraph none` 后重试；后续正式采样也必须带同一选项。缺少 perf 或采样权限时会直接报错，不修改 sysctl 或系统配置。

两端同时启动第一条、等它们均成功后再启动第二条：

```bash
# 对照：3 个固定调优点，不启用 perf
bash test/urma_diag/run.sh --node-rank "$RANK" --master-addr 141.61.17.202 \
  --bench-dir "$PWD/ep/bench" --job urma_focus_01 --repeats 1 \
  --suite focused --affinity-mode unpinned

# 定位：仅在 BF16 Dispatch 调优阶段采样 4 个已识别的原生 Proxy TID
bash test/urma_diag/run.sh --node-rank "$RANK" --master-addr 141.61.17.202 \
  --bench-dir "$PWD/ep/bench" --job urma_cpu_01 --repeats 1 \
  --suite focused --affinity-mode unpinned --profile-cpu
```

focused 保留原 24 组正确性检查，只将调优点收敛为 FP8/BF16 Dispatch 的 RDMA chunk=32、Combine 的 chunk=16，SM=24、NVL chunk=4。Combine=16 为与既有 verbs 配置对齐；本轮 URMA 扫描中 Combine 最快点是 12，focused 不代表 URMA 自动最优参数。该模式也可用于 verbs，但当前优先完成 URMA。

CPU 采样不需要重新编译 GPU 模块。使用软件事件 `cpu-clock`、99 Hz，按新 TID 逐个附着，不采 Python 主线程。若每个 Proxy 对应的新线程不止一个，会拒绝采样。为取得足够样本，仅采样那一轮 BF16 profiler 的每个 period 从 30 次增至 200 次，共 2 个 period；其 FP8/Combine 仍为 30 次。独立 API CUDA-event 计时始终在停止 perf 后执行。`metrics.jsonl` 标出 `cpu_profile` 和 `profiler_num_tests`，不要把受采样/迭代次数影响的数字当作性能优化结果。

优先看 `perf.report.txt` 的函数自身 CPU 占比，结合 `perf.callgraph.txt` 的调用者找提交、完成轮询、WR 管理或 GPU 命令队列读取热点。CPU 占比不是端到端等待时间比例；忙轮询很热只能说明候选位置，仍需后续分阶段计时/单变量对照确认。符号不完整时保留服务器上的 `perf.data` 和所用二进制，下一步再补符号。

回传两个 job；202 `RANK=0`，204 `RANK=1`：

```bash
export VERBS_DIAG_HOME=/home/h00918771
PY=/home/h00918771/metax_dev/venv-maca-py312/bin/python
for JOB in urma_focus_01 urma_cpu_01; do
  "$PY" test/verbs_diag/summarize.py "/home/h00918771/metax_dev/urma_diag_results/$JOB/node$RANK"
  "$PY" test/verbs_diag/collect_results.py --backend urma --job "$JOB" --node-rank "$RANK"
done
git add -- "test/bench_results/urma_diag/urma_focus_01/node$RANK" \
  "test/bench_results/urma_diag/urma_cpu_01/node$RANK"
git commit -m "[test] Add focused URMA and Proxy CPU profiles"
git pull --rebase https://github.com/LUOQIHuang/uccl.git codex/metax-urma-integration
git push https://github.com/LUOQIHuang/uccl.git HEAD:refs/heads/codex/metax-urma-integration
```

收集器回传 perf 的文本报告、stderr 与 status，保留本地 `perf.data`，不上传大型二进制。失败也可直接 collect 回传，不必先让 summarize 成功。

本机无 GPU；只完成语法、配置、源码变换、mock 硬件检查、线程 affinity 和 perf 生命周期测试，实际 URMA benchmark 与 perf 附着需在两台测试机运行。
