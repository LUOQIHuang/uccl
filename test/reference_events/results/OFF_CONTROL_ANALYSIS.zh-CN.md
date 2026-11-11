# OFF对照：事件构建本身没有明显Dispatch劣化

复核提交af748c84，目录urma_ref_4096_off_01。已使用case_data(reference_events='off')逐端检查，并与原base/on/post的归一化身份比较：生产源码、SDK/编译参数、诊断脚本、benchmark、库、设备、boot均一致；off与on确实加载相同模块SHA。两端status均rc=0、所有checks为True，原始250条API样本/端和5条summary一致，各点49个有效样本。reference_events_version=1、运行开关off，没有事件/正确性采集文件。

## Dispatch回到基线附近

单位μs；参考为同节点原(base+post)/2，不将两端当作重复轮次。

| 节点 | Dispatch | 原前后参考 | 新off | off变化 | off CV |
| --- | --- | ---: | ---: | ---: | ---: |
| 202 | FP8 | 5743.175 | 5750.173 | +0.122% | 0.844% |
| 202 | BF16 | 7962.650 | 7977.226 | +0.183% | 0.227% |
| 204 | FP8 | 5743.044 | 5751.416 | +0.146% | 0.786% |
| 204 | BF16 | 7962.376 | 7977.096 | +0.185% | 0.690% |

这些变化都很小，且波动小于1%。off的layout相对参考为202 −0.907%、204 +0.243%；Combine12约+1.100%，Combine16约+1.248%。没有旧RAII版本在关闭采集时仍出现的几十个百分点Dispatch劣化。

原on相对这次off：202 FP8 +8.601%、BF16 +2.188%；204 FP8 +2.749%、BF16 −15.793%。202 FP8原on含8.846ms离群值，204 BF16则是稳定的大幅变化，不能只按加速方向当成优化成功。

## 收窄了什么

本轮不支持“仅编译进这版参考探针就造成显著性能变化”。不能据此声称寄存器、代码生成等所有资源都完全相同；但其净影响没有表现为此前的大幅差额。

差异更集中在**实际开启采集及采样方式**这一组因素。当前on每样本同步、两端barrier、创建Event、重置/读回trace、启停时钟；off则是连续采样。不能仅由off回归基线就认定CPU时钟线程、GPU事件写入或某一个barrier已经被证明是原因。

这进一步确认要把“采集成功”和“原生产路径的阶段瓶颈已定位”分开。已有CPU请求链确实记录到提交→CQE较长、SDK调用较短；没有相同参考测点的verbs记录，尚不能归因URMA/verbs差额，也不能把较长的完成观察间隔等同于网卡传输服务时间。

## 下一步只比较采样方式，控制住其他因素

新增batch/paced两项，均使用原事件模块、关闭GPU/CPU事件和主机时钟；预热5、采样10，与原on一致；只比较连续调用与逐样本同步/barrier/Event创建。两项新控制在同一版脚本下做严格比较，不要求重跑旧base/on/post/off。

之所以需要一个短batch对照，是因为旧off为50/50，新控制为5/10；同时脚本新增模式后diagnostics_hash会改变。不能直接把这些差异忽略后声称“只差同步”。原GPU模块无需重编译。

操作及日志要求见[CADENCE_CONTROL.zh-CN.md](../CADENCE_CONTROL.zh-CN.md)。实现覆盖URMA和verbs共用入口，但现阶段只要求运行当前URMA环境，不协调切内核，也不扩展LL/Combine扫描。

开发侧已验证paced不会调用Capture或CPU timeline、没有GPU事件文件、保留逐次API样本；对新模式参数、两后端合成汇总及旧入口做回归。实际GPU上的batch/paced新路径仍待本轮验证。

## 可复核输出

[off_controls.csv](urma_ref_4096_off_analysis_01/off_controls.csv)包含所有5个API点的两端原始均值、变化和CV；同目录context.json保留身份及模块SHA。生产源文件未变，新提交只修改诊断脚本和文档。
