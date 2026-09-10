"""Export auditable recent positive/negative examples of the unchanged rule."""
import argparse
import csv
import hashlib
import importlib.util
import json
from pathlib import Path
from statistics import mean

spec = importlib.util.spec_from_file_location('_case_focus', Path(__file__).with_name('research_pullback_consolidation.py'))
focus = importlib.util.module_from_spec(spec)
spec.loader.exec_module(focus)


# Assign the export's historical outcome category from the observed measurements.
def label(gross):
    value = focus.net(gross)
    return 'positive' if value > 1e-10 else 'negative' if value < -1e-10 else 'flat'


# Select recent case rows for the bounded review export.
def select_recent(rows, outcome_label, count=3):
    eligible = [r for r in rows if r['outcome_status'] == 'complete' and label(r['r5']) == outcome_label]
    # No best/worst return sorting. Date descending, symbol ascending for ties.
    # Python's stable second sort preserves the first sort within equal dates.
    # The key compares symbol.
    eligible.sort(key=lambda r: r['symbol'])
    # The key compares signal date. reverse=True reverses the resulting order.
    eligible.sort(key=lambda r: r['signal_date'], reverse=True)
    return eligible[:count]


# Assemble a case's anchor, shape and observed-outcome fields for the export.
def case_facts(row, bars, calendar):
    a, t = calendar.index(row['anchor_date']), calendar.index(row['signal_date'])
    window = [bars[(row['symbol'], d)] for d in calendar[a:t+1]]
    post = window[1:]
    low, high = min(b['low'] for b in post), max(b['high'] for b in post)
    result = {**row, 'age': int(row['age']), 'anchor_close': window[0]['close'],
              'signal_close': window[-1]['close'], 'range_low': low, 'range_high': high,
              'range_pct': (high/low-1)*100,
              'volume_ratio': mean(b['volume'] for b in post)/window[0]['volume'],
              'relative_close_pct': (window[-1]['close']/window[0]['close']-1)*100,
              'window_source': sorted({b['source'] for b in window}),
              'available_premarket_date': calendar[t+1] if t+1 < len(calendar) else None,
              'label': label(row['r5']) if row['outcome_status']=='complete' else 'unknown',
              'net5_pct': focus.net(row['r5']) if row['outcome_status']=='complete' else None}
    assert focus.price_box(window)
    assert result['volume_ratio'] <= .75
    assert len(result['window_source']) == 1
    result['daily_bars'] = [{**b, 'trade_date': calendar[a+i],
                             'role': 'anchor' if i == 0 else 'signal' if a+i == t else 'formation'}
                            for i,b in enumerate(window)]
    if row['outcome_status']=='complete':
        future = [bars[(row['symbol'],d)] for d in calendar[t+1:t+6]]
        assert len(future)==5
        result['reference_open'] = future[0]['open']
        result['end_close'] = future[-1]['close']
        assert abs((future[-1]['close']/future[0]['open']-1)*100-float(row['r5'])) < 1e-8
        result['daily_bars'] += [{**b,'trade_date':calendar[t+1+i],'role':f'D+{i+1}'} for i,b in enumerate(future)]
    return result


# Read the frozen research cohort and write the selected case evidence exports.
def analyze(directory, db):
    original = json.loads((directory/'comparison.json').read_text(encoding='utf-8'))
    bars = focus.load_frozen_bars(db, original)
    calendar = sorted({d for s,d in bars})
    with (directory/'signals.csv').open(encoding='utf-8-sig',newline='') as f:
        rows = [r for r in csv.DictReader(f) if r['strategy']=='consolidation']
    facts = [case_facts(r,bars,calendar) for r in rows]
    selected = select_recent(rows,'positive') + select_recent(rows,'negative')
    keys = {(r['symbol'],r['signal_date']) for r in selected}
    # The key compares `r['label'] != 'positive'`, then `int(r['signal_date'].replace('-', ''))`
    # (negated for descending order), then symbol.
    result = {'rule_version':'consolidation_research_v0.1', 'data_end':original['inventory']['data_end'],
              'input_sha256':original['input_sha256'],
              'script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'dependency_sha256':hashlib.sha256(Path(focus.__file__).read_bytes()).hexdigest(),
              'selection_rule':'Latest 3 complete positive and latest 3 complete negative cases; date descending, symbol ascending on ties; net5 uses 0.3% round-trip friction.',
              'summary':{k:sum(r['label']==k for r in facts) for k in ['positive','negative','flat','unknown']},
              'selected_cases':sorted([r for r in facts if (r['symbol'],r['signal_date']) in keys],
                                      key=lambda r:(r['label']!='positive',-int(r['signal_date'].replace('-','')),r['symbol'])),
              'pending_cases':[r for r in facts if r['label']=='unknown'], 'all_cases':facts}
    (directory/'consolidation_case_evidence.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'summary':result['summary'],'selected_cases':[{k:r[k] for k in ['symbol','name','signal_date','available_premarket_date','range_pct','volume_ratio','relative_close_pct','reference_open','end_close','net5_pct','label']} for r in result['selected_cases']]},ensure_ascii=True))
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,default=Path('output/research/limit-up-paths-20260907'))
    parser.add_argument('--db',type=Path,default=Path('backend/data/limituplab.sqlite'))
    args=parser.parse_args()
    analyze(args.input,args.db)
