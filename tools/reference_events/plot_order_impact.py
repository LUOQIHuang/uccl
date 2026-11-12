"""Export historical WRITE ordering A/B evidence, without mixing campaigns."""
import argparse
import hashlib
import json
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager
from plot_stage_summary import read,one,grouped,export


def build(root,output,font):
    source=root/'urma_mode_parity/results/urma_normal_order_current_02/summary/order_comparison.csv'
    data=read(source)
    rows=[one(data,node=n,label=label) for label in ('dispatch_fp8_chunk32','dispatch_bf16_chunk32') for n in (0,1)]
    assert all(r['repeats']=='2' and r['paired_direction_consistent']=='True' for r in rows)
    font_manager.fontManager.addfont(str(font))
    plt.rcParams.update({'font.family':font_manager.FontProperties(fname=str(font)).get_name(),
        'axes.unicode_minus':False,'font.size':11,'svg.fonttype':'path'})
    output.mkdir(parents=True,exist_ok=False)
    fig,ax=plt.subplots(figsize=(12,5.4))
    grouped(ax,['202 FP8','204 FP8','202 BF16','204 BF16'],
        [float(r['relax_us'])/1000 for r in rows],[float(r['strong_us'])/1000 for r in rows],
        names=('WRITE RELAX','WRITE STRONG'),colors=('#24887f','#d47829'))
    ax.legend(loc='lower left',bbox_to_anchor=(0,1.02),ncol=2,frameon=False)
    ax.set_xlabel('Dispatch / ms：两轮运行均值的中位数')
    export(fig,output,'write-order-impact','历史单变量对照：只切WRITE排序，FADD保持STRONG及原fence',
        '每轮每点预热50/采样50/丢首值后49个有效样本；两轮执行次序相反。不是verbs/URMA后端对比。')
    (output/'figure_source.json').write_text(json.dumps(dict(
        source=str(source),sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        metrics=[dict(node=int(r['node']),label=r['label'],relax_ms=float(r['relax_us'])/1000,
            strong_ms=float(r['strong_us'])/1000,strong_over_relax=float(r['strong_us'])/float(r['relax_us'])) for r in rows],
        notes='Historical WRITE-only ordering intervention, two reversed pairs, FADD ordering/fence unchanged. Do not mix its 49-sample API timing with nine-sample paced captures or infer FADD relaxation correctness.'),indent=2),encoding='utf-8')
    print('ORDER_IMPACT_EXPORTED',output)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,default=Path('test'))
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--font',type=Path,default=Path('C:/Windows/Fonts/msyh.ttc'))
    a=p.parse_args();build(a.root,a.output,a.font)
