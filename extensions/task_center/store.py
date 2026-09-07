"""SQLite source of truth. No upstream imports and no secrets in job records."""
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def now():
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = self.root / "tasks.sqlite3"
        with self.connect() as c:
            c.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS jobs (
                  id TEXT PRIMARY KEY, title TEXT NOT NULL, status TEXT NOT NULL,
                  stage TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                  started_at TEXT, finished_at TEXT, request TEXT NOT NULL,
                  result TEXT NOT NULL DEFAULT '{}', error TEXT, retry_of TEXT,
                  imported INTEGER NOT NULL DEFAULT 0, request_key TEXT UNIQUE
                );
                CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status,created_at);
                CREATE TABLE IF NOT EXISTS events (
                  seq INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL,
                  at TEXT NOT NULL, status TEXT NOT NULL, stage TEXT NOT NULL
                );
            """)

    @contextmanager
    def connect(self):
        c = sqlite3.connect(self.db, timeout=10)
        c.row_factory = sqlite3.Row
        try:
            with c:
                yield c
        finally:
            c.close()

    def create(self, title, request, retry_of=None, request_key=None):
        jid, stamp = str(uuid.uuid4()), now()
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            if request_key:
                old = c.execute("SELECT id FROM jobs WHERE request_key=?", (request_key,)).fetchone()
                if old:
                    return old[0]
            c.execute("""INSERT INTO jobs
                (id,title,status,stage,created_at,updated_at,request,retry_of,request_key)
                VALUES (?,?, 'queued','等待执行',?,?,?,?,?)""",
                (jid, title[:300], stamp, stamp, json.dumps(request), retry_of, request_key))
            c.execute("INSERT INTO events(job_id,at,status,stage) VALUES (?,?,'queued','等待执行')", (jid, stamp))
        return jid

    def get(self, jid):
        with self.connect() as c:
            row = c.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
            if not row:
                return None
            d = dict(row)
            d['request'] = json.loads(d['request'])
            d['result'] = json.loads(d['result'])
            d['queue_position'] = c.execute("SELECT count(*) FROM jobs WHERE status='queued' AND (created_at < ? OR (created_at=? AND id<=?))", (d['created_at'], d['created_at'], jid)).fetchone()[0] if d['status'] == 'queued' else None
            d['events'] = [dict(x) for x in c.execute("SELECT at,status,stage FROM events WHERE job_id=? ORDER BY seq", (jid,))]
            return d

    def listing(self, query='', status='', page=1, size=30):
        where, args = ['1=1'], []
        if query:
            where.append('instr(lower(title),lower(?))>0')
            args.append(query)
        if status == 'active':
            where.append("status IN ('queued','running')")
        elif status == 'failed':
            where.append("status IN ('failed','interrupted')")
        elif status:
            where.append('status=?')
            args.append(status)
        clause = ' AND '.join(where)
        with self.connect() as c:
            total = c.execute(f'SELECT count(*) FROM jobs WHERE {clause}', args).fetchone()[0]
            ids = c.execute(f"SELECT id FROM jobs WHERE {clause} ORDER BY CASE WHEN status='running' THEN 0 WHEN status='queued' THEN 1 ELSE 2 END, CASE WHEN status='queued' THEN created_at END ASC, created_at DESC, id LIMIT ? OFFSET ?", (*args, size, (page-1)*size)).fetchall()
            counts = dict(c.execute('SELECT status,count(*) FROM jobs GROUP BY status').fetchall())
        return {'items': [self.get(x[0]) for x in ids], 'total': total, 'counts': counts, 'page': page, 'size': size}

    def claim(self):
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            if c.execute("SELECT 1 FROM jobs WHERE status='running'").fetchone():
                return None
            r = c.execute("SELECT id FROM jobs WHERE status='queued' ORDER BY created_at,id LIMIT 1").fetchone()
            if not r:
                return None
            stamp = now()
            c.execute("UPDATE jobs SET status='running',stage='准备执行',started_at=?,updated_at=? WHERE id=?", (stamp, stamp, r[0]))
            c.execute("INSERT INTO events(job_id,at,status,stage) VALUES (?,?,'running','准备执行')", (r[0], stamp))
        return self.get(r[0])

    def update(self, jid, status, stage, result=None, error=None, title=None):
        stamp = now()
        with self.connect() as c:
            row = c.execute('SELECT status,stage FROM jobs WHERE id=?', (jid,)).fetchone()
            if not row:
                raise KeyError(jid)
            if tuple(row) != (status, stage):
                c.execute('INSERT INTO events(job_id,at,status,stage) VALUES (?,?,?,?)', (jid, stamp, status, stage))
            c.execute("""UPDATE jobs SET status=?,stage=?,updated_at=?,error=?,
                finished_at=CASE WHEN ? IN ('completed','failed','interrupted') THEN COALESCE(finished_at,?) ELSE finished_at END,
                result=COALESCE(?,result),title=COALESCE(?,title) WHERE id=?""",
                (status, stage[:500], stamp, error, status, stamp, json.dumps(result, ensure_ascii=False) if result is not None else None, title[:300] if title else None, jid))

    def recover(self):
        with self.connect() as c:
            ids = [r[0] for r in c.execute("SELECT id FROM jobs WHERE status='running'")]
        for jid in ids:
            self.update(jid, 'interrupted', '执行器重启，任务已中断', error='请手动重试。不会自动重复调用模型。')
