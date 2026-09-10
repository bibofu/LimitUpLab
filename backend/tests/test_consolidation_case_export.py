import importlib.util
from pathlib import Path

spec=importlib.util.spec_from_file_location('cases',Path(__file__).parents[1]/'scripts/export_consolidation_cases.py')
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


# Regression scenario: case selection prioritizes recency not best return.
def test_case_selection_prioritizes_recency_not_best_return():
    rows=[{'symbol':'000002','signal_date':'2026-08-12','r5':100,'outcome_status':'complete'},
          {'symbol':'000001','signal_date':'2026-08-12','r5':1,'outcome_status':'complete'},
          {'symbol':'000003','signal_date':'2026-08-13','r5':2,'outcome_status':'complete'},
          {'symbol':'000004','signal_date':'2026-08-14','r5':'','outcome_status':'immature'}]
    selected=m.select_recent(rows,'positive',2)
    assert [r['symbol'] for r in selected]==['000003','000001']


# Regression scenario: label applies friction and does not label missing results.
def test_label_applies_friction_and_does_not_label_missing_results():
    assert m.label(.1)=='negative'
    assert m.label(1)=='positive'
    assert m.select_recent([{'outcome_status':'missing_bar','r5':''}],'negative')==[]
