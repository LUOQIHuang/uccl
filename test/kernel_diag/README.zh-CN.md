# 两套内核环境的只读诊断

目标：不切核，读取verbs/URMA两套已安装内核的配置和模块，再保存当前真实加载状态，查清跨内核运行还缺什么依赖。默认比较：

- verbs：6.6.0-145.3.34.165.oe2403sp3.aarch64
- URMA：6.6.0-gdr_f00955680_diag

**当前204若已启动到发行版内核，不必为了采集切回diag。两端当前内核可以不同。** 不需要空闲GPU测试窗口，不编译、不运行Torch/MACA benchmark；Python只用标准库，入口使用系统Python -S并清除常见运行库注入变量。

## 1. 两端分别运行

建议root运行以便读取/boot配置、模块及journal。非root也能采集，但权限不足会原样记录，不会冒充“模块不存在”。202为RANK0，204为RANK1；无需双端同时启动，无TCP端口需求。

```bash
cd /home/h00918771/uccl-ep-xpu-integration-new
RANK=0  # 204改为1
JOB=kernel_env_01
OUT=/home/h00918771/metax_dev/kernel_diag_results
bash test/kernel_diag/run.sh --node-rank "$RANK" --job "$JOB" \
  --output "$OUT" --journal
```

成功标志KERNEL_DIAG_COLLECTED，表示采集完成，不代表两种通信路径都已支持。输出目录必须新建；再次采集换JOB尾号，不覆盖之前证据。若/usr/bin/python3不可用，可设置KERNEL_DIAG_PYTHON为系统Python3绝对路径（最低3.6），不必使用MetaX虚拟环境。

脚本会：

1. 读取/boot/config-版本，缺失时尝试/lib/modules/版本/build或source下的.config，保存完整可读配置并生成差异CSV。
2. 对两套内核分别执行只读modinfo -k，查询metax、P2P bridge、UDMA/UB/UMMU及RDMA相关模块；保存version、srcversion、vermagic、依赖、文件位置与SHA256。也检查modules.builtin清单。
3. 读取当前/sys/module的版本、参数，比较当前加载srcversion与磁盘记录，避免把更新后的磁盘模块当成正在运行的模块。
4. 读取当前PCI路径/NUMA/链路/电源、RDMA端口与GID1/3、UB设备节点、CPU频率策略、启动参数、内存及panic/OOM配置。
5. 用不带赋值的setpci读取Root Port的Device Control 2，记录Atomic Request bit6，不改寄存器。
6. --journal额外读取当前及上一轮启动最后500行内核日志。上次启动日志若未持久化、权限不足或已丢失，会记录缺口；不会据此断言没有OOM/panic。

不执行modprobe/insmod/rmmod，不加载其他内核的.ko；不修改PCI/启动项/驱动参数/服务，不删除转储、不kill进程；不调用mx-smi/ibv_devinfo/ubctl，不创建GPU上下文。采集动作只向输出目录写结果；只读诊断仍会有少量CPU和磁盘读取开销。

## 2. 提供哪些文件

两端各自将输出的node目录复制到仓库结果目录：

```bash
DEST="$PWD/test/kernel_diag/results/$JOB"
mkdir -p "$DEST"
test ! -e "$DEST/node${RANK}"
cp -a "$OUT/$JOB/node${RANK}" "$DEST/"
```

提供test/kernel_diag/results/$JOB整个目录。每端包括inventory.json、SUMMARY.zh-CN.md、config_diff.csv、两套可读config.txt，以及可选的本轮/上轮启动日志。不需要内核Image、.ko或vmcore大文件。

## 3. 如何解释结果

| 观察 | 能说明什么 | 不能说明什么 |
| --- | --- | --- |
| 发行版内核modinfo能解析bridge/UDMA模块 | 磁盘有模块元数据，需核对vermagic/依赖 | 不证明当前已加载、EID可用或GPU传输正确 |
| 模块未解析 | 可能缺包、索引、名称不同、权限或工具缺失 | 不能直接断言该内核不支持 |
| 当前/sys/module存在bridge，runpm等状态已记录 | 可评估当前环境是否满足脚本的部分预检依赖 | 不能证明GPU segment注册或WRITE/FADD成功 |
| 两套GPU KMD版本不同 | 存在驱动混杂因素，应单列比较 | 不能单靠版本认定性能高低 |
| hash不同 | 二进制身份不同 | 模块为不同内核重编译本身就会改变hash，不能视为功能补丁差异 |
| vermagic版本相同 | 部分模块身份匹配 | 不能替代完整ABI/符号及运行验证 |
| 当前与上轮journal有OOM/panic线索 | 提供重启原因证据 | 最后500行没出现，不等于排除；内核切换也可能来自启动默认项 |

非运行内核的实际加载模块、CPU governor、PCI寄存器、EID和设备运行状态不能靠离线文件恢复；报告明确将磁盘数据与当前运行状态分开。

完整内核源码差异仍需两套源码基线commit/源码包及补丁集，并证明与已安装Image/模块对应。现有仓库的kernel_candidate.patch只是候选补丁，不能当成发行版与diag的完整diff。

## 4. 后续判断顺序

先核对GPU KMD、bridge/UDMA/UMMU依赖和当前设备状态。静态证据若显示有希望跨内核运行，再安排最小GPU注册和WRITE/FADD正确性验证；本脚本不会自动启动它们。暂不因内核名称不匹配直接断言“不支持”，也不绕过现有预检直接跑完整benchmark。

本轮环境诊断也不需要重新执行已中断的完整扩展编译。当前新报告只有“重启/SSH不可用”等现象，OOM尚需日志证明；两台物理服务器同时编译不能仅凭时间接近就认定为共享内存不足。
