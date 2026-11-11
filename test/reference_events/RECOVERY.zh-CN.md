# smoke128 建连失败：最短排查流程

## 当前能确定的结论

对应上传提交32546001、JOB=urma_ref_smoke128_01。两端的普通模块和事件模块已编译，事件模块的import和硬件检查通过。运行到第一组BF16正确性测试时，Proxy在线程中调用urma_bind_jetty失败，rc=4096，随后SIGABRT。两端都只留下layout的3个样本，尚无Dispatch采样或事件文件。

两端payload/control显存注册均success；kernel中的MAD发送出现Tx status error，随后Session超时、ubcore_exchange_tp_info失败，与当前Proxy PID/TID一致。这证明本次阻断在RC连接建立，而不是采集JSON解析或编译错误。缺少event_trace.jsonl、PAIR_STOP是后续症状。

**目前不能确定“只是刚重启、等一会即可恢复”，也不能据此认定固件坏了或完全排除打桩影响。**首次运行开启了映射主机时钟，但没有同一环境下未打桩的运行对照。报告中RoCE设备ACTIVE、ping成功，也没有验证udmac0d1e2的EID/TP连接。代码目前仍选择设备EID列表第一项；缺少本次EID/端口明细，不能确认该选择是否正确，不盲改EID。

另外，204上传日志末尾出现udmac0d1e2注销，后续activate dev resp超时及udrv activate失败，发生在失败后的模块重载过程中。它不能解释更早的首次失败，却说明重载后的状态可能更差，先核对当前设备，不能接着把模块“加载了”等同于设备可用。

## 1. 保留现有编译产物，不重新编译

只增加排障脚本和文档，ep/src、ep/include、setup.py均未改变；aa5accb0构建产物仍满足source_manifest校验。保持原来成功check时的Python、MACA_PATH、私有URMA库和额外库路径，不为了排障切MACA。

两端执行，204将RANK改为1：

```bash
cd /home/h00918771/uccl-ep-xpu-integration-new
set -euo pipefail
export URMA_DIAG_HOME=/home/h00918771
export URMA_DIAG_PYTHON=/home/h00918771/metax_dev/venv-maca-py312/bin/python
export URMA_DIAG_LIBRARY_DIR=/home/h00918771/metax_dev/urma_after_kernel_20261029/diag/umdk_private_618957c3/lib
# MACA_PATH及URMA_DIAG_EXTRA_LIBRARY_DIRS继续使用本次check成功时的原值。
RANK=0
BASE=/home/h00918771/metax_dev/reference_event_build/urma_base_v1
TRACE=/home/h00918771/metax_dev/reference_event_build/urma_events_v1
```

如果本次仍是同一shell，直接沿用已有这些变量。设备因模块重载而消失时，测试会在hardware check失败，保留该结果即可；没有必要尝试Formal 4096测试。

## 2. 现在只做一次普通模块128-token对照

202先启动，180秒内204启动相同JOB：

```bash
bash test/reference_events/recover.sh \
  --node-rank "$RANK" --master-addr 141.61.17.202 \
  --step baseline --build-dir "$BASE" --job urma_ref_connect_base_01
```

这是原Normal入口，固定128 tokens、warmup1/samples3，保留原正确性检查；不开GPU事件、CPU时间轴或主机时钟线程。它只负责排障，不作正式性能对比。一次失败即停，不循环重试。

入口自动在运行前后只读采集URMA设备sysfs（包括可见EID/端口）、urma_admin show、已加载模块srcversion、相关服务状态、IP接口及邻居、dmesg；不切内核、不重载模块、不改网络或EID。缺失命令、权限失败、未知命令行选项都保存在JSON中，不伪装为检查通过。urma_admin记录工具实际路径，其输出不等于应用实际加载的私有provider，后者仍以runtime.json为准。完成或失败后自动收集；退出码保持原运行失败。

结果目录：test/reference_events/results/urma_ref_connect_base_01/。两端均提供整个目录，重点保留：

- node0/node1的normal/bench.log、status.json、setup/preflight/runtime和模块manifest。
- console/node0/connection、console/node1/connection：connection_before/after.json、dmesg_before/after/delta.txt、connection_diagnosis.json。
- build/node0、build/node1中可获得的构建日志。无需.so、objects。

读取dmesg被拒绝或环形缓冲发生覆盖时，诊断会明确标注无法精确提取增量；这时全量日志中的旧错误不能当成本次重现。

## 3. 按结果分支，避免无效重跑

| 本次结果 | 下一步 |
| --- | --- |
| hardware check报设备消失、bridge缺失等 | 停止应用测试，先由环境维护者恢复当前驱动/设备；此次还不能验证绑定问题 |
| 普通模块也报相同bind/TP超时 | 支持故障存在于不含新打桩的连接路径；先核对两端URMA EID、端口和控制面状态，暂停打桩及4096性能任务 |
| 普通模块通过 | 再执行下方同一事件模块off对照；普通模块通过本身不能证明故障已根治 |
| 普通模块失败于其他位置 | 依据bench首个错误单独处理，不能自动归类为MAD故障 |

**仅在两端普通模块通过时**，用已编好的事件模块关闭采集：

```bash
bash test/reference_events/recover.sh \
  --node-rank "$RANK" --master-addr 141.61.17.202 \
  --step off --build-dir "$TRACE" --job urma_ref_connect_off_01
```

**仅在两端off通过时**，再开启事件：

```bash
bash test/reference_events/recover.sh \
  --node-rank "$RANK" --master-addr 141.61.17.202 \
  --step trace --build-dir "$TRACE" --job urma_ref_connect_on_01
```

off通过、on失败才会加强对主机时钟/事件激活的怀疑；也仍需结合连接日志及环境变化判断，不凭一次先后顺序断言因果。只提供实际运行的JOB目录即可。on通过后再回README第5节做4096正式三项，不将此次128-token结果混进正式统计。

## 4. 普通模块也绑定失败时，如何恢复

本次日志尚不能给出可靠的一条重置命令。优先把上述连接快照交给环境维护者，核对：udmac0d1e2是否重新注册且可激活；两端实际EID/活动UB端口与互联是否匹配；相关控制面组件状态；已加载udma/ubcore/uburma与私有用户态provider是否配套。根据设备实际配置采取恢复动作，不把以太网ping当成URMA控制面连通证明。

本脚本不自动设置EID、重启未知服务或重载共享驱动。恢复后先用**新JOB尾号**执行一次baseline128，通过再走off/on。不要通过关闭RC绑定、吞掉4096错误或把profile校验改成PASS来绕过，此时继续提交WRITE没有有效连接。
