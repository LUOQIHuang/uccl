# 下一步：只读取现有编译产物，不再运行打桩benchmark

## 目的

roles-only关闭采集后，FP8 Dispatch仍慢约54.8%，BF16慢约29.1%，尚未通过5%门槛。Combine差异已回到约−0.5%。正确性通过不表示性能代表性通过，暂不启用roles采集，也不再减少几个测点后重复整套GPU实验。

本轮直接读取现有base/roles模块与objects，收集ELF代码段大小、符号、notes/metadata及编译器信息，检查能否找到资源或代码体积差异。**无需GPU测试窗口，无需切内核、重建模块或重新跑4096 benchmark。**脚本不导入torch，不调用GPU运行时；只运行编译器的version/help查询和ELF读取工具，不执行编译。

ELF读取不保证MACA所有资源字段都能被通用工具解码；缺少寄存器/spill字段表示未获得证据，不能当成0。现有build.log确认了O3及roles-only宏，但没有给出足以确认spill的资源报告。

## 两端各执行一次

保留刚测试完的两份构建及objects目录。更新脚本后，两端执行以下命令；202的RANK=0，204改为1：

```bash
cd /home/h00918771/uccl-ep-xpu-integration-new
set -euo pipefail
RANK=0  # 204改为1
PY=/home/h00918771/metax_dev/venv-maca-py312/bin/python
BASE=/home/h00918771/metax_dev/dispatch_diag_build/urma_base_roles_v1
TRACE=/home/h00918771/metax_dev/dispatch_diag_build/urma_roles_v1
DEST="$PWD/test/dispatch_diag/results/urma_dispatch_codegen_01/node${RANK}"

"$PY" test/dispatch_diag/collect_codegen.py \
  --base-build "$BASE" --trace-build "$TRACE" \
  --maca-path /opt/maca-3.9.0 --output "$DEST"
```

两端不需要同时启动，也不需要master地址。Python仅需标准库，可用其他Python3执行；上面路径沿用已验证解释器。脚本核对base/roles源码、SDK、除打桩开关外的编译参数、模块SHA，避免错配。

成功标志 `DISPATCH_CODEGEN_COLLECTED` 表示资料收集结束，不表示已获得资源字段或找到根因。ELF子命令的退出码和截断信息均记录在codegen.json中；工具不能解码也保留输出，不触发GPU重试。单个工具最多运行45秒，单份文本最多保留2MiB，每个产物最多检查8个ELF候选，默认只临时提取不超过128MiB的单幅镜像。临时二进制不会保留在结果中。

输出目录必须不存在，防止覆盖。重跑时两端将目录尾号一起改成02。若objects已删除，脚本仍收集最终模块并明确记录missing；不要为了补objects立刻重新编译。若开始即报模块SHA或源码不匹配，保留终端错误及现有manifest，不修改manifest绕过检查。

## 需要提供的数据

```text
test/dispatch_diag/results/urma_dispatch_codegen_01/node0/
test/dispatch_diag/results/urma_dispatch_codegen_01/node1/
```

主要包括codegen.json、base/roles下的构建日志和manifest、ELF header/sections/symbols/notes文本、可读取的metadata十六进制、编译器版本/help。无需提供.so、.o、临时ELF，也不需要重传本轮benchmark目录。

拿到资料后，先检查仍含6个范围的Dispatch是否出现可见资源/代码形态差异。若资料不足，将明确报告限制；不能把本轮剩余29%～55%的测量代价当作原始URMA/verbs差额。下一次GPU实验应围绕已有证据做单变量验证，不继续使用当前不具代表性的内部时间归因。
