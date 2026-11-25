# 匹配源码结果：已取得三个模块，停止扫描，索取UDMA对应构建

输入提交 `9a4e70f8`、`e57d972b`，结果位于 `results/urma_matching_sources_01/`。本轮没有运行benchmark。node0未匹配到源码，node1提供的三个模块可供两端运行版本分析，不要求202再复制一次。

## 1. 本次补齐的材料

| 模块 | 与loaded一致的生成srcversion | 提供文件数 |
| --- | --- | --- |
| UMMU-core | `3500E02ECE5F584E58AF27F` | 12 |
| ubcore | `53C2130D466B6E57BA01331` | 73 |
| uburma | `ACC682CB49B082B37E5EDE3` | 17 |
| UDMA | 目标 `A3473D20CABD75A33EAD171`，未找到匹配构建 | 0 |

已核对102份源码的哈希与采集清单相符，Windows工作区换行统一为Linux换行后比较。复核数据见 `results/urma_matching_sources_01/analysis/summary.json`。生成版本与源码快照匹配是来源线索，仍不等价于可重复构建出同一二进制的证明。

## 2. 主UE映射现在有实测结果

两端lookup均rc=0，返回一致：

| 查询的完整EID | 返回主UE EID |
| --- | --- |
| 当前应用首项 `0000:0000:0001:0200:0010:0000:df00:0200` | `0000:0000:003f:0200:0010:0000:df00:0b00` |
| 历史204地址 `0000:0000:0001:0200:0010:0000:df00:4200` | `0000:0000:003f:0200:0010:0000:df00:4b00` |

这说明两个查询键均存在主UE映射，不能再把当前失败解释为“查询的主UE表为空”。历史204 EID不在当前设备可选源EID列表，与它仍存在于主UE映射表是两件不同的事，不能混淆。

匹配ubcore的 `ubcm/ubmad_datapath.c` 在控制通信中查此表，再选择目标主UE jetty。因此本轮补齐了一个控制面配置证据；lookup成功不等于URMA数据通路可达，也不证明FADD实际使用了正确目标地址空间。

## 3. 匹配源码差异没有给出可直接应用的修复

匹配UMMU-core的12份文件与先前树比较，仅 `core_matt.c` 不同。差异是PHYS-SG路径日志从info改为debug，以及映射校验失败时的回滚累计长度修正。普通page-backed SG映射路径没有这组差异。

当前首次失败请求作用于64字节主机屏障内存。没有证据表明上述PHYS-SG日志/回滚差异就是该主机FADD权限异常的原因，不将它作为新的性能优化点，也不修改驱动进行试跑。

匹配ubcore的segment注册路径仍保留所选EID进入segment的逻辑。它没有替我们证明EID相等就是冲突，也没有证明权限表中对应的TID/地址授权正确。

当前有效结论仍是：本次失败运行中，204的UMMU记录权限表异常，TID189、IOVA等于202屏障地址；应用报内部FADD ACK超时。错误目标地址空间与正确目标下的TID/token/授权问题仍需区分。没有新增Dispatch性能数据。

## 4. 下一步不再安排机器扫描或benchmark

已按上一阶段约定尝试两端的已知构建路径。UDMA两份候选构建标识分别为：

- `/home/g00953922/kernel_f00955680/.../udma.mod.c`、kernel_build树：`8E42E2310A9B00770B88DFD`。
- 204的 `gdr_build_753c812e/tree/urma/hw/udma/udma.mod.c`：`C11377C8E8CF3A66C7DBE85`。

它们均不等于运行目标 `A3473D20CABD75A33EAD171`。**停止继续扫描、重复元数据查询与128/4096测试。** 下一步向编译或部署该UDMA模块的人索取对应构建来源：

```text
运行内核：6.6.0-gdr_f00955680_diag
已安装模块：/lib/modules/6.6.0-gdr_f00955680_diag/updates/urma/hw/udma/udma.ko
loaded srcversion：A3473D20CABD75A33EAD171
磁盘模块 SHA256：12337c8ba85b93904bc539320d2aac11bd95cb23e97726ddf711c3b9c5f99176
需要：该构建的UDMA源码目录、配套头文件和实际应用补丁。
重点：segment注册/导入、token/TID、EID、context、jetty及WQE相关实现。
```

如能同时提供私有UDMA用户态provider源码，可核对原子WQE编码；本次实际加载文件为私有库目录下 `urma/liburma-udma.so`，SHA256为 `18ea061e598330ba56ac07d7f9b4691414884aaf805c7f7a1973b1bc5c028967`。这是补充材料，不要求为了找它再次扫描整个服务器。

环境维护者可并行确认204当前可选源EID为何与历史成功时不同，当前源EID/UE归属是否符合拓扑。已有主UE映射结果可直接提供给其核对；不直接insert/delete/flush，也不猜测应该选择哪一个index。

拿到匹配UDMA实现或环境负责人确认的配置修正后，才安排有针对性的代码修正与一次限时连通验证。此阶段不再添加GPU实验，不因尚缺源码就重复此前测试。
