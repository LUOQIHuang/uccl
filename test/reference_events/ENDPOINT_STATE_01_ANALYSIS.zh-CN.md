# 端点状态复核：解码依据匹配，四个模块的构建标识待匹配

更新：已取得UMMU-core、ubcore、uburma的匹配候选源码，UDMA仍未找到。两端查询和已知路径尝试已经完成，下一步见 [匹配源码结果与UDMA来源清单](MATCHING_SOURCES_01_ANALYSIS.zh-CN.md)，不再执行本页第4节的收集命令。

输入提交 `902f3d26`、`75b7d848`，结果为 `results/urma_endpoint_state_01/node0`、`node1`。这些是只读状态查询，不是新一轮benchmark。

## 1. 本次确认了什么

| 核对项 | 结果 | 意义 |
| --- | --- | --- |
| 两端5个已加载重点模块的srcversion、磁盘二进制SHA256 | 分别一致；每端loaded也与installed一致 | 没有发现两端这些模块版本不同，不能把问题描述为“两端驱动冲突” |
| 事件解码所在ummu模块 | 源码树生成srcversion与两端loaded一致 | 支持此前0x13=EVT_P_PERMISSION、TID=189、IOVA=202屏障地址的解码 |
| UMMU-core、UDMA、ubcore、uburma | 提供源码树的生成srcversion与loaded不同 | 这些实现尚不能当作已验证匹配的运行版本，需要对应构建源码 |
| 两端EID0与UMMU可见EID列表 | 仍相同 | 端点/地址空间配置仍是重要嫌疑，但尚未证明冲突 |
| 设备guid属性 | 两端都是全零 | 该值不能用作两台物理节点相同或身份冲突的证明 |
| 主UE表查询 | `main_ue_eid show`不被此工具支持 | 原结果没有取得此映射；已按本次help改用lookup，不将失败输出当作表为空 |

五个已加载重点模块是ummu、ummu_core、udma、ubcore、uburma；ubmem_vmmu有磁盘文件但没有对应loaded字段，不把它计为已加载模块。

## 2. 精确版本对照

| 模块 | 提供树的生成srcversion | 两端实际加载srcversion |
| --- | --- | --- |
| ummu | `9B554EAE99E6CFBA350EE28` | `9B554EAE99E6CFBA350EE28` |
| ummu_core | `D98DE01A0E7ED832AE96216` | `3500E02ECE5F584E58AF27F` |
| udma | `8E42E2310A9B00770B88DFD` | `A3473D20CABD75A33EAD171` |
| ubcore | `21A93C6028077D17580EB48` | `53C2130D466B6E57BA01331` |
| uburma | `6AA3EA07484C00C1E5E7E7C` | `ACC682CB49B082B37E5EDE3` |

srcversion是构建来源校验线索，不是可重复二进制构建的数学证明。生成文件可能陈旧，源码也可能在编译后修改；上表说明后四项尚未完成对应关系验证，不能仅据此宣布当前运行的驱动存在错误混装。

此前提供树中的UDMA token/TID与授权代码可以帮助找路径，但不能继续将它作为实际运行实现作确定归因。UMMU事件解码所在模块的版本匹配是本轮新增的有效支持。

## 3. 当前结论与行动边界

当前运行失败有设备权限表异常证据：204记录了TID189对202主机屏障地址的访问事件；同次运行内部FADD返回ACK超时。现在仍需区分目标地址空间错误、TID/token不匹配或正确目标上的授权问题。

本次没有新的Dispatch性能数据，也不能用这个当前通信故障改写历史Normal性能回退结论。不要为验证上述版本问题重跑128/4096、切内核、重载模块或修改EID。

环境维护者现在即可并行核对：204的源EID/UE映射是否符合当前拓扑，为什么历史成功时为`df00:4200`而当前首项与202同为`df00:0200`。这项配置核对不必等待源码定位完成；先请其确认合法配置，不盲改index。

## 4. 下一步：定点读取已有构建，不再全目录扫描

新脚本只尝试此前找到的几处构建目录中的固定 `.mod.c` 路径。读取生成srcversion，只有与本次loaded目标一致才复制相应模块的源码。输出JSON保存版本字段，避免再次被Git忽略。它还用已经确认的CLI语法查询当前EID和历史204 EID的主UE映射。

两端可独立运行一次，无需同步或GPU。两端文件可见范围不同，因此都尝试已知路径；脚本不会遍历整个/home。202增加已知源码根，204不传该参数。

```bash
cd /home/h00918771/uccl-ep-xpu-integration-new
git pull --ff-only https://github.com/LUOQIHuang/uccl.git codex/metax-urma-integration
PY=/home/h00918771/metax_dev/venv-maca-py312/bin/python
RANK=0                    # 204改为1
EXTRA=()
if [ "$RANK" = 0 ]; then EXTRA=(--root /home/g00953922/kernel_f00955680); fi
"$PY" test/reference_events/collect_matching_modules.py \
  --state "test/reference_events/results/urma_endpoint_state_01/node$RANK/endpoint_state.json" \
  --output "test/reference_events/results/urma_matching_sources_01/node$RANK" \
  "${EXTRA[@]}"
```

输出目录必须新建。默认候选为f00955680和h00918771目录下、`urma_after_kernel_20261029/diag/`中的 `gdr_build_753c812e/tree`、`kernel_build_5c8e613f/tree`、`kernel_candidate_e747a3b1/tree`，以及显式--root路径。只读固定版本文件，匹配后复制该模块源码，不编译、不加载驱动、不创建通信资源。

输出 `copied=`和 `not_found=`。退出码1表示存在未找到或读取不完整的模块，仍应保留结果，并不表示又发生了一次benchmark错误。两个lookup的返回码、输出也全部保留；未配置历史EID时lookup可能失败，不能据此断言当前表损坏。

提供完整 `test/reference_events/results/urma_matching_sources_01/`。若两端均未找到匹配项，**停止继续扫描和重复采集**，向部署这组模块的人索取上表四个loaded srcversion对应的构建源码/补丁。不能用另一份相同uname的源码代替。

收到匹配实现后，再沿EID/地址空间及token/TID路径做代码判断。只有有明确修正项后才安排一次限时连通验证。本页不要求新的GPU实验。
