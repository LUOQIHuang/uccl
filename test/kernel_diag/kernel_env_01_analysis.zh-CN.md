# kernel_env_01分析：当前跨内核URMA GPU路径的实际差异

来源：results/kernel_env_01/node0与node1的inventory.json、配置及journal；采集时间2026-11-10 23:32（Asia/Shanghai）。两端当前均运行6.6.0-145.3.34.165.oe2403sp3.aarch64。

## 已确认的事实

| 项目 | 当前发行版内核 | 磁盘上的diag内核 |
| --- | --- | --- |
| metax模块 | 已加载3.10.14，srcversion=9EF5AE11D996938760154D7 | 两端磁盘也为3.10.14，srcversion相同；未启动验证 |
| udma/ubcore/uburma | 模块已解析、已加载；udmac0d1e2设备节点存在 | 使用updates/urma中的另一套模块，srcversion均与发行版不同 |
| UMMU/UMMU-core | 已解析、已加载 | 磁盘模块srcversion与发行版不同 |
| udma_metax_p2p_bridge | modinfo -k未解析，modules.builtin未列出，/sys/module未观察到 | 两端均可解析，depends=udma,metax，vermagic为diag版本 |
| 当前runpm_link | 两端均为0 | 非运行内核当前状态不可知 |
| 当前Atomic Request bit6 | 两端Device Control 2=0020，bit6=0 | 非运行内核当前状态不可知 |

发行版不是完全没有URMA基础驱动。眼下更具体的障碍是：当前安装/加载组合没有可用的已确认P2P bridge，且当前Atomic Request状态不满足既有URMA GPU预检。这里只能说明当前部署的缺口，不证明发行版内核无法通过移植获得支持。设备节点存在也不证明EID、GPU segment注册和WRITE/FADD成功。

此前报告将verbs KMD记为3.10.14、diag KMD记为3.8.23。新采集显示两套磁盘模块现在都为3.10.14；应修正当前环境描述，但不能据此追认历史benchmark时diag实际加载的版本。相同version/srcversion不代表两内核模块二进制相同，也不能替代完整源码/ABI核对。

## 配置差异

202能读取两套.config，仅11项差异，涉及LOCALVERSION、编译器标识、模块签名、部分构建/ftrace/TCP配置。HZ=250、PREEMPT_NONE、NUMA、IOMMU、ARM_SMMU_V3、DMA_SHARED_BUFFER、PCI_P2PDMA、DEBUG_KERNEL等关键值相同。没有发现这些Kconfig开关不同导致Normal差距的证据；相似配置也不代表内核源码和驱动实现相同。

204只有发行版.config可读，diag的build/source路径不可用，不能独立确认204的diag配置；不能把202配置直接视作204实际构建配置。

## 上次故障证据

两端上一轮journal均返回“No persistent journal was found”，未获得上次panic/OOM记录。当前日志中的“Panic handler registered”等是处理器注册消息，不是panic现场。采集时MemAvailable约2TB，不支持当时处于整机内存耗尽状态，但不能排除此前OOM或特定cgroup限制。

原采集器对/proc/sys一次请求4MiB，导致部分sysctl返回ENOMEM。在本地Linux可复现“大读失败、小读成功”，已改为每次最多4096字节读取。这些采集错误不能当作真实OOM证据；主结论所用模块/配置/PCI读取不受影响，不需要为此立刻重跑整个采集。

## 下一步最小工作

1. 不切核、不加载diag的.ko，先核对bridge源码/Makefile、未定义符号与发行版udma/metax导出接口，判断能否为发行版内核重编译。
2. 若bridge依赖diag的私有UDMA/UMMU改动，需一起核对补丁接口；不能只复制bridge.ko。现有bridge的vermagic为diag版本。
3. 配套接口确认后才安排受控部署和最小GPU注册、WRITE/FADD正确性验证，并单独处理Atomic Request配置；不要现在直接运行完整URMA benchmark。
4. 若目标是尽快获得同内核性能对照，也可评估diag内核上的verbs路径；磁盘有ib_uverbs/mlx5等只说明存在候选模块，仍需最小功能验证，不能直接宣布可用。

当前尚不能将Normal性能差距归因于某个内核补丁，也不能宣布同内核两后端已经可运行。
