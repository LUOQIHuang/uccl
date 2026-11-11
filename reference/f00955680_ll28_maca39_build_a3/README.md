# f00955680 ll28_maca39_build_a3 EP Stage Trace Reference

Complete copy of `/home/f00955680/metax_dev/stage_latency_20261108/ll28_maca39_build_a3/`
(excluding log files and symlinks) for code review.

## 目录结构

```
reference/f00955680_ll28_maca39_build_a3/
├── urma_header_package.txt      URMA header package info
├── urma_header_sha.txt          URMA header SHA256
├── tree/                        源代码树 (1.5M, 95 files)
│   ├── run.sh                   运行脚本
│   ├── run_eager.sh
│   ├── test.py
│   ├── test_eager.py
│   ├── ep/                      EP 源码
│   │   ├── setup.py             构建脚本
│   │   ├── setup.pre_link_fix.py
│   │   ├── src/
│   │   │   ├── internode_ll.cu        LL Dispatch / Combine GPU 阶段打点
│   │   │   ├── internode.cu           Normal Dispatch / Combine GPU 阶段打点
│   │   │   ├── proxy.cpp              CPU Proxy 轮询/解析/提交/完成事件
│   │   │   ├── rdma.cpp               Verbs/RDMA 传输与打点
│   │   │   ├── urma_transport.cpp     URMA 传输与打点
│   │   │   ├── internode_ll.cu.orig   LL 修改前原始版本
│   │   │   └── ...                    其他 EP 源文件
│   │   └── include/
│   │       ├── gpu_stage_trace.cuh    GPU 打点记录结构
│   │       ├── cpu_stage_trace.hpp    CPU 打点记录和时间戳
│   │       ├── urma_transport.hpp.orig
│   │       └── ...                    其他 EP 头文件
│   ├── include/cc/              拥塞控制头文件
│   ├── include/util/            工具头文件
│   └── lib/                     (空, 原为符号链接)
├── build/                       编译产物 (58M)
│   ├── build.ninja              Ninja 构建文件
│   └── src/*.o                  编译后的 object 文件
├── module/                      最终模块 (28M)
│   └── ep.abi3.so               编译后的 EP 共享库
├── input/                       输入配置
│   └── patch_order.py
└── urma_include/                URMA SDK 头文件 (224K, 7 files)
    ├── urma_api.h
    ├── urma_cmd.h
    ├── urma_opcode.h
    ├── urma_perf.h
    ├── urma_provider.h
    ├── urma_types.h
    └── urma_types_str.h
```

## 排除的文件

- `build.log` — 构建日志
- `source_patch.log` — 源码补丁日志
- `.ninja_log` / `.ninja_deps` — Ninja 构建缓存日志
- `._*` — macOS 资源叉文件
- 符号链接 (`crtbeginS.o`, `crtendS.o`, `libstdc++.so`)

## 来源

- 原始路径: `/home/f00955680/metax_dev/stage_latency_20261108/ll28_maca39_build_a3/`
- 构建树: `ll28_maca39_build_a3` (MACA 3.9.0)
- 日期: 2026-11-04 ~ 2026-11-09
- 复制: 2026-11-11
- 测试机: 141.61.17.202 / 141.61.17.204 (root)
