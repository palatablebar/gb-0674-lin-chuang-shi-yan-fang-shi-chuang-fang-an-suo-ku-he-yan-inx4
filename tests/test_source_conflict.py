"""原始采血 vs 补录冲突：采信策略、统一结论、追溯、幂等与上游失效。"""
from app.engine import evaluate
from app import store
from app.config import ROOT
from copy import deepcopy

CONFLICT_HEADER='id,subject,visit,at,enrolled,kind,parent,value,unit,protocol,revision,source'

def conflict():
    return evaluate({'dataset':'conflict'})

def test_original_is_official_backfill_quarantined():
    s=conflict()
    official={r['id']:r for r in s['records']}
    assert '00001-V1' in official and official['00001-V1']['record_role']=='OFFICIAL_ORIGINAL'
    assert '00001-V1-B' not in official
    q={x['id']:x for x in s['quarantine']}
    assert q['00001-V1-B']['code']=='BACKFILL_SUPERSEDED'
    assert q['00001-V1-B']['raw']['at']=='2026-10-08T08:00:00+08:00'  # 原值完整留痕

def test_window_uses_original_not_whitewashed():
    # 原始 day11 窗外；补录 day7 窗内。结论必须保留窗外，不得被补录漂白。
    s=conflict()
    win={r['id']:r for r in s['windows']}
    assert win['00001-V1']['code']=='LATE' and win['00001-V1']['day']==11
    assert '00001-V1-B' not in win
    assert any(d['id']=='00001-V1' and d['code']=='WINDOW_LATE' for d in s['deviations'])

def test_enrollment_conflict_keeps_original_enrollment():
    # 补录改写入组日期同样不采信，入组基准仍以原始记录/受试者主数据为准。
    s=conflict()
    assert not any(r['id']=='00002-V1-B' for r in s['records'])
    assert any(q['id']=='00002-V1-B' and q['code']=='BACKFILL_SUPERSEDED' for q in s['quarantine'])

def test_backfill_without_original_blocks_visit():
    s=conflict()
    assert any(q['id']=='00020-V1' and q['code']=='BACKFILL_NO_ORIGINAL' for q in s['quarantine'])
    assert any(d['subject']=='00020' and d['visit']=='V1' and d['code']=='MISSING_PRIMARY' for d in s['deviations'])

def test_lock_blocked_and_reasons_traceable():
    s=conflict()
    assert s['balance']['status']=='待处理'
    codes={(r['code'],r['record_id']) for r in s['balance']['block_reasons']}
    assert ('BACKFILL_SUPERSEDED','00001-V1-B') in codes
    assert ('BACKFILL_NO_ORIGINAL','00020-V1') in codes
    assert ('WINDOW_LATE','00001-V1') in codes
    assert s['archive']['status']=='待处理'

def test_row_conservation_with_sources():
    s=conflict()
    c=s['counts']
    explained=len(s['records'])+len(s['quarantine'])+c['blank']+c['duplicate']+c['repeated_header']
    assert c['input']==explained
    assert c['backfill_superseded']==2 and c['backfill_without_original']==1

def test_legacy_11_column_files_unchanged():
    # 无 source 列的历史文件：默认 ORIGINAL，不触发补录裁决，走原有下游校验。
    for name in ('dirty','clean','legacy'):
        s=evaluate({'dataset':name})
        assert all(r.get('source','ORIGINAL')=='ORIGINAL' for r in s['records'])
        assert not s.get('source_conflicts')

def test_default_source_when_column_absent():
    text=(ROOT/'sample_data'/'clean.csv').read_text()
    s=evaluate({'csv_text':text})
    assert s['records'][0]['source']=='ORIGINAL'

def test_bad_source_rejected():
    row='X1,00001,V1,2026-10-08T08:00:00+08:00,2026-10-01T00:00:00+08:00,PRIMARY,,1.2,g/L,auto,1,MANUAL'
    s=evaluate({'csv_text':CONFLICT_HEADER+'\n'+row+'\n'})
    assert any(q['id']=='X1' and q['code']=='BAD_SOURCE' for q in s['quarantine'])

def test_original_conflict_quarantines_all():
    rows=[
      'A1,00007,V1,2026-10-08T08:00:00+08:00,2026-10-01T00:00:00+08:00,PRIMARY,,1.2,g/L,auto,1,ORIGINAL',
      'A2,00007,V1,2026-10-09T08:00:00+08:00,2026-10-01T00:00:00+08:00,PRIMARY,,1.2,g/L,auto,1,ORIGINAL',
      'A3,00007,V1,2026-10-10T08:00:00+08:00,2026-10-01T00:00:00+08:00,PRIMARY,,1.2,g/L,auto,1,BACKFILL',
    ]
    s=evaluate({'csv_text':CONFLICT_HEADER+'\n'+'\n'.join(rows)+'\n'})
    assert {q['code'] for q in s['quarantine']}=={'ORIGINAL_CONFLICT'}
    assert {q['id'] for q in s['quarantine']}=={'A1','A2','A3'}
    assert not any(r['subject']=='00007' for r in s['records'])

def test_retest_still_adjucated_against_official_primary():
    # 复检挂在正式原始记录上，补录被隔离不影响复检裁决链。
    s=conflict()
    selected={r['primary_id']:r for r in s['selected_results']}
    assert '00001-V1' in selected

def test_rerun_same_package_no_new_accepted(tmp_path):
    p=str(tmp_path/'c.sqlite')
    a=store.save(p,conflict())
    b=store.save(p,evaluate({'dataset':'conflict'}))
    assert a['manifest']['run_id']==b['manifest']['run_id']
    assert len(store.history(p))==1
    assert len(a['records'])==len(b['records'])

def test_upstream_update_invalidates_old_lock(tmp_path):
    p=str(tmp_path/'c.sqlite')
    old=store.save(p,conflict())
    base=(ROOT/'sample_data'/'conflict.csv').read_text()
    updated=base.replace('2026-10-12T08:00:00+08:00,2026-10-01T00:00:00+08:00,PRIMARY,,1.2,g/L,auto,1,ORIGINAL',
                         '2026-10-09T08:00:00+08:00,2026-10-01T00:00:00+08:00,PRIMARY,,1.2,g/L,auto,1,ORIGINAL')
    new=store.save(p,evaluate({'csv_text':updated}))
    assert new['manifest']['run_id']!=old['manifest']['run_id']
    assert new['lineage_key']==old['lineage_key']
    stale=store.get(p,old['manifest']['run_id'])
    assert stale['status']=='已失效' and stale['superseded_by']==new['manifest']['run_id']
    assert new['manifest']['replaces']==old['manifest']['run_id']
    import pytest
    from app.errors import BusinessError
    with pytest.raises(BusinessError) as e:
        store.review(p,old['manifest']['run_id'],1)
    assert e.value.code=='SUPERSEDED_RUN'

def test_unrelated_package_does_not_invalidate(tmp_path):
    p=str(tmp_path/'c.sqlite')
    first=store.save(p,conflict())
    store.save(p,evaluate({'dataset':'clean'}))
    again=store.get(p,first['manifest']['run_id'])
    assert again['status']!='已失效' and not again['superseded_by']
