import csv, io
from collections import defaultdict
from app.errors import BusinessError
from app.reasons import reject

FIELDS=['id', 'subject', 'visit', 'at', 'enrolled', 'kind', 'parent', 'value', 'unit', 'protocol', 'revision']
SOURCE_FIELD='source'
SOURCE_VALUES=('ORIGINAL','BACKFILL')
# 采信策略（唯一结论）：原始采血为正式首次记录，补录一律不采信。
OFFICIAL_SOURCE='ORIGINAL'

def read_source(state, fields, aliases):
    raw=state['raw_bytes']
    if not raw: raise BusinessError('EMPTY_FILE','请选择非空文件')
    if not raw.strip(b'\xef\xbb\xbf\r\n \t'): raise BusinessError('BLANK_FILE','文件只有空行')
    text=None
    encoding=None
    for enc in ('utf-8-sig','gb18030'):
        try: text=raw.decode(enc);encoding=enc;break
        except UnicodeDecodeError: continue
    if text is None: raise BusinessError('BAD_ENCODING','文件编码无法识别')
    stream=csv.reader(io.StringIO(text,newline=''),strict=True)
    try:
        original=next(stream)
        header=[aliases.get(v.strip(),v.strip()) for v in original]
        with_source=header==fields+[SOURCE_FIELD]
        if header!=fields and not with_source: raise BusinessError('BAD_HEADER','字段或顺序不匹配')
        state['counts']={'input':0,'blank':0,'repeated_header':0,'duplicate':0,'backfill_superseded':0,'backfill_without_original':0,'original_conflict':0}
        records=[]
        for cells in stream:
            line=stream.line_num
            state['counts']['input']+=1
            if not cells or not any(v.strip() for v in cells):
                state['counts']['blank']+=1;continue
            if cells==original:
                state['counts']['repeated_header']+=1;continue
            row=dict(zip(header,cells));row['_line']=line
            if len(cells)!=len(header): reject(state,row,'COLUMN_COUNT',f'需要{len(header)}列，实际{len(cells)}列');continue
            if not row.get('id','').strip(): reject(state,row,'MISSING_ID','编号不能为空');continue
            row['id']=row['id'].strip()
            source=(row.pop(SOURCE_FIELD,None) or '').strip() or OFFICIAL_SOURCE
            if source not in SOURCE_VALUES:
                reject(state,row,'BAD_SOURCE',f'来源仅支持 {"/".join(SOURCE_VALUES)}，实际 {source}');continue
            row['source']=source
            records.append(row)
    except csv.Error:
        raise BusinessError('BROKEN_CSV','CSV引号或末行损坏')
    if not state['counts']['input']: raise BusinessError('HEADER_ONLY','文件只有表头')
    by_id=defaultdict(list)
    for row in records: by_id[row['id']].append(row)
    selected=[]
    for key,rows in by_id.items():
        identities={tuple((k,v) for k,v in r.items() if k!='_line') for r in rows}
        if len(identities)>1:
            for row in rows: reject(state,row,'DUPLICATE_CONFLICT','同一编号存在不同原值')
        else:
            selected.append(rows[0]);state['counts']['duplicate']+=len(rows)-1
    selected=resolve_first_sampling(state,selected)
    state['records']=selected
    state['encoding']=encoding
    state['views'][state['context']['keys'][0]]={'rows':[dict(r) for r in selected], 'metrics':{'采信行':len(selected),'隔离行':len(state['quarantine']),'重复行':state['counts']['duplicate'],'未采信补录':state['counts']['backfill_superseded'],'无原始冲突':state['counts']['backfill_without_original']+state['counts']['original_conflict']},'title':'原始证据'}


def resolve_first_sampling(state, rows):
    """同一受试者+同一访视的首次采样冲突：保留原始采血，隔离补录，结论对下游唯一。"""
    groups=defaultdict(list)
    for row in rows:
        if row.get('kind')=='PRIMARY':
            groups[(row['subject'],row['visit'])].append(row)
    rejected=set()
    conflicts=[]
    for (subject,visit),members in sorted(groups.items()):
        originals=sorted((r for r in members if r['source']=='ORIGINAL'),key=lambda r:r['_line'])
        backfills=sorted((r for r in members if r['source']=='BACKFILL'),key=lambda r:r['_line'])
        # 仅当出现显式补录标记时启用首采冲突裁决；无来源列的历史文件维持原下游校验路径。
        if not backfills:
            continue
        if len(members)==1 and originals:
            continue
        if len(originals)==1:
            winner=originals[0]
            for row in backfills:
                state['counts']['backfill_superseded']+=1
                conflicts.append({'subject':subject,'visit':visit,'official_id':winner['id'],'rejected_id':row['id'],'code':'BACKFILL_SUPERSEDED','detail':'补录与原始采血冲突，保留原始采血为正式首次记录，补录不采信'})
                reject(state,row,'BACKFILL_SUPERSEDED',f"原始采血 {winner['id']}（{winner['at']} / 入组 {winner['enrolled']}）为正式首次记录，补录不采信")
                rejected.add(row['id'])
            continue
        if not originals:
            for row in backfills:
                state['counts']['backfill_without_original']+=1
                conflicts.append({'subject':subject,'visit':visit,'official_id':'','rejected_id':row['id'],'code':'BACKFILL_NO_ORIGINAL','detail':'仅有补录、缺少原始采血，不产生正式首次记录'})
                reject(state,row,'BACKFILL_NO_ORIGINAL','缺少原始采血，补录不能作为正式首次记录')
                rejected.add(row['id'])
            continue
        for row in members:
            state['counts']['original_conflict']+=1
            conflicts.append({'subject':subject,'visit':visit,'official_id':'','rejected_id':row['id'],'code':'ORIGINAL_CONFLICT','detail':'同一访视存在多条冲突的原始采血，无法确定唯一正式记录'})
            reject(state,row,'ORIGINAL_CONFLICT','多条原始采血互相冲突，全部隔离待人工核查')
            rejected.add(row['id'])
    state['source_conflicts']=conflicts
    official=[]
    for row in rows:
        if row['id'] in rejected:
            continue
        role=row.get('record_role')
        if role is None and row.get('kind')=='PRIMARY':
            role='OFFICIAL_ORIGINAL'
        official.append({**row,'record_role':role} if role else dict(row))
    return official
