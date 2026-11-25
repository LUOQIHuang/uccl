# 源码复核：本次UMMU事件是权限表访问异常

输入提交 `2a071875`，源码来自 `results/urma_kernel_sources_01/node0/`。本次收到254份源码文件的收集清单，其中248份正文已进入Git，约3 MiB；6份生成的 `.mod.c` 被仓库 `*.mod*` 规则忽略。主体源码已经够用，不需要重新收集整棵源码。

## 1. 已经向前定位到哪里

按本次提供的源码，204在上次连通失败时记录的事件可解码为：

| 字段 | 解码结果 | 直接依据 |
| --- | --- | --- |
| event code | `0x13 = EVT_P_PERMISSION` | `interrupt.c:66`，枚举注释为权限表遍历过程中的权限异常 |
| TID | **189 / 0xbd** | `raw[0]` 的 `[51:32]`，`queue.h:153` / `interrupt.c:351` |
| 访问地址IOVA | **`0xfffb90bc7000`** | `raw[3]`，`queue.h:160` / `interrupt.c:354` |
| 地址对应的注册区 | **202的proxy 0主机屏障区，64 B、atomic=1** | 前次两端bench注册日志与该地址精确对应 |
| RNW / privileged / stall | 0 / 1 / 0 | 同一份queue.h位定义和驱动解码函数 |
| TECT tag | 0 | 驱动取raw[4]低16位 |

源码位置前缀为 `results/urma_kernel_sources_01/node0/sources/kernel/drivers/iommu/hisilicon/`。解码器验证了完整8个word，并保存源码SHA256；两份解码定义文件的哈希与上传清单一致。结果为 `results/urma_kernel_sources_01/analysis/node1_ummu_decoded.json`。

因此，我们已从“只看到应用ACK超时”推进到“同次运行、同一屏障地址对应的设备权限表异常”。这里的权限指设备地址空间对该地址的访问授权。RNW=0本身不能区分普通WRITE与原子操作；结合首次prepare阶段的内部FADD，才把它与屏障请求关联。

TID是UMMU地址空间/授权检查使用的标识，不是proxy编号。当前还没有故障请求完整的目标segment token与WQE，不能把189直接命名为“202的TID”或“204某个已知buffer的TID”。

## 2. 源码把后续排查范围收窄了

```text
应用选择设备和EID
  → 导出/导入远端segment和token
  → 设备按EID及相关身份选择地址空间
  → UMMU按TID检查访问地址的授权
       本次：TID=189，IOVA=202屏障地址，事件=EVT_P_PERMISSION
  → 同次运行204得到FADD ACK超时，202无法越过prepare
```

上图表达代码中的依赖关系和同次运行的证据，不表示已经证明事件和某条WQE一一配对。

有四个直接相关的代码事实：

1. 应用取 `eid_list[0]` 创建context；两端最新查询的首项相同，而历史成功micro两端EID不同。
2. `ubcore_segment.c:230` 将所选 `eid_index` 对应的EID写入导出的segment，应用所选身份会进入远端内存描述。
3. `udma_segment.c:689` 将导入segment的token右移 `UDMA_TID_SHIFT` 得到TID；这份ABI定义的位移为8。导入接口返回成功不能证明之后的设备访问授权一定成功。
4. `cfg_table.c` 根据GUID维护os_meta/TECT，再建立EID关联和TID表，说明需要一起核对EID、GUID和地址空间，不能只看一条EID字符串就断言冲突。

现在优先区分的是：

| 候选 | 与现有证据的关系 | 当前缺少的证据 |
| --- | --- | --- |
| 选错端点/地址空间 | 两端首项EID相同，且204事件地址属于202，是当前首要嫌疑 | 两端GUID、主UE映射、当前UMMU EID状态及部署版本 |
| 目标TID/token或授权表不匹配 | 0x13直接指向权限表异常，也可能在正确端点下发生 | 故障segment/WQE身份与授权状态；需先确认实际驱动来源 |
| 使用了不匹配的源码解释日志 | 同release并不充分，UDMA/ubcore/uburma部署在updates目录 | 生成的模块srcversion与已加载/已安装版本对应关系 |

本次源码中的非stall事件处理会返回不支持该恢复路径并输出dump；不能期待应用继续轮询就自动修复该访问。暂不修改fence、权限位或EID index。

## 3. 本次代码补充

- 增加离线UMMU解码器，用所提供源码的枚举和位定义解析已有原始事件，不访问服务器设备。
- 修复版本收集遗漏：将6份生成 `.mod.c` 中的srcversion及文件哈希写入JSON，以免再次被Git忽略。原文件清单保留，不伪造它们已经上传。
- 增加一次只读端点状态查询，直接读取GUID等关键字段，避免旧sysfs递归库存被大量EID条目占满而漏掉身份字段。

没有修改生产kernel、URMA算子或Normal工作负载。这些代码用于确认本次不能运行的故障，不产生新的性能优化数字。

## 4. 下一步只补一次元数据，不跑benchmark

两端分别执行，不要求同时启动，不需要GPU窗口、不编译程序、不创建URMA context或提交请求。202额外读取已经找到的源码目录；204不需要再找源码。

```bash
cd /home/h00918771/uccl-ep-xpu-integration-new
git pull --ff-only https://github.com/LUOQIHuang/uccl.git codex/metax-urma-integration
PY=/home/h00918771/metax_dev/venv-maca-py312/bin/python
RANK=0                     # 204改为1
OUT="$PWD/test/reference_events/results/urma_endpoint_state_01/node$RANK"
EXTRA=()
if [ "$RANK" = 0 ]; then
  EXTRA=(--source-root /home/g00953922/kernel_f00955680)
fi
"$PY" test/reference_events/endpoint_state.py \
  --device udmac0d1e2 --output "$OUT" "${EXTRA[@]}"
```

输出目录必须尚不存在。`ENDPOINT_STATE_COLLECTED`仅表示完成采集，不表示配置正确。权限失败、属性缺失、系统工具选项不兼容会保留在JSON，不能当作检查通过。

这一次同时补两类材料：

| 查询 | 为什么必需 |
| --- | --- |
| 202生成模块srcversion、两端loaded/installed版本 | 检查本次解码源码与运行驱动的对应关系；同时核对 `.mod.c` 哈希与之前收集清单 |
| 设备GUID、UE/虚拟化属性、当前EID、主UE查询和UMMU EID只读属性 | 帮助环境负责人核对204是否被配置成了另一端的身份，以及EID/地址空间作用域 |

只需要提供完整 `test/reference_events/results/urma_endpoint_state_01/`。不重跑EID原生查询、不重传源码、不启动128/4096 benchmark。脚本只读状态并写结果，不更改EID、设备权限、模块或PCI配置。

## 5. 收到后怎样行动

若身份/映射配置与历史不同且不符合当前设备拓扑，先由环境负责人修正明确的配置项；不是扫描8个index碰运气。若端点配置正确，则沿导入token/TID和实际provider授权路径继续核对，不把“同EID”直接当根因。若生成模块版本与已加载版本不符，先取得匹配的实现，再作代码修复判断。

只有有针对性的配置或代码修正后，才做一次限时连通验证；通过后恢复原Normal性能主线。本次权限故障不能直接解释之前约10%的剩余Dispatch差距。

开发侧55项CPU回归检查通过，涵盖真实事件解码、生成版本字段提取及只读查询编排。本地没有MetaX设备，未声称已验证服务器端点配置或修复ACK超时。
