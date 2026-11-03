# verbs Normal 定位测试（可迁移用户目录）

当前新实现的对照请使用 [Normal/LL v2 构建与验收](../urma_mode_parity/README.md)，可用同一新模块分别选择 verbs/URMA。本页保留历史模块与匹配旧 benchmark 源码的诊断流程。

所有用户路径默认从 $HOME 推导，也可设置 VERBS_DIAG_HOME。以 h00918771 为例，从 Git 仓库直接运行 /home/h00918771/uccl-ep-xpu-integration/test/verbs_diag/ 下的脚本即可。

## 按原结构复制的数据

```text
/home/h00918771/
  uccl-ep-xpu-integration/ep/bench/        # benchmark 及依赖源码，保持内容不变
  metax_dev/
    # 测试脚本位于上方仓库的 test/verbs_diag/
    urma_after_kernel_20261029/diag/
      ep_host_atomic_from_204_4ed19a6b/module/ep.abi3.so  # 202/rank 0
      ep_host_atomic_4ed19a6b/module/ep.abi3.so           # 204/rank 1
      umdk_private_618957c3/lib/          # 扩展依赖的私有库
    native-libs-3.7.0.38-G1NK1l/          # 原环境若依赖该目录，也复制
```

两端扩展 SHA256 为 144f1fca126eb8971a5278c3baf0b44e1a29fb7b03ce51c928feb7abc19af798。两端二进制相同，也可以把同一份 .so 放在任意本人目录，用 --module 指定。

复制时注意软链接可能仍指向原用户目录；需要独立副本时对选中的文件/目录使用 cp -aL，复制链接的目标内容。不要修改原用户文件或批量替换二进制内的路径。

## Python 环境与库

虚拟环境不能当普通数据目录直接迁移。复制的 activate、pip shebang、pyvenv.cfg、Python 链接和部分包可能保留旧路径。优先使用本人已有的 MetaX Python，或在本人目录重新创建并安装匹配的 MetaX torch 和依赖。只改 activate 中的用户名不足以验证环境已迁移。

新版 run.sh 不会自动 source 复制来的 activate-uccl.sh、env.sh 或 native-libs/activate.sh，避免这些脚本把路径指回旧用户目录。需要时通过 VERBS_DIAG_ACTIVATE 显式指定已检查过的激活脚本。

run.sh 使用 /opt/maca-3.7.0（MACA_PATH 可覆盖），并添加自己 metax_dev 下的私有 UMDK lib、native-libs 根目录/lib/lib64。其他目录通过 VERBS_DIAG_EXTRA_LIBRARY_DIRS（冒号分隔）指定。VERBS_DIAG_LIBRARY_DIR 可覆盖私有 UMDK lib 位置。

Python 优先级：VERBS_DIAG_PYTHON > 自己 metax_dev/venv-maca-py312/bin/python（若存在）> 当前 PATH 的 python3。推荐显式选择本人可用的 MetaX Python：

```bash
export VERBS_DIAG_HOME=/home/h00918771
# 以下 Python 路径仅为示例，需实际能导入沐曦 torch。
export VERBS_DIAG_PYTHON=/home/h00918771/metax_dev/venv-maca-py312/bin/python
```

VERBS_DIAG_DEV_ROOT 可覆盖 metax_dev 的位置，默认 $VERBS_DIAG_HOME/metax_dev。系统 /opt/maca 路径不随用户名变化。

## 先做路径检查

202：

```bash
bash /home/h00918771/uccl-ep-xpu-integration/test/verbs_diag/run.sh \
  --node-rank 0 --master-addr 141.61.17.202 \
  --job verbs_home_check --check-only
```

204：同一命令改 --node-rank 1。

check-only 不联系另一端、不创建结果目录、不运行 benchmark。它校验源码/扩展哈希，导入 torch 和扩展，打印真实 Python/torch/扩展路径、home 下已加载的动态库以及 ldd 输出。导入会加载运行库，但不启动 benchmark 或通信测试。

查看 PYTHON_REAL、TORCH_PATH、EXTENSION_IMPORT_OK、LOADED_HOME_LIBRARIES 是否还指向原用户目录；检查软链接、虚拟环境、额外库目录和 RPATH。独立 ldd 可能看不到由 torch 预加载的库，因此最终以扩展实际导入是否成功作为加载检查。此检查不证明运行时 ABI、DMA-BUF 或 GPU 操作已通过。

## 双端测试

两端保持同一个新 job，端口 31080/31081 空闲，在 180 秒内启动。202：

```bash
bash /home/h00918771/uccl-ep-xpu-integration/test/verbs_diag/run.sh \
  --node-rank 0 --master-addr 141.61.17.202 \
  --job verbs_diag_home_01 --repeats 3
```

204：

```bash
bash /home/h00918771/uccl-ep-xpu-integration/test/verbs_diag/run.sh \
  --node-rank 1 --master-addr 141.61.17.202 \
  --job verbs_diag_home_01 --repeats 3
```

源码若不在默认位置，添加 --bench-dir /你的路径/ep/bench；扩展添加 --module /你的路径/ep.abi3.so。还支持 --module-sha、--hca、--ifname、--gpu、--port、--output。默认 HCA=rocep40s0f0，Socket=enp33s0，GPU=0。严格校验已上传的 benchmark/buffer/utils 哈希，无需为迁移目录修改这些源文件内容。

测试不要求 URMA 设备；所用 .so 若链接 liburma，仍需可加载的运行库。预检要求 mx-smi 显示 GPU 空闲、fuser 确认 GPU/HCA 无打开者，且无同 UID 的旧 Normal barrier。不自动清理任何已有进程/共享屏障。

## 测量与回传

每轮交替默认绑核/关闭绑核，第二轮反转顺序；3 轮共 6 次 benchmark。每 rank 4096 tokens、hidden=7168、top-k=8、experts=256、SM=24，沿用 seed=0 的原始路由。

每次保留 24 组正确性检查，仅在结果目录生成 benchmark 副本，将 NVL chunk 固定为 4。Dispatch RDMA chunk=4/8/12/16/20/24/28/32；Combine=12/16/20/24/28/32，共 22 点。

保留 profiler transmit/notify，额外使用 CUDA event 测量完整 callable（5 次预热，15 次计时，丢弃首个样本）。API-event 与 profiler 属于不同批次，不能相加；不代表完整模型推理时延。新增测量用于包内 A/B，不能当成与历史 Best 完全同条件。

在计时区域外读取线程 affinity、当前 CPU、上下文切换、schedstat；不是连续调度追踪。关闭 Proxy 绑核仍可能继承父进程/MCCL/cgroup 限制。记录端口前后计数及拓扑，任意一端失败即停止。作者本机没有可用 GPU，仅完成语法及 CPU 协调/汇总逻辑检查。

默认结果在 /home/h00918771/metax_dev/verbs_diag_results/verbs_diag_home_01/node0/，另一端为 node1/。可在各机汇总：

```bash
"${VERBS_DIAG_PYTHON:-python3}" /home/h00918771/uccl-ep-xpu-integration/test/verbs_diag/summarize.py \
  /home/h00918771/metax_dev/verbs_diag_results/verbs_diag_home_01
```

两台分别打包并回传：

```bash
tar -czf "$HOME/verbs_diag_home_01_$(hostname -s).tar.gz" \
  -C /home/h00918771/metax_dev/verbs_diag_results verbs_diag_home_01
```

失败也保留输出，查看 preflight_error.txt、setup.json、bench.log 和 status.json。

## 通过 Git 同步脚本和回传结果

两台机器各自使用独立 checkout，均在 `codex/metax-urma-integration` 分支。新 checkout 可执行（目标目录需尚不存在）：

```bash
git clone --branch codex/metax-urma-integration \
  https://github.com/LUOQIHuang/uccl.git \
  /home/h00918771/uccl-ep-xpu-integration
```

已有 checkout 则在开跑前同步并检查提交号：

```bash
cd /home/h00918771/uccl-ep-xpu-integration
git pull --rebase https://github.com/LUOQIHuang/uccl.git codex/metax-urma-integration
git rev-parse HEAD
```

两端脚本应来自同一提交。运行期间不要更新该 checkout 的脚本。`driver.py` 会比较两端脚本内容，拒绝版本不一致的测试。

运行结束后，202 先汇总本节点，再收集适合提交的小文件：

```bash
cd /home/h00918771/uccl-ep-xpu-integration
"${VERBS_DIAG_PYTHON:-python3}" test/verbs_diag/summarize.py \
  /home/h00918771/metax_dev/verbs_diag_results/verbs_diag_home_01/node0
"${VERBS_DIAG_PYTHON:-python3}" test/verbs_diag/collect_results.py \
  --job verbs_diag_home_01 --node-rank 0
git add -- test/bench_results/verbs_diag/verbs_diag_home_01/node0
git diff --cached --stat
git commit -m "[test] Add verbs diagnostics verbs_diag_home_01 node0"
git pull --rebase https://github.com/LUOQIHuang/uccl.git codex/metax-urma-integration
git push https://github.com/LUOQIHuang/uccl.git HEAD:refs/heads/codex/metax-urma-integration
```

204 执行相同流程，把命令中的 node0 改为 node1，--node-rank 0 改为 1。两端提交不同子目录，避免同名 bench.log 相互覆盖。其他 job 同时替换所有 job 名。collect_results 支持 --source 指定实际本节点结果目录；失败结果也可收集，即使 summarize 拒绝失败 case。

`collect_results.py` 只复制白名单中的日志、状态、metrics/affinity、拓扑和汇总文件；不会提交私有 .so、虚拟环境、缓存或其他文件，不自动执行 commit/push。相同内容可重复收集；同一个 job 的已有文件内容不同则拒绝覆盖，应使用新 job。

目录约定：

```text
test/verbs_diag/                                      # 共享脚本
test/bench_results/verbs_diag/verbs_diag_home_01/node0/  # 202 结果
test/bench_results/verbs_diag/verbs_diag_home_01/node1/  # 204 结果
```

根 .gitignore 对此结果目录放行 log/json/jsonl/csv，仍忽略二进制。无需 git add -f。若 push 提示远端领先，再 pull --rebase 后重试普通 push，不使用 force push。
