# 在当前 URMA 内核上验证原生 verbs

目前没有证据要求为了运行 verbs 切换内核。旧 scripts 的 `--expected-kernel` 将发行版内核名设为预检条件。纯 verbs driver 的该参数只是对 `uname -r` 的校验。两端归档里，diag 内核的 `ib_uverbs`、`mlx5_core` 和 `mlx5_ib` 均可在磁盘上解析。现有归档没有找到在 diag 内核下完成纯 verbs 的 GPU 数据路径记录。 [内核诊断](../kernel_diag/kernel_env_01_analysis.zh-CN.md)

此次在 `6.6.0-gdr_f00955680_diag` 完成一次两端 128 tokens/rank 的 Normal 功能验证，固定 GPU0、`rocep40s0f0` port 1、RoCE v2 GID 3。控制进程走 141.61.17.202，verbs 数据路径选择上述端口和GID。验收包含加载纯 verbs 扩展、真实GPU DMA-BUF注册、BF16 WRITE和原生 FADD 成功完成以及原Normal正确性检查。通过后可以优先安排同内核的正式RDMA对照。此次小样本只用于功能验收。

## 两端执行步骤

两端进入仓库根目录。202设 `RANK=0`，204设 `RANK=1`。保留现有URMA内核。Python默认使用 `/home/h00918771/metax_dev/venv-maca-py312/bin/python`，MACA默认 `/opt/maca-3.9.0`，控制网卡沿用最近成功的202 `enp40s0` 和204 `enp34s0f5u1u4`。如果实际控制接口不同，可各端通过 `--ifname` 指定。RDMA GID若变化，两端核对实际业务网地址后传同一 `--gid-index`。

1. **先看本地端口和GID。** 两端可独立执行，不启动GPU或对端任务。失败时检查输出中的端口名称、PCI驱动、RoCE类型和GID。

   ```bash
   bash test/verbs_baseline/run_on_diag.sh --action inventory --node-rank "$RANK"
   ```

2. **选择纯 verbs 构建。** 默认是 `/home/h00918771/metax_dev/verbs_same_kernel_build/current_v1`。已有带reference events的纯 verbs 构建可通过 `--build-dir` 指定，以下每一步都使用同一目录。已有构建的模块SHA、源码和标志由驱动预检校验。若没有匹配构建，在两端各执行一次 `build`。构建可以在双机机时前独立执行。

   ```bash
   bash test/verbs_baseline/run_on_diag.sh --action build --node-rank "$RANK"
   bash test/verbs_baseline/run_on_diag.sh --action check --node-rank "$RANK"
   ```

   若复用已有构建，直接执行 `check`。两端均应出现 `BLOCKERS_GPU_READY` 和 `VERBS_CHECK_PASSED`。前者表示GPU小张量可用，后者表示扩展和设备预检通过。

3. **跑一次双机 smoke。** 先后启动两端，使用相同JOB和port，默认 `verbs_same_kernel_01`、34080。测试驱动运行Normal正确性检查并采3次短记录。脚本读取BF16事件中的实际WRITE opcode和原生FETCH_AND_ADD opcode，并按WR ID检查对应成功完成记录。

   ```bash
   bash test/verbs_baseline/run_on_diag.sh --action smoke --node-rank "$RANK"
   ```

   两端都出现 `VERBS_ON_DIAG_SMOKE_PASSED`，就可确认这套机器、当前驱动和该端口在 diag 内核下跑通了纯 verbs Normal。若失败，按最早失败的位置处理。端口缺失、扩展导入失败、GPU DMA-BUF注册失败、连接失败或原子失败对应不同依赖。保留日志再判断是否有必要切核。

## 回传记录

脚本自动归档到 `test/reference_events/results/verbs_same_kernel_01/`。交回两端的 `console`、`setup/runtime/module_manifest`、`rdma_port.json`、`r01_baseline/normal/status.json`、`bench.log` 和 `event_trace.jsonl`。没到双机步骤时，提交已生成的 `inventory.log`、`gpu_check.log` 或 `check.log` 即可。重验双机使用新的 `--job`，可继续复用同一已通过构建。
