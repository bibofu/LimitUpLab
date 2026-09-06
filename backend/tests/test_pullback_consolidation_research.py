import importlib.util
import sqlite3
from pathlib import Path

import pytest

spec=importlib.util.spec_from_file_location('focused',Path(__file__).parents[1]/'scripts/research_pullback_consolidation.py')
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def row(symbol, date='d', age='2', board='1', retreat=-4, r5=5):
    return {'symbol':symbol,'signal_date':date,'age':age,'board_height':board,
            'anchor_to_signal_pct':retreat,'r5':r5,'mae5':-3}


def test_matching_excludes_same_stock_from_both_sides():
    left=[row('shared'),row('left')]
    right=[row('shared',r5=-3),row('right',r5=-3),row('age1',age='1')]
    result=m.pair_rows(left,right)
    assert len(result)==1
    assert result[0]['left_symbols']==['left']
    assert result[0]['right_symbols']==['right']
    assert result[0]['positive_pp']==100


def test_board_and_retreat_controls_use_signal_attributes():
    left=[row('left')]
    right=[row('high',board='2'),row('deep',retreat=-12),row('ok',retreat=-5)]
    matched=m.pair_rows(left,right,same_board=True,retreat_tolerance=2)
    assert matched[0]['right_symbols']==['ok']
    assert not m.pair_rows(left,[row('deep',retreat=-12)],retreat_tolerance=2)


def test_date_weight_is_not_pair_count_weight():
    strata=[{'date':'a','left_n':1,'right_n':100,'positive_pp':100,'mean_pp':5,'mae_pp':2},
            {'date':'b','left_n':1,'right_n':1,'positive_pp':-100,'mean_pp':-5,'mae_pp':-2}]
    result=m.comparison(strata,['a','b'])
    assert result['positive_pp']==0
    assert result['mean_pp_ci95'] is None


def test_price_box_ablation_does_not_read_volume():
    bars=[{'close':10,'high':10.1,'low':9.9,'volume':None} for _ in range(3)]
    assert m.price_box(bars)
    assert not m.price_box(bars[:2])
    bars[-1]['low']=8
    assert not m.price_box(bars)


def test_changed_snapshot_refused_without_writing(tmp_path):
    db=tmp_path/'test.sqlite'
    with sqlite3.connect(db) as c:
        c.execute('CREATE TABLE t (value INTEGER)')
        c.execute('INSERT INTO t VALUES (1)')
    before=db.read_bytes()
    with pytest.raises(ValueError,match='differs from frozen'):
        m.load_frozen_bars(db,{'queries':['SELECT * FROM t','SELECT * FROM t'],'input_sha256':'incorrect'})
    assert db.read_bytes()==before
