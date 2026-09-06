"""Stress-test frozen consolidation evidence without selecting new thresholds."""
import argparse
import csv
import hashlib
import importlib.util
import json
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean

spec = importlib.util.spec_from_file_location('_focused_research', Path(__file__).with_name('research_pullback_consolidation.py'))
focus = importlib.util.module_from_spec(spec)
spec.loader.exec_module(focus)


def observations(pool, bars, calendar):
    index = {d: i for i, d in enumerate(calendar)}
    result = []
    for r in pool:
        window = [bars[(r['symbol'], d)] for d in calendar[index[r['anchor_date']]:index[r['signal_date']] + 1]]
        if not focus.price_box(window):
            continue
        valid_volume = all(b['volume'] is not None and b['volume'] > 0 for b in window)
        same_source = len({b['source'] for b in window}) == 1
        ratio = mean(b['volume'] for b in window[1:]) / window[0]['volume'] if valid_volume and same_source else None
        result.append({**r, 'volume_ratio': ratio,
                       'arm': 'unknown' if ratio is None else 'shrink' if ratio <= .75 else 'other',
                       'feature_tencent': all(b['source'] == 'akshare.stock_zh_a_hist_tx' for b in window),
                       'both_tencent': all(b['source'] == 'akshare.stock_zh_a_hist_tx' for b in window)
                                       and r['all_tencent_history'] == 'True'})
    return result


def first_box(rows):
    """Lock arm at the first observed eligible price box, even if volume unknown.

    Later shrinkage, completeness and performance cannot change the assignment.
    """
    seen, kept = set(), []
    for r in sorted(rows, key=lambda r: (r['signal_date'], r['symbol'])):
        key = r['symbol'], r['anchor_date']
        if key not in seen:
            seen.add(key)
            kept.append(r)
    return kept


def spaced(rows, calendar):
    index, last, kept = {d: i for i, d in enumerate(calendar)}, {}, []
    for r in sorted(rows, key=lambda r: (r['signal_date'], r['symbol'])):
        i = index[r['signal_date']]
        if i > last.get(r['symbol'], -100) + 5:
            kept.append(r)
            last[r['symbol']] = i
    return kept


def complete(rows, arm):
    return [r for r in rows if r['arm'] == arm and r['outcome_status'] == 'complete']


def point_comparison(left, right):
    strata = focus.pair_rows(left, right)
    result = {'dates': len({r['date'] for r in strata}), 'left_n': sum(r['left_n'] for r in strata),
              'right_n': sum(r['right_n'] for r in strata)}
    for field in ['positive_pp', 'mean_pp', 'mae_pp']:
        by_date = defaultdict(list)
        for r in strata:
            by_date[r['date']].append(r[field])
        result[field] = mean(mean(v) for v in by_date.values()) if by_date else None
    return result


def influence(left, right, field):
    values = sorted({r[field] for r in left + right})
    results = [{'removed': value, **point_comparison([r for r in left if r[field] != value],
                                                     [r for r in right if r[field] != value])} for value in values]
    valid = [r for r in results if r['dates']]
    ranges = {k: [min(r[k] for r in valid), max(r[k] for r in valid)] if valid else None
              for k in ['positive_pp', 'mean_pp', 'mae_pp']}
    return {'removals': len(results), 'ranges': ranges, 'details': results}


def analyze(directory, db):
    original = json.loads((directory / 'comparison.json').read_text(encoding='utf-8'))
    with (directory / 'eligible_pool.csv').open(encoding='utf-8-sig', newline='') as f:
        pool = list(csv.DictReader(f))
    bars = focus.load_frozen_bars(db, original)
    calendar = sorted({d for symbol, d in bars})
    dates = [d['date'] for d in original['daily_coverage'] if d['date'] <= original['inventory']['mature_end']]
    all_boxes = observations(pool, bars, calendar)
    assigned = first_box(all_boxes)
    known = [r for r in assigned if r['arm'] != 'unknown']
    # Source filters are diagnostics on already assigned cohorts, never a search for
    # the first later observation whose outcome/source happens to be available.
    cohorts = {'daily_original': [r for r in all_boxes if r['arm'] != 'unknown'],
               'first_price_box': known, 'first_price_box_spaced': spaced(known, calendar),
               'first_price_box_tencent_features': [r for r in known if r['feature_tencent']],
               'first_price_box_tencent_full_window': [r for r in known if r['both_tencent']],
               'first_price_box_early': [r for r in known if r['signal_date'] <= '2026-08-07'],
               'first_price_box_late': [r for r in known if '2026-08-17' <= r['signal_date'] <= original['inventory']['mature_end']]}
    summaries = {}
    for name, rows in cohorts.items():
        left, right = complete(rows, 'shrink'), complete(rows, 'other')
        summaries[name] = {'shrink': focus.metrics(left), 'other': focus.metrics(right),
                           'mature_shrink': sum(r['arm'] == 'shrink' and r['outcome_status'] != 'immature' for r in rows),
                           'mature_other': sum(r['arm'] == 'other' and r['outcome_status'] != 'immature' for r in rows),
                           'matched': focus.comparison(focus.pair_rows(left, right), dates)}
    left, right = complete(known, 'shrink'), complete(known, 'other')
    transitions = Counter()
    arm_at_first = {(r['symbol'], r['anchor_date']): r['arm'] for r in assigned}
    for r in all_boxes:
        if r['arm'] == 'shrink':
            transitions[arm_at_first[(r['symbol'], r['anchor_date'])]] += 1
    removal_results = {'symbol': influence(left, right, 'symbol'), 'date': influence(left, right, 'signal_date')}
    # A five-session gap diagnoses concentration in a short market episode.
    block_gaps = []
    for i in range(len(dates) - 4):
        excluded = set(dates[i:i+5])
        block_gaps.append({'start': dates[i], 'end': dates[i+4],
                           **point_comparison([r for r in left if r['signal_date'] not in excluded],
                                              [r for r in right if r['signal_date'] not in excluded])})
    payload = {'kind': 'historical_stress_checks_not_independent_validation',
               'source_input_sha256': original['input_sha256'],
               'source_files_sha256': {name: hashlib.sha256((directory/name).read_bytes()).hexdigest()
                                        for name in ['comparison.json', 'eligible_pool.csv']},
               'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
               'dependency_sha256': hashlib.sha256(Path(focus.__file__).read_bytes()).hexdigest(),
               'assignment_counts': dict(Counter(r['arm'] for r in assigned)),
               'daily_shrink_first_arm_counts': dict(transitions), 'summary': summaries,
               'leave_one_out': removal_results, 'five_day_gaps': block_gaps,
               'data_expansion_check': {'project_latest_event_date': original['inventory']['data_end'],
                                        'earlier_event_dates': ['2026-05-13', '2026-05-14', '2026-05-15', '2026-06-02'],
                                        'hithink_local_daily_rows': 0, 'hithink_local_adjustment_rows': 0,
                                        'independent_new_history_added': False}}
    (directory/'consolidation_validation.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    with (directory/'consolidation_first_box_assignments.csv').open('w', encoding='utf-8-sig', newline='') as f:
        writer=csv.DictWriter(f, fieldnames=list(assigned[0]))
        writer.writeheader()
        writer.writerows(assigned)
    labels = {'daily_original': '原每日观察', 'first_price_box': '首次价格横盘固定分组',
              'first_price_box_spaced': '固定分组+同股间隔>5日',
              'first_price_box_tencent_features': '固定分组+特征窗口纯腾讯历史',
              'first_price_box_tencent_full_window': '固定分组+特征和结果纯腾讯历史',
              'first_price_box_early': '固定分组前段7/24至8/7', 'first_price_box_late': '固定分组后段8/17至8/28'}
    def fmt(value):
        return '无' if value is None else f'{value:+.2f}'
    def cell(r):
        return '无样本' if not r['n'] else f"{r['n']} / {r['positive5']:.1f}% / {r['mean5']:+.2f}%"
    lines = ['# 横盘缩量证据：去重、来源与集中度验证', '',
             '2026-09-07。本轮未增加新形态或调参；以价格横盘首次成立时固定分组，检验之前观察是否受重复样本或后来转为缩量影响。仍属于已观察历史上的压力检查，未取得新的独立验证集。', '',
             '结论：本轮没有稳定重复出缩量优势，应下调此前“横盘缩量优先于回撤类”的研究判断。固定首次价格横盘分组后匹配正比例差为+7.82个百分点，加入同股间隔后为-0.51个百分点，纯腾讯特征来源下为-5.56个百分点；这些对照只覆盖4至6个匹配日，既不能确认有效，也不能证明无效。保留横盘缩量与温和回撤两个观察假设，不据此晋级生产策略。', '',
             '原研究在缩量首次出现的T日才确认形态，并没有使用T之后价格。固定首次价格横盘分组回答的是另一问题：共同起点的缩量特征是否有增量。因此口径变化导致结果变化是稳定性警示，不代表已经发现原代码存在未来数据泄漏；后续出现缩量的独立价值仍需要新数据另行验证。', '',
             '统一计算：T收盘确认，D+1开盘至D+5收盘，双边总摩擦假设0.3%。数据缓存选择偏差、未复权和不可确认成交等限制继续存在。', '',
             '## 固定分组比较', '',
             '价格横盘条件沿用原定义：距最近涨停2至4日，涨停后最高/最低-1≤8%，当前收盘相对涨停收盘-5%至+8%。第一次可观察横盘时，量比≤0.75归缩量，否则归非缩量；量缺失或来源标签不一致归未知，后续不重新分配。量比为涨停后平均量/涨停日量。先分组再看未来结果。', '',
             '| 检查口径 | 缩量 n / 正比例 / 均值 | 非缩量 n / 正比例 / 均值 | 匹配日 | 同日同年龄正比例差 | 同日同年龄均值差 |',
             '| --- | --- | --- | --- | --- | --- |']
    for key, r in summaries.items():
        v = r['matched']
        lines.append(f"| {labels[key]} | {cell(r['shrink'])} | {cell(r['other'])} | {v['dates']} | {fmt(v.get('positive_pp'))}点 | {fmt(v.get('mean_pp'))}点 |")
    lines += ['', '主表每组仅统计五日完整样本。纯来源结果窗口筛选利用了后续可见性，只是来源敏感性诊断，不是当时可执行的过滤规则。逐日观察与首次分组的样本不能相加。间隔检查在查看结果前进行；某个早期信号缺结果也会占用间隔。', '',
              '固定首次分组后，缩量仅11个完整事件、6个信号日；间隔检查剩9个完整事件，纯腾讯来源剩6个。后段8/17至8/28没有首次即缩量的成熟事件，无法检查后段重复性。固定分组缩量样本的正比例为72.7%，但低于-5%的比例为18.2%，第10百分位为-10.39%；高正比例不能等同于尾部温和。', '',
              '## 单只股票与单日影响', '',
              '以下基于首次价格横盘固定分组，逐个移除双方的一只股票或一个信号日，然后重新做同日同年龄匹配。范围是影响诊断，不是置信区间。', '',
              '| 移除方式 | 正比例差范围（点） | 均值差范围（点） | MAE差范围（点） |',
              '| --- | --- | --- | --- |']
    for k, r in removal_results.items():
        fields=['positive_pp','mean_pp','mae_pp']
        vals=['无' if r['ranges'][f] is None else ' 至 '.join(fmt(v) for v in r['ranges'][f]) for f in fields]
        lines.append(f"| {'每次一只股票' if k=='symbol' else '每次一天'} | {' | '.join(vals)} |")
    valid_gaps = [r for r in block_gaps if r['dates']]
    for field in ['positive_pp', 'mean_pp', 'mae_pp']:
        if valid_gaps:
            lines.append(f"\n逐个移除连续5个交易日的信号：{field}范围 {min(r[field] for r in valid_gaps):+.2f} 至 {max(r[field] for r in valid_gaps):+.2f} 点。")
    lines += ['', '## 数据扩样核查', '',
              '项目日K及事件的最新日期仍为2026-09-04；7/20以前事件只有5/13、5/14、5/15、6/2四个零散日期，不能构成连续近5日候选池。另检查配置的本地hithink-finance DuckDB，v_daily及raw_adjustment_events均为0行，因此未取得额外可直接使用的本地独立样本。未把缺失日期补零或用旧样本冒充新验证。', '',
              'hithink只读查询：SELECT source, rows, symbols, earliest, latest（实际SQL及返回见data_expansion_check.json）。后续若引入完整长历史，需要重新冻结全池输入，并预先约定观察窗口；本轮没有批量初始化或同步新行情库。', '',
              '## 如何复核', '',
              '分组明细：consolidation_first_box_assignments.csv；所有检查、探索区间和逐次移除明细：consolidation_validation.json。成熟/完整数量保存在JSON。MAE差为五日最低价相对D+1开盘的变化之差，正数表示缩量组观察到的不利极值较小。', '',
              '匹配先排除双方重叠股票，按日期和距涨停天数分层，分层在日期内等权再对日期等权；区间为5日循环移动块重采样2,000次，种子20260907。不同检查存在多重比较，区间未做相应校正；小样本中一致的符号仍不代表可泛化。', '',
              '## 下一阶段验证约定（方案，未运行）', '',
              '停止在当前26日窗口继续寻找最优阈值。保留三个明确分开的观察组：首次价格横盘就缩量、首次横盘未缩量、横盘后首次转为缩量；后者的时钟必须在转为缩量当日重新开始。温和回撤作为外部形态参照，深回撤只作环境敏感性对照。', '',
              '取得覆盖全候选池的新历史或未来连续数据后，先登记数据窗口、形态版本、首次确认时间、来源和缺失字段，再统一追踪所有组。进入正式结论比较前，沿用至少100个完整事件、60个信号日、成熟结果完整率95%的初步数据质量门槛；门槛不是统计保证。至少检查多个时段、相同锚点年龄、费用情景、公司行动及来源单位，不能用同一旧窗口重复探索替代独立验证。', '',
              '当前来源指纹：'+original['input_sha256']+'。规则冻结不代表过去的数据当时已入库，本轮结果均不可标记为live。']
    (directory/'consolidation_validation.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')
    print(json.dumps({'summary': summaries, 'assignment_counts':payload['assignment_counts'],
                      'influence':{k:v['ranges'] for k,v in removal_results.items()},
                      'gap_ranges':{k:[min(r[k] for r in valid_gaps),max(r[k] for r in valid_gaps)] for k in ['positive_pp','mean_pp','mae_pp']}}, ensure_ascii=True))
    return payload


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,default=Path('output/research/limit-up-paths-20260907'))
    parser.add_argument('--db',type=Path,default=Path('backend/data/limituplab.sqlite'))
    args=parser.parse_args()
    analyze(args.input,args.db)
