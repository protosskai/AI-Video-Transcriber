"""Read-only source mount, SQLite backup into a disposable container, then migrate only the copy."""
import json
import sqlite3
import tempfile
from pathlib import Path
from extensions.task_center.store import Store
from extensions.task_center.library import Library

source=sqlite3.connect('file:/source/tasks.sqlite3?mode=ro',uri=True)
with tempfile.TemporaryDirectory(prefix='migration-check-') as tmp:
    target=sqlite3.connect(str(Path(tmp)/'tasks.sqlite3'))
    source.backup(target)
    before=target.execute('SELECT * FROM jobs ORDER BY id').fetchall()
    events=target.execute('SELECT * FROM events ORDER BY seq').fetchall()
    target.close(); source.close()
    store=Store(tmp); library=Library(store); library.sync()
    with store.connect() as c:
        assert [tuple(r) for r in c.execute('SELECT * FROM jobs ORDER BY id')]==before
        assert [tuple(r) for r in c.execute('SELECT * FROM events ORDER BY seq')]==events
    data=library.listing()
    for content in data['items']:
        for job in content['jobs']:
            assert library.resolve(job['id'])==content['id']
    print(json.dumps({'original_jobs':len(before),'contents':data['total'],
                      'unchanged_jobs_and_events':True,'legacy_links_resolve':True}))
