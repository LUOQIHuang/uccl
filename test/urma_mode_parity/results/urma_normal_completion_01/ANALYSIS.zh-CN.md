# URMA Normal完成合并A/B：本轮结果

结果提交：`fe153efb`。本轮仅测URMA Normal，同一重建模块切换all和paired；不需要新的verbs测试或切换内核。

## 1. 实际收到的数据

- 两端console显示：diagnostic_all、diagnostic_paired、r01/r02的all/paired、r03_all，共7个双机case通过，正确性、模式、设备、退出和API-only检查均为True。
- r03_paired未进入 `CASE_BEGIN normal`，在ready预检查阶段停止。首次两端rc均为1，retry2为node0 rc1、node1 rc0；第一次retry还缺少master-addr参数。因此不能将它归为paired运行时错误或正确性失败。具体预检查原因需读取服务器失败目录的 `normal/preflight_error.txt`。
- 当前完整原始采样、phases和内核快照只有node0。node1有console，但没有原始结果目录。
- node0的7份原始采样均完整：每case5个测点，每点50个值，首个丢弃、49个有效值。
- 没有setup/status/runtime/host/build_manifest/measurement_boundary等JSON元数据。已确认仓库通用 `*.json` 规则误忽略这些结果；本次补上结果目录例外。补传既有文件即可，不需要重跑。

## 2. 完整调用时延：净收益很小

单位ms；仅使用关闭插桩的API-only数据。all有3轮，paired有2轮，各列取独立运行均值的中位数。两轮中位数为两数中点，不把diagnostic或两端作为额外重复。

| 测点 | all | paired | paired相对变化 |
| --- | ---: | ---: | ---: |
| FP8 Dispatch chunk32 | 4.138 | 4.106 | -0.78% |
| BF16 Dispatch chunk32 | 7.428 | 7.400 | -0.38% |
| Combine chunk12 | 7.302 | 7.211 | -1.24% |
| Combine chunk16 | 7.263 | 7.283 | +0.27% |

只比较r01/r02两个对应轮次，FP8/BF16改善分别约0.56%/0.26%，方向仍是很小的改善，不能靠少一轮解释为漏掉了明显加速。

各轮Dispatch：

- FP8 all：4.120、4.138、4.147ms；paired：4.100、4.111ms。
- BF16 all：7.410、7.428、7.441ms；paired：7.396、7.404ms。

Combine有明显运行级变化：r01_all的chunk12/16为7.887/8.348ms，之后all的chunk16为7.263/7.223ms；paired两轮chunk16为7.285/7.281ms。不能选择r01_all单点宣称paired使Combine快约13%，其余轮次不支持该说法。

与之前verbs基线约3.703/6.497ms相比，当前paired的FP8/BF16仍分别慢约10.9%/13.9%。这是历史不同内核/构建栈的参考比较，不用于单行代码归因。

## 3. 优化路径确实触发，但没有转化成明显端到端收益

以下为各一次独立诊断，4个Proxy合计除以50次调用：

| Dispatch测点 | all实际CQE/次 | paired实际CQE/次 | CQE减少 | paired取消WRITE CQE/次 |
| --- | ---: | ---: | ---: | ---: |
| FP8 | 398.00 | 313.02 | 21.35% | 84.98 |
| BF16 | 398.00 | 316.00 | 20.60% | 82.00 |

两组每次Dispatch均为144个WRITE、254个FADD，逻辑退休数均为398。paired覆盖约59.0%/56.9%的WRITE，没有靠减少数据WRITE或FADD数量取得这个CQE降幅。两组恢复锚点计数均为0。

GPU确认处理CPU范围：FP8约0.096→0.034ms/次，BF16约0.134→0.033ms/次。SDK提交CPU范围约0.09ms/次，分组准备约0.11ms/次，前后变化小。以上是跨线程累计且可能重叠的CPU范围，不可相加作为完整时延占比。

结论是：当前方案确实减少了完成事件和确认工作，但完整Dispatch只改善约几十微秒，没有消除约0.4/0.9ms的核心差距。不支持继续把“增加CQE合并覆盖率”当作首要优化方向。

由于paired还增加completion-order约束并延迟配对WRITE上下文退休，本轮只能评价方案净效果，不能精确分离CQE收益与完成排序/预算持有的代价。必要时可用ordered控制进一步分离，但本轮没有ordered数据。

## 4. 内核告警更接近销毁阶段

比较每case的kernel_before、kernel_after_measurements和kernel_after：node0全部7个成功case均在计时结束快照之后新增TP level AE type16/subtype0，邻近GPU unmap；计时区间未见新增该类TP事件。r02_paired计时区间有free_page回调抑制提示，不是该类TP AE。

all与paired均有退出后告警，因此它不是paired独有的问题。证据更支持退出/解绑阶段关联；仍需node1日志和驱动语义才能判断其影响，不能将“benchmark通过”写成“无内核异常”。

## 5. 下一步优先级

1. 补现有文件：node1原始结果、两端JSON元数据，以及r03_paired失败目录中的preflight_error/gpu_before/urma_preflight等。无需为上传问题重跑。
2. 暂不将paired设为默认；保留all。当前净收益不足以解决Normal差距，也不足以证明所有完成/错误语义。
3. 下一轮更值得比较实际消息长度、对齐和并发下的WRITE-only与WRITE+FADD，区分GPU目标写入/可见性和通知依赖路径。保持同一URMA环境，避免重新大扫shape。
4. 若需要精确评估CQE合并自身收益，才追加ordered控制；不通过任意关闭fence或盲目扩大队列深度来追求单次数字。

本报告由现有原始文件复算。由于缺失JSON和第三轮paired，正式的严格三轮汇总程序拒绝当前目录是预期行为；不伪造缺失的状态文件，也不把两轮标为三轮。
