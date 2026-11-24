# 第二次连通检查：UMMU事件与远端屏障地址对应

输入提交：`7f1dd9d3`。原始数据：`results/normal_scope_20261124_recovery/normal_scope_urma_02_connect/`。复核结果保存在同级 `normal_scope_urma_02_analysis/`，不修改上传的原始诊断文件。

## 1. 这次得到的结论

关闭GPU/CPU活动采集后，仍在首次BF16正确性prepare失败：204的内部原子请求返回 `status=9`，202随后CPU等待超时。故障不要求开启本轮活动采集才会出现；本轮依然没有Dispatch性能数据。

| 证据 | 202 / node0 | 204 / node1 |
| --- | --- | --- |
| 本轮配置 | paced，128 tokens，boundaries，FADD全开 | 同工作负载，仅数据tail-off |
| 正确GPU PCI / 实际父桥 | `21:00.0` / `20:00.0`，控制值0060 | 相同 |
| 实际管理网接口 | `enp40s0` | `enp34s0f5u1u4` |
| 源码、诊断、模块检查 | 通过 | 通过；两端源码/诊断指纹一致 |
| 仅有的layout均值 | 133.504 μs，2个保留样本 | 132.992 μs，2个保留样本 |
| 首次跨节点Dispatch | `timeout (dispatch CPU)` | 内部FADD：status=9、token=65536、WR=UINT64_MAX |

GPU父桥与网卡参数的旧问题已经修正，但ACK故障仍在。两端的六个已采集模块srcversion一致；这不能证明驱动正确，但目前没有“两端这些模块版本不一致”的证据。

## 2. 新增的具体线索

204本次新增内核日志中出现UMMU事件，其dump地址与202的主机屏障注册地址完全相等：

| 文件与位置 | 原始内容 | 含义 |
| --- | --- | --- |
| 202 `node0/normal/bench.log` 的proxy 0主机注册 | `kind=host va=0xfffb90bc7000 len=64 atomic=1` | 202的64字节主机原子区；结合构造代码，对应barrier_words |
| 204 `node1/normal/kernel_after.txt:392` | 服务器日志时间03:25:12，`ummu ummu.0: event 0x13 received:` | 204在本次运行期间记录了UMMU事件 |
| 同一文件第396行 | `0x0000fffb90bc7000` | dump第4个原始word，数值等于202上述地址 |
| 204自己的proxy 0主机原子区 | `0xfffc25e9d000` | 与事件中的地址不同 |

```text
204首次Dispatch prepare
  └─ 内部屏障FADD，目标是202的barrier_words
       └─ 204的UMMU事件dump出现202的屏障地址 0xfffb90bc7000
            └─ 同次运行：204报告ACK超时，202无法越过准备屏障
```

上图展示同次运行的对应关系，不表示已证明“UMMU事件必然导致ACK超时”，也不是精确的跨机器时间排序。不要将两端墙钟相减计算延迟。

**当前最值得检查的是URMA端点/EID选择、远端segment导入、TID及地址访问映射。** 需要相应内核/私有provider实现解释为什么这个地址出现在204的UMMU事件中。可能涉及寻址、导入映射、授权或设备状态；现有证据不能区分。这里没有把0x13擅自解码为某种确定权限/页表错误，也没有将raw word的位域当成已经确认的TID。

202在03:26:54还有TP level AE。其时间与失败后的退出清理相近，不能据此说TP事件先触发了此次失败。更早的RAS事件同样不能直接作为本次根因。

## 3. 修正“dmesg没有新增错误”的判断

原connection JSON明确记录两端 `dmesg` 返回码1、`Operation not permitted`。前后错误文本相同，被旧诊断器误判为“可精确比较的空增量”。因此，上传提交说明里的“dmesg_delta为空，无新增内核错误”不成立。

本次复核使用benchmark另存的journal尾部：

| 节点 | before尾部与after头部连续重叠行数 | after新增行数 | 新证据 |
| --- | --- | --- | --- |
| 202 | 261 | 139 | 本轮注册、退出清理与TP事件 |
| 204 | 352 | 48 | 本轮注册及UMMU 0x13事件 |

connection前后boot_id在各自节点内部相同。按连续重叠边界截取新增部分，保留顺序和重复行；不是简单集合相减。该方法只适用于能对齐的这些尾部，不能保证完整保留了所有被限流或未记录的设备事件。

已修复诊断器：失败读取不参与差分，改用可用journal；无重叠、切换boot、权限失败均标明局限。新输出为 `kernel_delta.txt` 与结构化 `kernel_log`，不会把journal内容伪装成dmesg。也修正了204 status里出现URMA错误但 `no_errors=true` 的检查遗漏。

## 4. 下一步只查询环境，不再重复跑benchmark

目的有两个：取得应用实际选择的物理设备EID；找到运行内核的UMMU/UDMA源码。**这一步不需要两端同时启动，也不需要重编GPU扩展，不创建URMA context/segment/jetty，不提交WRITE/FADD。** EID工具只编译并运行一个小型CPU查询程序，使用当前benchmark的SDK头和私有库；会核对SDK哈希，并记录实际加载库路径/哈希。

默认 `urma_admin show` 在本次两端都只列出 `bonding_dev_0` 和同一个EID。应用使用的是 `udmac0d1e2`，不能把该默认输出直接当成应用的EID，更不能直接宣布EID冲突。新库存查询增加指定物理设备、全设备输出和ubcore sysfs；原生SDK查询保留EID列表顺序，记录第0项，因为生产代码当前取 `eid_list[0]`。

两端各执行一次，202设RANK=0，204设RANK=1。用尚未使用的结果目录；这些查询可以独立进行，不占用GPU做benchmark。

```bash
cd /home/h00918771/uccl-ep-xpu-integration-new
git pull --ff-only https://github.com/LUOQIHuang/uccl.git codex/metax-urma-integration
REPO="$PWD"
RANK=0                         # 204改为1
PY=/home/h00918771/metax_dev/venv-maca-py312/bin/python
BUILD=/home/h00918771/metax_dev/normal_scope_build/urma_v1
INCLUDE=/home/h00918771/metax_dev/urma_sdk_private_include
LIB=/home/h00918771/metax_dev/urma_after_kernel_20261029/diag/umdk_private_618957c3/lib
OUT="$REPO/test/reference_events/results/urma_address_inventory_01/node$RANK"
if [ "$RANK" = 0 ]; then IFACE=enp40s0; else IFACE=enp34s0f5u1u4; fi

test ! -e "$OUT" || { echo '结果目录已存在，请换目录名'; exit 1; }
mkdir -p "$OUT"
"$PY" test/reference_events/connection_diag.py \
  --output "$OUT/connection" --phase before \
  --device udmac0d1e2 --ifname "$IFACE" \
  --gpu-pci 0000:21:00.0 --atomic-port auto

"$PY" test/reference_events/eid_inventory.py \
  --output "$OUT/eids" --device udmac0d1e2 \
  --include-dir "$INCLUDE" --lib-dir "$LIB" \
  --build-manifest "$BUILD/manifest.json"
```

预期原生查询输出 `EID_QUERY_PASSED`。若失败，保留 `eid_inventory.json` 和compile/query日志即可，不循环重试，不跳过SDK或实际库路径检查。系统工具缺少权限或选项不兼容会原样保留在connection JSON，不能当作成功。

结果解释：如果物理设备上应用选中的EID在两端相同，优先请环境负责人核对跨节点寻址和EID配置；这仍需结合设备机制确认。如果不同，则继续查segment导入和UMMU地址授权/翻译路径。两种情况下都不直接更改EID、重载模块或跳过屏障。

## 5. 如何寻找当前内核源码

先在任一台执行，保存输出。源码链接可能不存在；模块路径只用于核对，`.ko`不是源码。

```bash
{
  uname -r
  ls -ld /lib/modules/"$(uname -r)"/build /lib/modules/"$(uname -r)"/source
  readlink -f /lib/modules/"$(uname -r)"/build
  readlink -f /lib/modules/"$(uname -r)"/source
  modinfo -n udma
  modinfo -n ummu
} > "$OUT/kernel_source_locations.txt" 2>&1
cat "$OUT/kernel_source_locations.txt"
```

若build/source链接指向真实目录，请提供该绝对路径。若链接不存在，再做一次有范围、限时的查找：

```bash
for base in /usr/src /home/f00955680/metax_dev /home/h00918771/metax_dev; do
  [ -d "$base" ] || continue
  timeout 30s find "$base" -maxdepth 12 \
    -type d \( -name .git -o -name node_modules -o -name site-packages \) -prune -o \
    -type f \( -name core_matt.c -o -name core_mapt.c -o -name '*ummu*queue*.c' -o -name udma_main.c \) -print \
    >> "$OUT/kernel_source_locations.txt" 2>&1
done
cat "$OUT/kernel_source_locations.txt"
```

这只读文件名，不修改文件、驱动或硬件。无权限、限时退出均保留；不要求为查找切换内核。若找不到，可能只安装了二进制模块，需要向编译此内核的人索取源码目录/归档。

重点需要 `drivers/iommu/hisilicon/ummu-core/` 的事件解码、MATT/MAPT管理，以及UDMA的segment导入、TID和WQE构建相关实现。实际文件名以源码树为准；先提供找到的路径，不必盲目打包整个内核。

## 6. 需要提供的文件与恢复主线条件

提供 `test/reference_events/results/urma_address_inventory_01/` 两端完整目录，包含connection JSON/journal、`eids/eid_inventory.json`、compile/query日志与 `kernel_source_locations.txt`。不需要上传查询二进制、GPU扩展或对象文件。

待控制原子请求可完成后，才恢复原128门禁和4096五项大区间对照。本轮排障没有产生新的性能改善数字，之前的性能差距和优化结论不据此改写。

开发侧已用真实上传日志验证权限失败、尾部重叠和远端地址匹配；原生EID查询通过CPU模拟共享库的编译/装载测试，并用归档真实SDK头完成语法编译。真实设备查询及0x13解码仍需服务器环境/对应驱动源码。
