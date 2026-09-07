"""Read-only release checks; execute inside the production web container."""
import json
import sqlite3
import urllib.request

old=sqlite3.connect('file:/tmp/pre-redesign.sqlite3?mode=ro',uri=True)
new=sqlite3.connect('file:/data/task-center/tasks.sqlite3?mode=ro',uri=True)
rows=old.execute('SELECT * FROM jobs ORDER BY id').fetchall()
for row in rows:
    assert new.execute('SELECT * FROM jobs WHERE id=?',(row[0],)).fetchone()==row, 'Old job changed'
assert new.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
events=old.execute('SELECT * FROM events ORDER BY seq').fetchall()
for row in events:
    assert new.execute('SELECT * FROM events WHERE seq=?',(row[0],)).fetchone()==row
base='http://127.0.0.1:8000'
def get(path):
    with urllib.request.urlopen(base+path) as r:return r.read()
library=json.loads(get('/api/workbench/library'))
assert library['worker_online']
exports=0
for c in library['items']:
    detail=json.loads(get('/api/workbench/library/'+c['id']))
    for attempt in detail['attempts']:
        for kind in attempt['available']:
            assert get('/api/workbench/tasks/'+attempt['id']+'/export/'+kind+'?format=txt')
            exports+=1
assert b'transcribe.js' in get('/workbench/')
print(json.dumps({'preserved_jobs':len(rows),'preserved_events':len(events),'contents':library['total'],
                  'exports_checked':exports,'worker_online':True,'integrity':'ok'}))
