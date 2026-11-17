import io, csv, json, zipfile, hashlib
from app.errors import BusinessError

def serialize(result):
    if not result.get('manifest') or not result.get('views'):
        raise BusinessError('INCOMPLETE_RUN','批次尚未完成')
    buffer=io.BytesIO()
    manifest={**result['manifest'],'revision':result.get('revision',1),'status':result.get('status','待复核'),
              'lineage_key':result.get('lineage_key'),'superseded_by':result.get('superseded_by')}
    evidence={'counts':result['counts'],'views':result['views'],'quarantine':result['quarantine'],
              'source_conflicts':result.get('source_conflicts',[]),
              'admission_policy':'同一访视原始采血与补录冲突时保留原始采血为正式首次记录，补录隔离不采信'}
    with zipfile.ZipFile(buffer,'w',compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('manifest.json',json.dumps(manifest,ensure_ascii=False,indent=2))
        archive.writestr('evidence.json',json.dumps(evidence,ensure_ascii=False,indent=2))
    return buffer.getvalue()
