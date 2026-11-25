# EID库存复核与下一步源码收集

更新：相关源码已收到，按所提供代码将UMMU 0x13解码为权限表异常。下一步见 [源码复核与一次只读元数据查询](UMMU_PERMISSION_01_ANALYSIS.zh-CN.md)，不再重复本页的源码收集。

本次输入提交 `05b955ab`、`93b94fe8`，数据为 `results/urma_address_inventory_01/node0` 和 `node1`。上传的是查询结果与源码位置，尚不包含内核源码文件。无需重新运行EID查询或benchmark。

## 1. 当前最重要的变化：两端会选到同一首项EID

SDK查询本身两端都返回0。相应SDK头已在运行工具中按benchmark构建manifest核对。两端针对物理设备 `udmac0d1e2`，各得到8项相同EID；第0项均为index 0：

```text
0000:0000:0001:0200:0010:0000:df00:0200
```

生产代码 `ep/src/urma_transport.cpp` 在init中取 `eid_list[0].eid_index` 创建context。因此，按当前查询与代码，两端会使用同一个首项EID。这里是单独查询程序的结果，不冒充本次失败进程导出的完整jetty descriptor。

进一步复核了历史成功双机URMA Normal transport micro：`test/urma_mode_parity/results/urma_notify_05/urma_notify_05_micro_r03/`。两端 `micro/status.json` 均为rc=0，全部checks为true。其原始 `descriptors.json` 显示：

| 记录 | 202 / node0 | 204 / node1 |
| --- | --- | --- |
| 历史成功micro导出的EID | `0000:0000:0001:0200:0010:0000:df00:0200` | `0000:0000:0001:0200:0010:0000:df00:4200` |
| 本次SDK查询首项EID | `0000:0000:0001:0200:0010:0000:df00:0200` | `0000:0000:0001:0200:0010:0000:df00:0200` |
| 历史本节点EID是否出现在当前show --all中 | 是 | 否 |

历史解析保留结构校验：每条descriptor为13464字节，offset 3192的wire-v2头为 `(2,1,8,9,72)`，分别表示wire版本、Normal标志、通道数、jetty数、rjetty结构长度；offset 3212起读取9个rjetty的首16字节EID。每节点4个proxy均一致，双方remote记录与对端local的9条rjetty逐项相符。该固定ABI只用于这组归档数据，不作为任意版本的通用解码规则。

结合上一次失败中“204的UMMU事件dump出现202的主机屏障地址”，**端点身份/EID选择或相关环境配置变化，是当前ACK超时的首要排查方向。** 还需确认该平台EID、UASID和TID的寻址作用域，不能仅凭EID相等就宣布冲突，也不能直接选择另一个index尝试。历史204的EID当前并不存在，盲改为index 1或7缺乏依据。

这定位的是本次无法运行的通信故障。本轮没有新的Dispatch时延数据，不据此改写此前Normal性能回退的归因或优化收益。

## 2. EID_QUERY_FAILED中的脚本误报已修复

原工具请求链接私有目录里的 `liburma.so`，动态加载器按SONAME加载同目录 `liburma.so.0`。这两份文件不是同一路径，旧工具因此拒绝；但两端记录的实际库和请求库SHA256相同：

```text
56d6374553c2843c13eb7f7bc98a5bb65fa2fa4eb5f3b4a331f56cf126fb1f22
```

实际加载的liburma、liburma_common、libummu与udma provider均在指定私有目录下，且这四项在两端各自哈希一致。当前没有“这四个私有库在两端不同”的证据。

已修复为：允许同一目录、内容SHA256完全相同的SONAME副本；目录外相同内容、同目录不同内容仍拒绝。原查询返回值、EID表和库哈希都保留，不覆盖原始 `error`。离线复核结果见 `results/urma_address_inventory_01/analysis/summary.json`。**不用为了这个误报再占用机器重跑查询。**

## 3. 找到的源码位置

- 202：`/home/g00953922/kernel_f00955680`，本次记录称目录可读，build/source链接都指向这里。
- 204：没有有效build/source链接。其 `/usr/src/debug/kernel-*` 是其他版本候选，不能直接当作运行私有内核的准确源码。
- 两端已安装UDMA来自 `updates/urma/hw/udma/udma.ko`。源码目录指针本身不能证明这份更新模块由该目录编译，收集器会一并记录模块信息和二进制哈希，用于核对来源。

接下来只在202收集已找到的相关代码，不再遍历他人整个metax_dev目录。

## 4. 202执行一次，不需要204配合

拉取本次更新后执行。无需构建GPU扩展、加载模块或运行benchmark。输出目录必须新建，脚本不会覆盖已有结果。

```bash
cd /home/h00918771/uccl-ep-xpu-integration-new
git pull --ff-only https://github.com/LUOQIHuang/uccl.git codex/metax-urma-integration
PY=/home/h00918771/metax_dev/venv-maca-py312/bin/python
OUT="$PWD/test/reference_events/results/urma_kernel_sources_01/node0"

"$PY" test/reference_events/collect_kernel_sources.py \
  --source-root /home/g00953922/kernel_f00955680 \
  --extra-root libummu=/usr/src/debug/libummu-1.0.4-1.oe2403sp3.aarch64 \
  --output "$OUT"
```

成功标志：`KERNEL_SOURCE_COLLECTION_PASSED`。若输出PARTIAL或权限错误，保留已有目录和错误，不重跑benchmark，也不修改目录权限或模块状态。单个额外源码目录缺失会记录为missing，不阻断UMMU和UDMA主体收集。

收集范围为以下目录中的源码、头文件、Kconfig/Kbuild/Makefile：

| 范围 | 要回答的问题 |
| --- | --- |
| `drivers/iommu/hisilicon/` | 0x13事件含义、raw word位域，地址及访问映射路径 |
| `drivers/ub/urma/hw/udma/` | segment导入、TID、WQE及目标端点选择 |
| `drivers/ub/urma/ubcore/`、`uburma/`及URMA头文件 | EID/context/UASID与用户接口的关联 |
| 指定的libummu调试源码 | 用户态授权、MATT/MAPT及事件处理的对应实现 |
| 版本文件、modinfo与已装模块哈希 | 检查源码与部署版本的关系，避免用错版本解码 |

只读复制这些源文件；不复制.o/.ko等二进制，不跟随源文件符号链接，不构建或执行复制的代码。总量上限128 MiB、10000个文件，超限停止并保留已收集部分。

需要提供完整 `test/reference_events/results/urma_kernel_sources_01/`，包括 `sources/` 和 `kernel_source_manifest.json`。此项可以在没有GPU测试窗口时完成。

## 5. 收到源码后的判定顺序

1. 解码本次UMMU 0x13事件，核对其中地址和TID/上下文字段；不套用另一内核版本的定义。
2. 沿EID/context/UASID/TID映射，判断两端相同首项EID是否会使该屏障请求访问错误地址空间，还是该设备机制允许共享该EID。
3. 明确部署UDMA与源码树是否匹配；若不匹配，索取对应更新模块源码，避免修改不相干版本。
4. 只有确定了合法端点配置或代码修正，才安排一次有限的连通验证；成功后恢复原Normal大区间对比。

本阶段不扫EID index、不跳过barrier、不调整fence或重新做性能参数扫描。
