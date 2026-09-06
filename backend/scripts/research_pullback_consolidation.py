"""Focused frozen-cohort comparison; descriptive research, no production signals."""
import argparse
import csv
import hashlib
import json
import math
import sqlite3
from collections import defaultdict
from pathlib import Path
from statistics import mean, median

import numpy as np

KEYS = ['consolidation', 'mild_raw', 'mild_stable', 'deep_raw', 'deep_repair']


def net(gross):
    return ((1 + float(gross) / 100) * .9985 / 1.0015 - 1) * 100


def metrics(rows):
    if not rows:
        return {'n': 0}
    r5 = [net(r['r5']) for r in rows]
    daily = defaultdict(list)
    for r in rows:
        daily[r['signal_date']].append(net(r['r5']))
    return {'n': len(rows), 'dates': len(daily), 'positive5': mean(x > 1e-10 for x in r5)*100,
            'mean5': mean(r5), 'median5': median(r5),
            'positive3': mean(net(r['r3']) > 1e-10 for r in rows)*100,
            'mean3': mean(net(r['r3']) for r in rows),
            'daily_positive5': mean(mean(x > 1e-10 for x in v) for v in daily.values())*100,
            'daily_mean5': mean(mean(v) for v in daily.values()),
            'below_minus5': mean(x < -5 for x in r5)*100,
            'p10': float(np.quantile(r5, .1)),
            'mae5_median': median(float(r['mae5']) for r in rows),
            'retreat_median': median(float(r['anchor_to_signal_pct']) for r in rows)}


def pair_rows(left, right, same_age=True, same_board=False, retreat_tolerance=None):
    """Symmetric stratum weights: each date/age stratum appears once per day.

    Exclude overlapping symbols on the same date on BOTH sides before matching.
    Only signal-time attributes determine strata and permitted cross-pairs.
    """
    lg, rg = defaultdict(list), defaultdict(list)
    def key(r):
        base = (r['signal_date'], r['age'] if same_age else None)
        return base + (('1' if int(r['board_height']) == 1 else '2+'),) if same_board else base
    for r in left:
        lg[key(r)].append(r)
    for r in right:
        rg[key(r)].append(r)
    result=[]
    for k in sorted(lg.keys() & rg.keys()):
        shared={r['symbol'] for r in lg[k]} & {r['symbol'] for r in rg[k]}
        a=[r for r in lg[k] if r['symbol'] not in shared]
        b=[r for r in rg[k] if r['symbol'] not in shared]
        pairs=[(x,y) for x in a for y in b if retreat_tolerance is None or
               abs(float(x['anchor_to_signal_pct'])-float(y['anchor_to_signal_pct'])) <= retreat_tolerance]
        if not pairs:
            continue
        result.append({'date': k[0], 'left_n': len({x['symbol'] for x,y in pairs}),
                       'right_n': len({y['symbol'] for x,y in pairs}),
                       'left_symbols': sorted({x['symbol'] for x,y in pairs}),
                       'right_symbols': sorted({y['symbol'] for x,y in pairs}),
                       'positive_pp': mean((int(net(x['r5'])>1e-10)-int(net(y['r5'])>1e-10))*100 for x,y in pairs),
                       'mean_pp': mean(net(x['r5'])-net(y['r5']) for x,y in pairs),
                       'mae_pp': mean(float(x['mae5'])-float(y['mae5']) for x,y in pairs)})
    return result


def comparison(strata, dates):
    if not strata:
        return {'dates': 0, 'strata': 0}
    result={'dates': len({s['date'] for s in strata}), 'strata': len(strata),
            'left_n': sum(s['left_n'] for s in strata), 'right_n': sum(s['right_n'] for s in strata)}
    for field in ['positive_pp','mean_pp','mae_pp']:
        daily=defaultdict(list)
        for s in strata:
            daily[s['date']].append(s[field])
        result[field]=mean(mean(v) for v in daily.values())
        result[field+'_ci95']=None
        if len(daily)<8:
            continue
        values=np.array([mean(daily[d]) if d in daily else np.nan for d in dates])
        rng=np.random.default_rng(20260907)
        boot=[]
        for _ in range(2000):
            starts=rng.integers(0,len(dates),math.ceil(len(dates)/5))
            ids=np.concatenate([(s+np.arange(5))%len(dates) for s in starts])[:len(dates)]
            v=values[ids]
            if np.any(np.isfinite(v)):
                boot.append(float(np.nanmean(v)))
        result[field+'_ci95']=[float(v) for v in np.quantile(boot,[.025,.975])]
    return result


def price_box(window):
    """Original consolidation price predicate, with no volume filter."""
    if len(window)<3 or len(window)>5:
        return False
    post=window[1:]
    retreat=window[-1]['close']/window[0]['close']-1
    return (-.05<=retreat<=.08 and max(r['high'] for r in post)/min(r['low'] for r in post)-1<=.08)


def load_frozen_bars(db, original):
    # Verify the exact original snapshot fingerprint before adding any features.
    with sqlite3.connect(db.resolve().as_uri()+'?mode=ro',uri=True) as c:
        c.row_factory=sqlite3.Row
        c.execute('PRAGMA query_only=ON')
        c.execute('BEGIN')
        inputs=[[dict(r) for r in c.execute(q)] for q in original['queries']]
    digest=hashlib.sha256(json.dumps(inputs,sort_keys=True,ensure_ascii=True).encode()).hexdigest()
    if digest!=original['input_sha256']:
        raise ValueError('Database differs from frozen study. Recreate all cohorts together; do not silently mix versions.')
    return {(r['symbol'],r['trade_date']):r for r in inputs[1]}


def analyze(directory,db):
    def read(name):
        with (directory/name).open(encoding='utf-8-sig',newline='') as f:
            return list(csv.DictReader(f))
    original=json.loads((directory/'comparison.json').read_text(encoding='utf-8'))
    signals,pool=read('signals.csv'),read('eligible_pool.csv')
    bars=load_frozen_bars(db,original)
    calendar=sorted({d for s,d in bars})
    dates=[r['date'] for r in original['daily_coverage'] if r['date']<=original['inventory']['mature_end']]
    groups={k:[r for r in signals if r['strategy']==k and r['outcome_status']=='complete'] for k in KEYS}
    summaries={}
    for k,rows in groups.items():
        earliest_age2=[r for r in rows if int(r['age'])>=2]
        prior=original['summary'][k]
        summaries[k]={'label':prior['label'],'mature':prior['mature_signals'],'main':metrics(rows),
                      'age2plus_first_signals_only':metrics(earliest_age2),
                      'early':metrics([r for r in rows if r['signal_date']<='2026-08-07']),
                      'late':metrics([r for r in rows if r['signal_date']>='2026-08-17']),
                      'tencent_outcome':metrics([r for r in rows if r['all_tencent_history']=='True'])}
    comparisons, matching_strata={}, {}
    for other in KEYS[1:]:
        for label,kwargs in [('same_date',{'same_age':False}),('same_date_age',{}),
                             ('same_date_age_board',{'same_board':True}),
                             ('same_date_age_board_retreat2pp',{'same_board':True,'retreat_tolerance':2})]:
            key='consolidation_vs_'+other+'_'+label
            matching_strata[key]=pair_rows(groups['consolidation'],groups[other],**kwargs)
            comparisons[key]=comparison(matching_strata[key],dates)
    # Frozen membership for shrink / no-shrink comparison uses all observed eligible
    # days, not optimized triggers; explicitly a separate daily-observation ablation.
    box_shrink,box_other=[],[]
    for r in pool:
        a,t=calendar.index(r['anchor_date']),calendar.index(r['signal_date'])
        window=[bars[(r['symbol'],d)] for d in calendar[a:t+1]]
        if not price_box(window):
            continue
        homogeneous=len({b['source'] for b in window})==1
        valid_volume=all(b['volume'] is not None and b['volume']>0 for b in window)
        if not homogeneous or not valid_volume:
            continue
        row={**r,'volume_ratio':mean(b['volume'] for b in window[1:])/window[0]['volume']}
        (box_shrink if row['volume_ratio']<=.75 else box_other).append(row)
    for name,raw in [('shrink',box_shrink),('other',box_other)]:
        summaries['box_'+name]={'eligible_days':len(raw),'mature_days':sum(r['outcome_status']!='immature' for r in raw),
                               'main':metrics([r for r in raw if r['outcome_status']=='complete'])}
    box_shrink=[r for r in box_shrink if r['outcome_status']=='complete']
    box_other=[r for r in box_other if r['outcome_status']=='complete']
    matching_strata['box_shrink_vs_other']=pair_rows(box_shrink,box_other)
    matching_strata['box_shrink_vs_other_board_retreat2pp']=pair_rows(box_shrink,box_other,same_board=True,retreat_tolerance=2)
    for key in ['box_shrink_vs_other','box_shrink_vs_other_board_retreat2pp']:
        comparisons[key]=comparison(matching_strata[key],dates)
    payload={'kind':'historical_focused_comparison_not_live','source_input_sha256':original['input_sha256'],
             'input_files_sha256':{name:hashlib.sha256((directory/name).read_bytes()).hexdigest() for name in ['comparison.json','signals.csv','eligible_pool.csv']},
             'script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
             'summary':summaries,'comparisons':comparisons,'matching_strata':matching_strata}
    (directory/'pullback_vs_consolidation.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
    lines=['# 回撤类与横盘缩量：重点比较','',
           '2026-09-07。复用原研究冻结样本，数据库查询结果与原输入指纹完全一致。未改形态阈值。信号期2026-07-24至08-28，共26个成熟交易日。', '',
           '研究判断：横盘缩量更值得作为下一轮前向观察的主候选，温和回撤保留为基础对照，深回撤单独研究反弹环境。理由是横盘目前的不利路径较小，且缩量相对价格横盘显示初步增量线索；并非已证明横盘盈利概率更高。没有新增生产策略。', '',
           '统一基准为T日收盘确认形态，D+1开盘至D+5收盘。双边总摩擦假设0.3%，两侧各0.15%；不代表实际账户成交或费率。MAE5为五日最低价相对D+1开盘的毛变化，数值越接近零表示观察到的不利极值越小，不是持仓最大回撤。', '',
           '| 形态 | 完整/成熟 | 正比例 | 均值 | 低于-5%比例 | MAE5中位数 | 第10百分位 | 按日等权正比例 |',
           '| --- | --- | --- | --- | --- | --- | --- | --- |']
    for k in KEYS:
        r=summaries[k];m=r['main']
        lines.append(f"| {r['label']} | {m['n']}/{r['mature']} | {m['positive5']:.1f}% | {m['mean5']:+.2f}% | {m['below_minus5']:.1f}% | {m['mae5_median']:+.2f}% | {m['p10']:+.2f}% | {m['daily_positive5']:.1f}% |")
    lines+=['','## 同日、同阶段的直接比较','',
            '下表均为横盘减去对应回撤组。先排除同一分层里两组共有股票，再匹配。每个日期/年龄/板高分层内按合格配对等权；分层在日期内等权，最后日期等权。板高分为首板、2板及以上；回撤距离进一步限定为相对锚点收盘变化差≤2个百分点。两组均为各自首次形态信号。','',
            '| 对照 | 匹配条件 | 匹配日/分层 | 横盘/对照股票日 | 正比例差 | 均值差 | MAE差 |',
            '| --- | --- | --- | --- | --- | --- | --- |']
    for k,v in comparisons.items():
        if k.startswith('box_'):
            continue
        other=next(o for o in KEYS[1:] if k.startswith('consolidation_vs_'+o+'_'))
        name=k[len('consolidation_vs_'+other+'_'):]
        if not v['dates']:
            lines.append(f"| {summaries[other]['label']} | {name} | 0 | 无共同样本 | 无 | 无 | 无 |")
        else:
            lines.append(f"| {summaries[other]['label']} | {name} | {v['dates']}/{v['strata']} | {v['left_n']}/{v['right_n']} | {v['positive_pp']:+.1f}点 | {v['mean_pp']:+.2f}点 | {v['mae_pp']:+.2f}点 |")
    lines+=['','same_date=同日；age=距涨停天数；board=涨停锚点板高分组；retreat2pp=回撤距离≤2个百分点。各行是不同子样本，不是逐步估计的因果效应。缺少可匹配样本不能解释为形态无效。95%探索区间在JSON中保留；不足8个匹配日不估区间。区间采用5交易日循环移动块重采样2,000次，种子20260907；未校正本次新增的多组探索，也未消除缓存选择偏差。匹配日期及双方股票名单保存在JSON的matching_strata。','',
            '## 缩量本身的增量比较','',
            '沿用横盘的全部价格条件和年龄2至4日，限定窗口来源标签一致且成交量有效；仅把量比≤0.75与>0.75分开。这一辅助比较使用每日观察，不按涨停锚点首次信号去重，与主表样本不可混用。','',
            '| 组别 | 完整/成熟股票日 | 正比例 | 均值 | MAE5中位数 |',
            '| --- | --- | --- | --- | --- |']
    for k in ['shrink','other']:
        r=summaries['box_'+k];m=r['main']
        lines.append(f"| {'缩量' if k=='shrink' else '非缩量'} | {m['n']}/{r['mature_days']} | {m['positive5']:.1f}% | {m['mean5']:+.2f}% | {m['mae5_median']:+.2f}% |")
    for k in ['box_shrink_vs_other','box_shrink_vs_other_board_retreat2pp']:
        v=comparisons[k]
        lines.append(f"\n{k}："+ (f"{v['dates']}个匹配日、{v['left_n']}/{v['right_n']}个股票日，正比例差{v['positive_pp']:+.1f}个百分点，均值差{v['mean_pp']:+.2f}个百分点，MAE差{v['mae_pp']:+.2f}个百分点。" if v['dates'] else '无可比样本。'))
    lines+=['','## 重点发现与解读边界','',
            '按样本计算时，深回撤正比例64.8%，温和回撤61.5%，横盘61.9%；按信号日期等权后分别为57.0%、64.9%、59.4%。这说明信号集中出现的日期会影响排序，但三组覆盖日期仍不同，所以日等权也不是最终的公平胜率排名。','',
            '横盘与温和回撤在同日、同年龄匹配后，仅剩7日的11/26个股票日；再匹配板高与回撤距离只剩3日的3/7个股票日。与深回撤按同日同年龄匹配则仅4日的5/6个股票日，且随着匹配条件变化，差值方向会改变。共同样本不足，不适合宣布谁胜出。','',
            '缩量增量检验：同日同年龄的横盘观察中，缩量组相对非缩量正比例差+16.96个百分点，95%探索区间[0.00,32.72]；五日均值差+3.75个百分点，区间[-2.19,8.21]。进一步匹配板高与回撤距离后剩8日的15/29个股票日，均值差区间仍跨零。这只是线索，不能把区间下界触及零或每日重复样本解释为已验证显著优势。','',
            '温和回撤的265个完整首次信号中127个年龄为1，横盘则全部年龄≥2。只筛选首次信号年龄≥2是不同子样本，并不等于把原信号延迟一天；对应结果保存在JSON的age2plus_first_signals_only。', '',
            '形态价格范围存在结构差异：横盘收盘相对锚点为-5%至+8%，温和回撤为-8%至-3%，深回撤为-25%至-10%。因此横盘与深回撤无法在2个百分点回撤距离内匹配；不应把未匹配均值之差全归因于缩量。','',
            '缓存偏向曾被系统跟踪的股票，横盘完整样本少，后段只有2个。未复权、混合采集路径和成交量单位风险沿用原报告；同来源标签不保证量单位完全一致。未核验分钟成交与跌停退出；费用是假设。全部比较属于事后探索，没有新建live策略或宣称最优参数。','',
            '来源：本地冻结CSV与只读SQLite，输入指纹 '+original['input_sha256']+'。完整条件见report.md，成本情景见positive_rates.md，机器可复核结果见pullback_vs_consolidation.json。', '',
            '机制参考：[Chen等，Daily Price Limits and Destructive Market Behavior](https://www.princeton.edu/~wxiong/papers/PriceLimit.pdf)。旧样本同时显示短期延续与更长期反转，不构成本次形态有效的证据。']
    (directory/'pullback_vs_consolidation.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps(payload,ensure_ascii=True))
    return payload


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,default=Path('output/research/limit-up-paths-20260907'))
    parser.add_argument('--db',type=Path,default=Path('backend/data/limituplab.sqlite'))
    args=parser.parse_args()
    analyze(args.input,args.db)
