from importlib import import_module
from pathlib import Path
from copy import deepcopy
import json, time, base64, hashlib
from app.config import ROOT, MODULES, MAX_BYTES
from app.errors import BusinessError
from app.manifest import digest, make_manifest

DATASETS={'dirty','clean','legacy','conflict'}

def lineage_key(records):
    # 同一冲突包=同一组受试者-访视首次记录身份；上游增删改任一记录都会改变此键。
    keys=sorted({(r.get('subject',''),r.get('visit',''),r.get('kind',''),r.get('id','')) for r in records})
    return hashlib.sha256(json.dumps(keys,ensure_ascii=False).encode()).hexdigest()[:16]

def evaluate(payload=None, until=7, snapshot=None):
    payload=payload or {}
    dataset=payload.get('dataset','dirty')
    if dataset not in DATASETS: raise BusinessError('BAD_DATASET','样例名称无效')
    if not isinstance(payload.get('overrides',{}),dict): raise BusinessError('BAD_CONTEXT','参数须为对象')
    raw=(base64.b64decode(snapshot['bytes_b64'],validate=True) if snapshot is not None else (str(payload['csv_text']).encode() if 'csv_text' in payload else (ROOT/'sample_data'/f'{dataset}.csv').read_bytes()))
    if len(raw)>MAX_BYTES: raise BusinessError('TOO_LARGE','文件超过2MB',413)
    context=deepcopy(snapshot['context']) if snapshot is not None else json.loads((ROOT/'sample_data/context.json').read_text())
    context.update(deepcopy(payload.get('overrides',{})))
    start=time.perf_counter()
    state={'raw_bytes':raw,'context':context,'records':[],'quarantine':[], 'counts':{},'views':{},'source':dataset}
    for index,key in enumerate(MODULES,1):
        if index>until: break
        import_module(f'app.modules.{key}.rules').apply(state)
    state['lineage_key']=lineage_key(state['records']+[{'id':q.get('id',''),'subject':(q.get('raw') or {}).get('subject',''),'visit':(q.get('raw') or {}).get('visit',''),'kind':(q.get('raw') or {}).get('kind','')} for q in state['quarantine']])
    state['input_snapshot']={'bytes_b64':base64.b64encode(raw).decode(),'context':deepcopy(context)}
    state.pop('raw_bytes',None)
    state['manifest']=make_manifest(digest(raw,context),dataset,time.perf_counter()-start,state['lineage_key'])
    return state
