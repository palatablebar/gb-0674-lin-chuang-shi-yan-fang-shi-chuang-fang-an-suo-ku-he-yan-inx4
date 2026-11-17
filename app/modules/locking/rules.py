def decide(state):
    c=state['counts'];explained=len(state['records'])+len(state['quarantine'])+c['blank']+c['duplicate']+c['repeated_header'];blocked=len(state['deviations'])+len(state['retest_issues'])+len(state['quarantine'])
    reasons=[]
    for d in state['deviations']:
        reasons.append({'category':'访视偏离','code':d['code'],'subject':d.get('subject',''),'visit':d.get('visit',''),'record_id':d.get('id',''),'detail':_DEVIATION_TEXT.get(d['code'],d['code'])})
    for r in state['retest_issues']:
        reasons.append({'category':'复检问题','code':r['code'],'subject':r.get('subject',''),'visit':r.get('visit',''),'record_id':r.get('id',''),'detail':_RETEST_TEXT.get(r['code'],r['code'])})
    for q in state['quarantine']:
        raw=q.get('raw') or {}
        reasons.append({'category':'采信隔离','code':q.get('code',''),'subject':raw.get('subject',''),'visit':raw.get('visit',''),'record_id':q.get('id',''),'detail':q.get('detail','')})
    if c['input']!=explained:
        reasons.append({'category':'行守恒','code':'ROW_BALANCE_BROKEN','subject':'','visit':'','record_id':'','detail':f"输入{c['input']}行与去向合计{explained}行不一致"})
    return {'input':c['input'],'accepted':len(state['records']),'quarantined':len(state['quarantine']),'row_balanced':c['input']==explained,'completed':len(state['completed']),'expected':len(state['context']['subjects'])*len(state['context']['required_visits']),'deviations':len(state['deviations']),'retest_issues':len(state['retest_issues']),'blocked':blocked,'block_reasons':reasons,'status':'可锁库' if not blocked and c['input']==explained else '待处理'}

_DEVIATION_TEXT={'MISSING_PRIMARY':'缺少正式首次采样记录，复检或补录均不能填补','DUPLICATE_PRIMARY':'同一访视存在多条正式首次记录','WINDOW_EARLY':'正式首次采样早于访视窗起始','WINDOW_LATE':'正式首次采样晚于访视窗末端（补录不得漂白为窗内）'}
_RETEST_TEXT={'RETEST_REFERENCE':'复检引用的首次记录不存在或类型不符','RETEST_WINDOW':'复检时刻或版本不满足规则','RETEST_VERSION_CONFLICT':'同最高版本复检冲突，保留原结果并阻断'}

def apply(state):
    state['balance']=decide(state);state['views']['locking']={'title':'锁库闸门','rows':[state['balance']],'metrics':{'应访视':state['balance']['expected'],'已完成':state['balance']['completed'],'阻断项':state['balance']['blocked']}}
