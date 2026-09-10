"""Describe positive path frequency under fixed friction assumptions, not fills.

Consumes the frozen exports from research_limit_up_paths.py. Does not touch DB
or production strategy logic. All input definitions and selection biases persist.
"""
import argparse
import csv
import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path
from statistics import mean

import numpy as np


def adjusted_pct(gross_pct, total_friction_pct):
    """Illustrative equal entry/exit friction; not actual broker fee rates."""
    half = total_friction_pct / 200
    return ((1 + gross_pct / 100) * (1 - half) / (1 + half) - 1) * 100


# Summarize the selected historical outcome sample with its empirical positive-rate measurements.
def stats(rows, horizon=5, cost=.3):
    values = [adjusted_pct(float(r[f"r{horizon}"]), cost) for r in rows]
    if not values:
        return {"n": 0}
    wins = [x for x in values if x > 1e-10]
    losses = [x for x in values if x < -1e-10]
    return {"n": len(values), "positive_n": len(wins), "positive_pct": len(wins) / len(values) * 100,
            "mean_pct": mean(values), "average_positive_pct": mean(wins) if wins else None,
            "average_negative_pct": mean(losses) if losses else None,
            "below_minus5_pct": mean(x < -5 for x in values) * 100}


# Compare historical positive outcomes within the matched cohort definition.
def matched_probability(rows, pool, dates, cost=.3):
    peers = defaultdict(list)
    for r in pool:
        peers[(r['signal_date'], r['age'])].append(r)
    daily = defaultdict(list)
    for r in rows:
        group = [p for p in peers[(r['signal_date'], r['age'])] if p['symbol'] != r['symbol']]
        if group:
            p = mean(adjusted_pct(float(x['r5']), cost) > 1e-10 for x in group)
            daily[r['signal_date']].append((int(adjusted_pct(float(r['r5']), cost) > 1e-10) - p) * 100)
    if not daily:
        return {"dates": 0, "difference_pp": None, "ci95_pp": None}
    interval = None
    if len(daily) >= 8:
        values = np.array([mean(daily[d]) if d in daily else np.nan for d in dates])
        rng = np.random.default_rng(20260907)
        samples = []
        for _ in range(2000):
            starts = rng.integers(0, len(dates), math.ceil(len(dates) / 5))
            indices = np.concatenate([(s + np.arange(5)) % len(dates) for s in starts])[:len(dates)]
            v = values[indices]
            if np.any(np.isfinite(v)):
                samples.append(float(np.nanmean(v)))
        interval = [float(v) for v in np.quantile(samples, [.025, .975])]
    return {"dates": len(daily), "difference_pp": mean(mean(v) for v in daily.values()), "ci95_pp": interval}


# Read exported historical cohorts and calculate the pattern positive-rate comparisons.
def analyze(directory):
    # Load one named input file from the surrounding research export directory.
    def read(name):
        with (directory / name).open(encoding='utf-8-sig', newline='') as f:
            return list(csv.DictReader(f))
    rows, pool = read('signals.csv'), read('eligible_pool.csv')
    original = json.loads((directory / 'comparison.json').read_text(encoding='utf-8'))
    pool = [r for r in pool if r['outcome_status'] == 'complete']
    dates = [r['date'] for r in original['daily_coverage'] if r['date'] <= original['inventory']['mature_end']]
    results = {}
    for key, prior in original['summary'].items():
        all_rows = [r for r in rows if r['strategy'] == key]
        complete = [r for r in all_rows if r['outcome_status'] == 'complete']
        mature = [r for r in all_rows if r['outcome_status'] != 'immature']
        main = stats(complete)
        missing = len(mature) - len(complete)
        results[key] = {
            'label': prior['label'], 'complete': len(complete), 'mature': len(mature),
            'gross_d5': stats(complete, cost=0), 'friction03_d5': main,
            'friction06_d5': stats(complete, cost=.6), 'friction03_d3': stats(complete, horizon=3),
            'early': stats([r for r in complete if r['signal_date'] <= '2026-08-07']),
            'late': stats([r for r in complete if r['signal_date'] >= '2026-08-17']),
            'without_oneprice': stats([r for r in complete if r['one_price_up'] == '0']),
            'missing_outcome_bounds_pct': ([main['positive_n'] / len(mature) * 100,
                                           (main['positive_n'] + missing) / len(mature) * 100] if mature else None),
            'matched': matched_probability(complete, pool, dates),
        }
    payload = {'kind': 'historical_price_path_friction_sensitivity_not_realized_profit',
               'source_input_sha256': original['input_sha256'],
               'source_files_sha256': {name: hashlib.sha256((directory/name).read_bytes()).hexdigest()
                                       for name in ['comparison.json', 'signals.csv', 'eligible_pool.csv']},
               'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
               'cost_assumptions_pct': [0, .3, .6], 'summary': results}
    (directory/'positive_rates.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    lines = ['# 形态正收益概率：固定摩擦假设下的补充研究', '',
             '研究日期：2026-09-07。沿用原报告固定形态与完整五日样本，未搜索新参数。', '',
             'T收盘确认，统一以D+1开盘为价格起点，观察D+3和D+5收盘。0.3%和0.6%为双边总摩擦情景，各一半作用于起点成本和终点所得，不代表实际费率。公式：(1+毛路径变化)×(1-单边摩擦)/(1+单边摩擦)-1。', '',
             '所有数字都是在假定能按基准价格成交时的价格路径测算，不是实际账户盈利概率；一字板、跌停封单、盘中滑点、公司行动、缺失数据和缓存选择偏差尚未充分解决。', '',
             '| 形态 | 完整/成熟 | 毛正比例 | 0.3%情景正比例 | 0.6%情景正比例 | D+3正比例(0.3%) | 正样本均值 | 负样本均值 |',
             '| --- | --- | --- | --- | --- | --- | --- | --- |']
    for r in results.values():
        m=r['friction03_d5']
        lines.append(f"| {r['label']} | {r['complete']}/{r['mature']} | {r['gross_d5']['positive_pct']:.1f}% | {m['positive_pct']:.1f}% | {r['friction06_d5']['positive_pct']:.1f}% | {r['friction03_d3']['positive_pct']:.1f}% | {m['average_positive_pct']:+.2f}% | {m['average_negative_pct']:+.2f}% |")
    lines += ['', '## 稳定性与缺失边界', '',
              '下表除成熟缺失边界外均以完整样本测算。前段7/24至8/7，后段8/17至8/28，中间5个交易日的信号隔离。相对差值为同日、同距涨停天数的其他股票的正比例之差，按日期等权；未控制板高、行业、市值。区间为5日移动块重采样的95%探索区间，无多重比较校正。', '',
              '| 形态 | 前段正比例(n) | 后段正比例(n) | 剔除D+1疑似一字(n) | 成熟缺失边界 | 同日参照差值/区间(百分点) |',
              '| --- | --- | --- | --- | --- | --- |']
    for r in results.values():
        cells = [f"{r[k]['positive_pct']:.1f}% ({r[k]['n']})" if r[k]['n'] else '无' for k in ['early','late','without_oneprice']]
        lo,hi=r['missing_outcome_bounds_pct']; match=r['matched']; ci=match['ci95_pp']
        interval=f"[{ci[0]:+.2f}, {ci[1]:+.2f}]" if ci else '不足8日'
        lines.append(f"| {r['label']} | {' | '.join(cells)} | {lo:.1f}%至{hi:.1f}% | {match['difference_pp']:+.1f} / {interval} |")
    lines += ['', '成熟缺失边界：把所有缺失结果分别视为非正、正，构造概率下界与上界；不是置信区间，也没有包括无法识别形态的历史缺失股票。剔除一字是事后诊断，不能假设事先识别并完成替代成交。', '',
              '## 如何解释', '',
              '正比例只回答更常出现正数，不等于收益期望更高。正负样本幅度、尾部及跨时段表现必须一并比较。短样本的高正比例不能直接变成买入结论。', '',
              '本窗口的解释：深回撤对照的正比例较高，但同日参照差值接近零，不能确认为形态优势；横盘缩量的负样本幅度较小，但仅21个完整样本；温和回撤有265个完整样本，加入企稳条件未显示稳定增益。2进3正比例前后段由74.1%变为40.0%，不能按连板热度推断更容易盈利。深回撤修复只有9个完整样本且缺失40%，不能按77.8%的表面比例排第一。', '',
              'D+3沿用完整五日样本，便于固定样本比较，不能当作全部三日成熟事件的估计。不同形态存在股票/锚点重叠。当前结果不证明新时期存在优势。', '',
              '普通A股的交收前转卖限制意味着不能使用“次日开盘至次日收盘”作为可日内完成的净收益回测。来源：[深圳证券交易所交易规则（2026年修订），3.1.4至3.1.5](https://docs.static.szse.cn/www/lawrules/rule/trade/current/W020260424690713155663.pdf)。', '',
              '旧账户级研究发现涨停后的部分变化发生在次日开盘，本次起点因此保留D+1开盘，未将此前跳空计入测算。该旧样本不证明当前形态有效。来源：[Chen等，Daily Price Limits and Destructive Market Behavior](https://www.princeton.edu/~wxiong/papers/PriceLimit.pdf)。', '',
              '输入记录指纹：`'+original['input_sha256']+'`。原始定义、筛选与缺失说明见同目录report.md；逐项结果见positive_rates.json。']
    (directory/'positive_rates.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')
    print(json.dumps(results, ensure_ascii=True))
    return payload


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, default=Path('output/research/limit-up-paths-20260907'))
    analyze(parser.parse_args().input)
