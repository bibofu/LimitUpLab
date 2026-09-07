import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location('stress', Path(__file__).parents[1] / 'scripts/validate_consolidation_evidence.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def row(date, arm='other', status='complete', symbol='s'):
    return {'symbol': symbol, 'anchor_date': '0', 'signal_date': date, 'arm': arm,
            'outcome_status': status, 'age': date, 'board_height': 1,
            'anchor_to_signal_pct': 0, 'r5': 5, 'mae5': -3}


def test_arm_locks_before_future_shrinkage_or_completeness():
    rows = [row('2', status='missing_bar'), row('3', arm='shrink')]
    fixed = m.first_box(rows)
    assert len(fixed) == 1
    assert fixed[0]['arm'] == 'other'
    assert fixed[0]['outcome_status'] == 'missing_bar'


def test_unknown_initial_volume_cannot_be_reclassified_later():
    assert m.first_box([row('2', arm='unknown'), row('3', arm='shrink')])[0]['arm'] == 'unknown'


def test_missing_first_outcome_still_occupies_signal_spacing():
    rows = [row('2', status='missing_bar'), row('3'), row('8')]
    assert [r['signal_date'] for r in m.spaced(rows, [str(i) for i in range(10)])] == ['2', '8']


def test_feature_source_requires_full_anchor_to_signal_window():
    calendar = ['0', '1', '2']
    pool = [{**row('2'), 'all_tencent_history': 'True'}]
    bars = {('s', d): {'open': 10, 'close': 10, 'low': 9.9, 'high': 10.1,
                       'volume': 100 if d == '0' else 50, 'source': 'akshare.stock_zh_a_hist_tx'} for d in calendar}
    assert m.observations(pool, bars, calendar)[0]['arm'] == 'shrink'
    bars[('s', '0')]['source'] = 'mixed-legacy-source'
    result = m.observations(pool, bars, calendar)[0]
    assert result['arm'] == 'unknown'
    assert not result['feature_tencent']
    assert not result['both_tencent']
