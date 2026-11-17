import sqlite3, json
from pathlib import Path
from app.errors import BusinessError

def connect(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    con=sqlite3.connect(path,timeout=15)
    con.row_factory=sqlite3.Row
    con.execute('PRAGMA journal_mode=WAL')
    con.execute("CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, digest TEXT UNIQUE NOT NULL, result TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL DEFAULT '待复核')")
    cols={r[1] for r in con.execute('PRAGMA table_info(runs)')}
    if 'lineage_key' not in cols:
        con.execute("ALTER TABLE runs ADD COLUMN lineage_key TEXT")
    if 'superseded_by' not in cols:
        con.execute("ALTER TABLE runs ADD COLUMN superseded_by TEXT")
    con.commit()
    return con

def _row_to_result(row):
    result=json.loads(row['result']);result.update(revision=row['revision'],status=row['status'])
    result['superseded_by']=row['superseded_by']
    result['lineage_key']=row['lineage_key']
    if row['superseded_by']:
        result['status']='已失效'
        result.setdefault('manifest',{})['superseded_by']=row['superseded_by']
    return result

def save(path, result):
    run_id=result['manifest']['run_id'];lineage=result.get('lineage_key')
    with connect(path) as con:
        con.execute('INSERT OR IGNORE INTO runs(id,digest,result,lineage_key) VALUES(?,?,?,?)', (run_id,result['manifest']['sha256'],json.dumps(result,ensure_ascii=False),lineage))
        row=con.execute('SELECT * FROM runs WHERE id=?',(run_id,)).fetchone()
        # 上游更新：同一冲突包出现新批号时，旧批号锁库结论一律失效并指向新批。
        if lineage and not row['superseded_by']:
            older=con.execute("SELECT id FROM runs WHERE lineage_key=? AND id!=? ORDER BY rowid",(lineage,run_id)).fetchall()
            for old in older:
                con.execute('UPDATE runs SET superseded_by=? WHERE id=? AND superseded_by IS NULL',(run_id,old['id']))
                if old['id']!=run_id:
                    result.setdefault('manifest',{})['replaces']=old['id']
            if older:
                con.execute('UPDATE runs SET result=? WHERE id=?',(json.dumps(result,ensure_ascii=False),run_id))
    return get(path,run_id)

def get(path, run_id):
    with connect(path) as con: row=con.execute('SELECT * FROM runs WHERE id=?',(run_id,)).fetchone()
    if not row: raise BusinessError('NOT_FOUND','批次不存在',404)
    return _row_to_result(row)

def history(path):
    with connect(path) as con:
        return [{'id':r['id'],'revision':r['revision'],'status':'已失效' if r['superseded_by'] else r['status'],'lineage_key':r['lineage_key'],'superseded_by':r['superseded_by']} for r in con.execute('SELECT * FROM runs ORDER BY rowid DESC LIMIT 100')]

def review(path,run_id,revision):
    with connect(path) as con:
        target=con.execute('SELECT * FROM runs WHERE id=?',(run_id,)).fetchone()
        if not target: raise BusinessError('NOT_FOUND','批次不存在',404)
        if target['superseded_by']:
            raise BusinessError('SUPERSEDED_RUN',f"上游已更新，本批锁库结论已失效，请以新批 {target['superseded_by']} 重新复核",409)
        cur=con.execute('UPDATE runs SET revision=revision+1,status=? WHERE id=? AND revision=?',('已复核',run_id,revision))
        if cur.rowcount!=1: raise BusinessError('STALE_VERSION','复核版本已更新，请刷新',409)
    return get(path,run_id)
