"""Additive content projection. Jobs and their original URLs/results remain intact."""
import hashlib
import json
import uuid
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from .store import now


class Conflict(ValueError):
    pass


def source_key(request):
    if request.get('kind') == 'url':
        p = urlsplit(request['url'].strip())
        host = (p.hostname or '').lower()
        query = parse_qs(p.query)
        if host in ('youtube.com', 'www.youtube.com', 'm.youtube.com', 'youtu.be'):
            video = p.path.strip('/') if host == 'youtu.be' else query.get('v', [''])[0]
            if not video and p.path.startswith(('/shorts/', '/live/')):
                video = p.path.split('/')[2]
            source = 'youtube:' + video if video else request['url']
        elif host in ('www.bilibili.com', 'bilibili.com') and p.path.startswith('/video/'):
            source = 'bilibili:' + p.path.strip('/') + ':' + query.get('p', ['1'])[0]
        else:
            query = {k: v for k, v in query.items() if not k.startswith('utm_')}
            source = urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path, urlencode(query, doseq=True), ''))
    elif request.get('sha256'):
        source = 'file:' + request['sha256']
    else:
        return None
    payload = [source, request.get('language', ''), bool(request.get('keep_video'))]
    return hashlib.sha256(json.dumps(payload).encode()).hexdigest()


class Library:
    def __init__(self, store):
        self.store = store
        with store.connect() as c:
            c.executescript('''
                CREATE TABLE IF NOT EXISTS contents (
                    id TEXT PRIMARY KEY, custom_title TEXT, source_key TEXT,
                    archived INTEGER NOT NULL DEFAULT 0, trashed INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS contents_source ON contents(source_key);
                CREATE TABLE IF NOT EXISTS content_jobs (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT UNIQUE NOT NULL, content_id TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS content_jobs_content ON content_jobs(content_id,seq);
                CREATE TABLE IF NOT EXISTS content_requests (
                    request_key TEXT PRIMARY KEY, content_id TEXT NOT NULL, job_id TEXT NOT NULL
                );
            ''')
        self.sync()

    def sync(self):
        """Import unmapped legacy/API jobs, following retry chains without merging unrelated history."""
        with self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            pending = {r['id']: dict(r) for r in c.execute('''SELECT j.* FROM jobs j
                LEFT JOIN content_jobs m ON m.job_id=j.id WHERE m.job_id IS NULL
                ORDER BY j.created_at,j.rowid''')}
            def attach(jid, visiting):
                r = pending[jid]
                parent = r['retry_of']
                if parent in pending and parent not in visiting:
                    attach(parent, visiting | {jid})
                row = c.execute('SELECT content_id FROM content_jobs WHERE job_id=?', (parent,)).fetchone()
                cid = row[0] if row else jid
                c.execute('INSERT OR IGNORE INTO contents(id,source_key,created_at,updated_at) VALUES (?,?,?,?)',
                          (cid, source_key(json.loads(r['request'])), r['created_at'], r['updated_at']))
                c.execute('INSERT OR IGNORE INTO content_jobs(job_id,content_id) VALUES (?,?)', (jid, cid))
                pending.pop(jid, None)
            while pending:
                attach(next(iter(pending)), set())

    def resolve(self, identifier):
        with self.store.connect() as c:
            row = c.execute('SELECT id FROM contents WHERE id=?', (identifier,)).fetchone()
            if not row:
                row = c.execute('SELECT content_id FROM content_jobs WHERE job_id=?', (identifier,)).fetchone()
        return row[0] if row else None

    def get(self, identifier):
        cid = self.resolve(identifier)
        if not cid:
            return None
        with self.store.connect() as c:
            content = dict(c.execute('SELECT * FROM contents WHERE id=?', (cid,)).fetchone())
            ids = [r[0] for r in c.execute('SELECT job_id FROM content_jobs WHERE content_id=? ORDER BY seq DESC', (cid,))]
        jobs = [self.store.get(jid) for jid in ids]
        content['jobs'] = jobs
        content['title'] = content['custom_title'] or next((j['title'] for j in jobs if j['status'] == 'completed'), jobs[0]['title'])
        return content

    def listing(self, q='', status='', view='library', page=1, size=30):
        self.sync()
        with self.store.connect() as c:
            rows = c.execute('''SELECT c.id,j.status,j.created_at,j.title FROM contents c
                JOIN content_jobs m ON m.seq=(SELECT MAX(seq) FROM content_jobs WHERE content_id=c.id)
                JOIN jobs j ON j.id=m.job_id
                WHERE c.trashed=? AND (?='trash' OR c.archived=?)
                AND (?='' OR instr(lower(COALESCE(c.custom_title,j.title)),lower(?))>0
                    OR EXISTS(SELECT 1 FROM content_jobs cm JOIN jobs jj ON jj.id=cm.job_id
                              WHERE cm.content_id=c.id AND instr(lower(jj.title),lower(?))>0))
                ORDER BY CASE j.status WHEN 'running' THEN 0 WHEN 'queued' THEN 1 ELSE 2 END,
                         CASE WHEN j.status='queued' THEN j.created_at END ASC,
                         j.created_at DESC,c.id''',
                (int(view == 'trash'), view, int(view == 'archive'), q, q, q)).fetchall()
        def matches(r):
            if status == 'active':
                return r['status'] in ('queued', 'running')
            if status == 'failed':
                return r['status'] in ('failed', 'interrupted')
            return not status or r['status'] == status
        counts = {}
        for r in rows:
            counts[r['status']] = counts.get(r['status'], 0) + 1
        ids = [r['id'] for r in rows if matches(r)]
        return {'items': [self.get(cid) for cid in ids[(page-1)*size:page*size]],
                'total': len(ids), 'counts': counts, 'page': page, 'size': size}

    def enqueue(self, title, request, request_key, content_id=None):
        """One transaction for deduplication, active-run check, job and content association."""
        stamp = now()
        key = source_key(request)
        with self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            receipt = c.execute('SELECT content_id,job_id FROM content_requests WHERE request_key=?', (request_key,)).fetchone()
            if receipt:
                if content_id and content_id != receipt[0]:
                    raise Conflict('请求标识已用于其他内容，请重新发起操作')
                return receipt[0], receipt[1], True
            def remember(cid, jid, reused):
                c.execute('INSERT INTO content_requests VALUES (?,?,?)', (request_key, cid, jid))
                return cid, jid, reused
            existing = c.execute('''SELECT m.content_id,j.id FROM jobs j JOIN content_jobs m ON m.job_id=j.id
                                  WHERE j.request_key=?''', (request_key,)).fetchone()
            if existing:
                return remember(existing[0], existing[1], True)
            if content_id:
                content = c.execute('SELECT * FROM contents WHERE id=?', (content_id,)).fetchone()
                if not content or content['trashed'] or content['archived']:
                    raise Conflict('请先恢复内容，再重新处理')
                active = c.execute('''SELECT j.id FROM jobs j JOIN content_jobs m ON m.job_id=j.id
                    WHERE m.content_id=? AND j.status IN ('queued','running')''', (content_id,)).fetchone()
                if active:
                    return remember(content_id, active[0], True)
                previous = c.execute('SELECT job_id FROM content_jobs WHERE content_id=? ORDER BY seq DESC LIMIT 1', (content_id,)).fetchone()[0]
            else:
                duplicate = c.execute('''SELECT c.id,m.job_id FROM contents c
                    JOIN content_jobs m ON m.seq=(SELECT MAX(seq) FROM content_jobs WHERE content_id=c.id)
                    WHERE c.source_key=? ORDER BY c.created_at DESC LIMIT 1''', (key,)).fetchone() if key else None
                if duplicate:
                    return remember(duplicate[0], duplicate[1], True)
                content_id, previous = str(uuid.uuid4()), None
                c.execute('INSERT INTO contents(id,source_key,created_at,updated_at) VALUES (?,?,?,?)',
                          (content_id, key, stamp, stamp))
            jid = str(uuid.uuid4())
            c.execute('''INSERT INTO jobs(id,title,status,stage,created_at,updated_at,request,retry_of,request_key)
                         VALUES (?,?,'queued','等待执行',?,?,?,?,?)''',
                      (jid, title[:300], stamp, stamp, json.dumps(request), previous, request_key))
            c.execute('INSERT INTO content_jobs(job_id,content_id) VALUES (?,?)', (jid, content_id))
            c.execute("INSERT INTO events(job_id,at,status,stage) VALUES (?,?,'queued','等待执行')", (jid, stamp))
            c.execute('UPDATE contents SET updated_at=? WHERE id=?', (stamp, content_id))
            return remember(content_id, jid, False)

    def change(self, cid, action, title=None):
        with self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            if not c.execute('SELECT 1 FROM contents WHERE id=?', (cid,)).fetchone():
                raise KeyError(cid)
            if action in ('trash', 'archive') and c.execute('''SELECT 1 FROM content_jobs m JOIN jobs j ON j.id=m.job_id
                        WHERE m.content_id=? AND j.status IN ('queued','running')''', (cid,)).fetchone():
                raise Conflict('内容仍在排队或执行，暂不能归档或移入回收站')
            statements = {
                'trash': 'trashed=1', 'restore': 'trashed=0,archived=0',
                'archive': 'archived=1', 'unarchive': 'archived=0',
                'rename': 'custom_title=?'
            }
            clause = statements[action]
            params = [title.strip()[:300]] if action == 'rename' else []
            c.execute(f'UPDATE contents SET {clause},updated_at=? WHERE id=?', (*params, now(), cid))

    def cancel(self, cid):
        with self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row = c.execute('''SELECT j.id,j.status FROM content_jobs m JOIN jobs j ON j.id=m.job_id
                               WHERE m.content_id=? ORDER BY m.seq DESC LIMIT 1''', (cid,)).fetchone()
            if not row or row['status'] != 'queued':
                raise Conflict('只能取消排队中的执行；已开始的执行不会被强行中断')
            stamp = now()
            c.execute("UPDATE jobs SET status='cancelled',stage='用户取消排队',updated_at=?,finished_at=? WHERE id=?", (stamp, stamp, row['id']))
            c.execute("INSERT INTO events(job_id,at,status,stage) VALUES (?,?,'cancelled','用户取消排队')", (row['id'], stamp))
