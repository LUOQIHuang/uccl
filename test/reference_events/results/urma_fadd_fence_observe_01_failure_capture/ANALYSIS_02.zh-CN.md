# 最后一轮补收集：找回成功B样本，历史trace及core仍缺失，停止历史取证

接收提交d4f659bd。此次新增的是只读归档，归档内包含此前另一轮成功的B运行，不是本次collector启动了benchmark。此前同名目标中保留的失败记录不覆盖；按归档路径区分，不能混用。

## 找回的数据

两端最新时间戳下conflicting_source/分别找回peer_off_paced及peer_off_post完整成功记录。已用case_data独立验证四个case的status、原始样本、保存均值、采样协议、boundary profile、模块、运行能力与实际fence；全部通过。

| B配置：202 on、204 off | 202 BF16均值 | 202 CV | 204 BF16均值 | 204 CV |
| --- | ---: | ---: | ---: | ---: |
| 归档peer_off_paced | 7.83272ms | 0.205% | 6.64311ms | 0.606% |
| 归档peer_off_post | 7.85249ms | 2.191% | 6.69375ms | 2.718% |

FADD仍STRONG、WRITE RELAX、窗口8、completion all、lane-mixed。两端正确性检查均通过。这增加了该B配置在4096形状下可成功运行的记录，不能推广为所有形状正确。

此前A基线仍是202 8.08565ms、204 6.67955ms，但A/B各端boot ID不同：

| 端 | A基线boot | 本次找回B的boot |
| --- | --- | --- |
| 202 | f0823b6b… | 632a2223… |
| 204 | 06455034… | 129d9059… |

模块SHA/源码相同不代表运行环境相同。因此不能用这次归档B与旧A相减，宣称获得新的一组严格fence A/B或已复现3.43%。**严格净收益主证据仍是urma_fadd_fence_01的两组反向配对。**

## 无法从存量记录恢复的部分

- 两端all_on_trace、peer_off_trace仍列为missing，未找回正式event_trace或具体preflight_error。此前终端记录的trace尝试在ready阶段退出，未证明进入GPU后SIGSEGV。
- journalctl启动列表各只保留最新一次启动，内核时段查询为空；本测试故障PID+原boot的coredumpctl查询均无记录。本轮已按跨启动和精确PID/boot查询，继续重复同一补收集没有依据。
- 原all_on_post的URMA完成错误/SIGABRT仍是有效故障记录，但没有原生栈可进一步定位源头。

结论：**历史取证到此结束。不再要求重复collect_fence_failure，也不为了补齐旧目录重跑原JOB。**

## 最新现场状态

采集快照显示两端已同为6.6.0-gdr_f00955680_diag，解决了上份快照中202内核不一致的问题。但两端URMA设备均被其他用户的simulator进程占用，尚不满足本轮设备独占条件。不能将本次占用倒推成过去ready失败的原因，也不能擅自结束他人进程。

## 接下来只做A侧采集验证

等设备协调可用，按[新JOB分段运行步骤](../../FADD_FENCE_OBSERVE_RESTART.zh-CN.md)：新启动状态下先128-token采集门禁，再只执行A的paced→trace→post。脚本新增observe-a，不会直接继续B；A任何一步失败立即停止并收集当前现场。保留原生产算子及GPU kernel，不改排序/窗口。

如果A能完成，再评估BF16采集扰动和clock质量，决定是否继续B。若A在ready退出，可直接从本轮preflight_error定位；若启动后失败，用本轮bench及即时内核快照判断，不再寻找已丢失的上一启动记录。新的observe-b具备A三项成功、同模块/boot门禁，但本阶段先不执行B。

当前进展：增加了B功能可运行记录；尚无新的fence开关阶段差异。既有ATOMIC回收背压观测和约278μs/3.43%部分收益结论均不变。
