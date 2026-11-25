# 指定设备组合：202 c1、204 c0连通检查

更新：`urma_c1_c0_01`两端已通过。下一步按 [本次结果与4096阶段定位步骤](DEVICE_PAIR_C1_C0_RESULT_AND_NEXT.zh-CN.md) 运行trace门禁和coverage，不再重复connect。

按用户指定：202使用 `udmac1d1e2`，204使用 `udmac0d1e2`。只改变URMA设备选择，沿用上次128-token paced连通检查及fence计划：202全开，204仅数据tail-off；内部屏障fence不变。目的仅是验证首次屏障FADD能否完成、Normal正确性是否通过，不用于计算正式性能收益。

本地无法访问服务器GPU，以下是待服务器验证的测试。无需重新编译GPU扩展；生产源码与算子未变，原模块仍须通过源码和模块哈希检查。两端必须拉取同一个本次提交，不能只设置环境变量而继续使用旧入口。

## 1. 两端拉取

在原仓库执行，不在之前带手工修改的旧worktree里运行：

```bash
cd /home/h00918771/uccl-ep-xpu-integration-new
git pull --ff-only https://github.com/LUOQIHuang/uccl.git codex/metax-urma-integration
git rev-parse HEAD
```

脚本默认使用以下已知环境，可通过已有同名环境变量覆盖：

- Python：`/home/h00918771/metax_dev/venv-maca-py312/bin/python`。
- MACA：`/opt/maca-3.9.0`；私有库沿用 `umdk_private_618957c3/lib`。
- 构建：`/home/h00918771/metax_dev/normal_scope_build/urma_v1`。
- GPU PCI：`0000:21:00.0`，Atomic父桥auto只读检查。
- OOB接口：202为 `enp40s0`，204为 `enp34s0f5u1u4`，端口33480/33481。

如实际构建目录不同，在下方命令追加 `--build-dir /实际构建目录`，不跳过模块检查。接口若变化，可先 `export URMA_DIAG_IFNAME=实际接口`。

## 2. 只执行这一项

202先启动：

```bash
bash test/reference_events/try_device_pair.sh --node-rank 0
```

204尽量在60秒内启动：

```bash
bash test/reference_events/try_device_pair.sh --node-rank 1
```

两端默认JOB都是 `urma_c1_c0_01`。结果存在时换相同新JOB，例如都追加 `--job urma_c1_c0_02`；不要覆盖旧结果或循环重跑。

入口会明确打印所选设备。源码中两端设备名称的比较已改为**显式固定计划**：该计划仅允许node0=c1、node1=c0，错误选项直接拒绝；同一计划下继续严格核对工作负载、源码、库和其他实验配置。原默认同设备计划仍保留设备相等检查。设备名在每端setup里完整保留，并用于实际环境、硬件检查、前后URMA状态采集和运行日志校验。

每端最多300秒case运行时间，另有原有的配对等待和只读状态采集时间。出现错误完成或Python异常会停止本次任务的进程及子进程，自动保留失败结果。不会修改EID、PCI配置或驱动，也不启用活动打点。

## 3. 怎样判断

两端都应得到：

```text
CONNECTION_CONTROL_RESULT run_rc=0 snapshot_rc=0 collect_rc=0
```

- 两端通过：支持“设备选择与此前无法通信有关”，再核对实际设备/EID、正确性和权限事件；不直接宣布已解决历史性能回退。
- 仍为同一内部FADD ACK超时：保留当前组合结果，不扫描更多设备或EID，不继续4096。
- 初始化/注册/配对失败：保留第一条错误。这表示当前组合尚未进入同一屏障验证，不能与之前FADD错误混为一谈。

即使通过，本轮也先停止；正式Normal性能比较在确认设备组合和运行环境后另行固定口径。

## 4. 需要提供的文件

提供整个 `test/reference_events/results/urma_device_pair_01/` 两端目录。自动包含bench/status、setup/preflight、runtime、前后connection/journal/内核日志和构建清单，不需要上传.so、objects或重新收集源码。

原始运行目录默认在 `/home/h00918771/metax_dev/normal_scope_results/urma_c1_c0_01/`，自动收集目的地为上述仓库结果目录。无需另外组织传输步骤。
