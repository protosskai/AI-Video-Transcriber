"""Idempotent import from a read-only legacy snapshot. Never modifies source files."""
import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path
from .store import Store, now


def migrate(source, root):
    source = Path(source)
    records = json.loads((source / 'tasks.json').read_text(encoding='utf-8'))
    store = Store(root)
    backups = store.root / 'legacy-backups'
    backups.mkdir(exist_ok=True)
    digest = hashlib.sha256((source / 'tasks.json').read_bytes()).hexdigest()[:16]
    shutil.copy2(source / 'tasks.json', backups / f'tasks-{digest}.json')
    imported, missing = 0, []
    for old_id, old in records.items():
        if not isinstance(old, dict):
            continue
        jid = str(old_id)
        if not re.fullmatch(r'[\w-]{1,100}', jid) or store.get(jid):
            continue
        result = {k: old[k] for k in ('script','summary','translation','no_speech','detected_language','summary_language') if old.get(k) is not None}
        for kind, field in [('raw','raw_script_file'),('script','script_path'),('summary','summary_path'),('translation','translation_path')]:
            filename = old.get(field)
            if filename:
                path = source / Path(filename).name
                if path.is_file() and path.resolve().parent == source.resolve():
                    result.setdefault(kind, path.read_text(encoding='utf-8'))
                elif not result.get(kind):
                    missing.append({'id': jid, 'kind': kind})
        stamp = now()
        status = {'completed':'completed','error':'failed'}.get(old.get('status'), 'interrupted')
        title = str(old.get('video_title') or '历史任务')[:300]
        request = {'kind':'legacy', 'url': old.get('url',''), 'language': old.get('summary_language','')}
        with store.connect() as c:
            c.execute("""INSERT OR IGNORE INTO jobs(id,title,status,stage,created_at,updated_at,finished_at,request,result,error,imported)
                VALUES (?,?,?,?,?,?,?,?,?,?,1)""", (jid,title,status,'从旧版本导入',stamp,stamp,stamp,json.dumps(request),json.dumps(result,ensure_ascii=False), '旧任务未完成，请重新提交' if status=='interrupted' else None))
            c.execute('INSERT INTO events(job_id,at,status,stage) VALUES (?,?,?,?)', (jid,stamp,status,'历史记录导入，原始创建时间未知'))
        imported += 1
    return {'imported': imported, 'missing': missing, 'backup': str(backups / f'tasks-{digest}.json')}


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--source', required=True)
    p.add_argument('--data', required=True)
    args = p.parse_args()
    print(json.dumps(migrate(args.source, args.data), ensure_ascii=False))
