# 本地验证记录

日期：2026-11-02。生产代码基线：`fe0959a2dc3d56ba0db4a6517c3e3dbde261a753`。
新增内容仅在 `urma_validation/baseline/`，未修改 CPU Proxy 或生产 URMA 实现。

已完成（WSL Linux）：

- `bash -n run.sh`：通过。
- `python3 -m py_compile run.py`：通过。
- `python3 -m unittest -v test_runner.py`：4项通过。覆盖真实 TCP 双进程调度/就绪/日志/指标、错误标记不能被 ALL_OK 掩盖、非法参数拒绝、进程超时清理、缺失可执行程序及失败报告归档。
- `cc -O3 -g -std=gnu11 -Wall -Wextra verbs_bench.c -o /tmp/uccl-baseline-verbs -libverbs`：编译通过。
- URMA 编译：当前 WSL 无 `urma_api.h` / 对应 SDK，失败已捕获。
- 一键 micro 最小配置实际执行：缺少可用 verbs 设备，测试失败；REPORT.md、results.json、双方日志和 tar.gz 均生成。该执行仅验证失败诊断链路，未产生有效性能结果。

未完成：目标设备 URMA SDK 编译、真实 verbs/URMA 跨机操作、GPU EP 构建和运行、MetaX 路径、性能数据。请按 README 在目标设备运行；不能将上述控制脚本测试视为这些硬件路径已经通过。

源码包不包含 `.git`、专有 SDK、Python 环境或未初始化的第三方 submodule 内容。需要额外依赖时，按项目说明准备；报告中的 SDK 编译错误应连同两端 tar.gz 返回。独立 micro 包无需完整仓库即可使用。
