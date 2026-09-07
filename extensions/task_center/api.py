"""Standalone workbench API; bind to loopback and publish ONLY behind Access."""
import asyncio
import fcntl
import hashlib
import json
import os
import re
import uuid
from pathlib import Path
from urllib.parse import urlsplit, quote

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from .store import Store
from .library import Library, Conflict

ROOT = Path(__file__).resolve().parents[2]
LANGUAGES = {'zh', 'en', 'ja', 'ko', 'es', 'fr', 'de', 'it', 'pt', 'ru', 'ar'}
EXTENSIONS = {'.txt', '.mp3', '.mp4', '.wav', '.m4a', '.webm', '.mkv', '.ogg', '.flac', '.mov'}
CONTENT = {'script', 'raw', 'summary', 'translation'}


def plain_text(markdown):
    text = re.sub(r'(?m)^\s{0,3}#{1,6}\s+', '', markdown)
    text = re.sub(r'\[([^\]]+)\]\([^)]*\)', r'\1', text)
    text = re.sub(r'(?m)^```[^\n]*\n?', '', text)
    text = text.replace('**', '').replace('__', '').replace('`', '')
    return text


def public(job, detail=False):
    result = {k: job[k] for k in ('id','title','status','stage','created_at','updated_at','started_at','finished_at','queue_position','error','retry_of','imported')}
    result['source'] = job['request'].get('url') or job['request'].get('name', '历史任务')
    result['language'] = job['request'].get('language', job['result'].get('summary_language', ''))
    result['can_retry'] = job['status'] in ('failed','interrupted') and job['request'].get('kind') in ('url','upload')
    result['available'] = [k for k in CONTENT if job['result'].get(k)]
    if detail:
        result['result'] = {k: v for k, v in job['result'].items() if k in CONTENT or k == 'no_speech'}
        result['events'] = job['events']
    return result


class URLJob(BaseModel):
    url: str = Field(min_length=1, max_length=4096)
    language: str = 'zh'
    keep_video: bool = False
    request_key: str = Field(min_length=8, max_length=128)


class ContentAction(BaseModel):
    action: str
    title: str = Field('', max_length=300)


class RetryContent(BaseModel):
    request_key: str = Field(min_length=8, max_length=128)


def create_app(root=None):
    store = Store(root or os.environ.get('TC_DATA_DIR', '/data/task-center'))
    library = Library(store)
    app = FastAPI(title='Transcriber Workbench', docs_url=None, redoc_url=None)
    app.state.store = store
    app.state.library = library
    static = ROOT / 'static' / 'workbench'
    app.mount('/workbench/assets', StaticFiles(directory=static), name='assets')

    @app.middleware('http')
    async def security(request: Request, call_next):
        # No CORS; reject browser cross-origin writes. Access is the outer auth gate.
        origin = request.headers.get('origin')
        if request.method not in ('GET','HEAD','OPTIONS') and origin:
            if urlsplit(origin).netloc != request.headers.get('host'):
                return Response('Cross-origin request rejected', status_code=403)
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'same-origin'
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        return response

    def require(jid):
        job = store.get(jid)
        if not job:
            raise HTTPException(404, '任务不存在')
        return job

    def validate_language(lang):
        if lang not in LANGUAGES:
            raise HTTPException(422, '不支持的结果语言')

    def worker_online():
        path = store.root / 'worker.lock'
        if not path.exists():
            return False
        with path.open('r') as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(handle, fcntl.LOCK_UN)
                return False
            except BlockingIOError:
                return True

    @app.get('/')
    def home():
        return RedirectResponse('/workbench/')

    @app.get('/workbench/')
    def workbench():
        return FileResponse(static / 'transcribe.html')

    @app.get('/health')
    def health():
        with store.connect() as c:
            c.execute('SELECT 1')
        return {'status': 'ok'}

    @app.get('/api/workbench/tasks')
    def listing(q: str = Query('', max_length=200), status: str = '', page: int = Query(1, ge=1), size: int = Query(30, ge=1, le=100)):
        data = store.listing(q, status, page, size)
        data['items'] = [public(x) for x in data['items']]
        data['worker_online'] = worker_online()
        return data

    @app.get('/api/workbench/tasks/{jid}')
    def detail(jid: str):
        return public(require(jid), True)

    @app.post('/api/workbench/tasks', status_code=202)
    def submit(body: URLJob, library_mode: bool = False):
        validate_language(body.language)
        url = body.url.strip()
        parts = urlsplit(url)
        if parts.scheme not in ('http', 'https') or not parts.hostname or parts.username or parts.password:
            raise HTTPException(422, '请输入不含账号密码的 HTTP/HTTPS 视频链接')
        if library_mode:
            library.sync()
            cid, jid, reused = library.enqueue(parts.hostname + ' / ' + (parts.path.rsplit('/', 1)[-1] or '视频'),
                {'kind': 'url', 'url': url, 'language': body.language, 'keep_video': body.keep_video}, body.request_key)
            return {'content_id': cid, 'job_id': jid, 'reused': reused}
        jid = store.create(parts.hostname + ' / ' + (parts.path.rsplit('/', 1)[-1] or '视频'),
                           {'kind': 'url', 'url': url, 'language': body.language, 'keep_video': body.keep_video}, request_key=body.request_key)
        return public(require(jid), True)

    @app.post('/api/workbench/uploads', status_code=202)
    async def upload(file: UploadFile = File(...), language: str = Form('zh'), request_key: str = Form(...), library_mode: bool = False):
        validate_language(language)
        if not 8 <= len(request_key) <= 128:
            raise HTTPException(422, '无效的请求标识')
        name = Path((file.filename or 'upload').replace('\\', '/')).name[:200]
        ext = Path(name).suffix.lower()
        if ext not in EXTENSIONS:
            raise HTTPException(422, '不支持的文件类型')
        uploads = store.root / 'uploads'
        uploads.mkdir(exist_ok=True)
        dest = uploads / (str(uuid.uuid4()) + ext)
        size, maximum = 0, int(os.getenv('UPLOAD_MAX_MB','200')) * 1024 * 1024
        digest = hashlib.sha256()
        try:
            with dest.open('xb') as handle:
                while chunk := await file.read(1024 * 1024):
                    size += len(chunk)
                    if size > maximum:
                        raise HTTPException(413, f'文件超过 {maximum // 1024 // 1024} MB 限制')
                    handle.write(chunk)
                    digest.update(chunk)
            if not size:
                raise HTTPException(422, '不能上传空文件')
            if library_mode:
                library.sync()
                cid, jid, reused = library.enqueue(Path(name).stem,
                    {'kind': 'upload', 'name': name, 'file': dest.name, 'language': language, 'sha256': digest.hexdigest()}, request_key)
                if store.get(jid)['request'].get('file') != dest.name:
                    dest.unlink()
                return {'content_id': cid, 'job_id': jid, 'reused': reused}
            jid = store.create(Path(name).stem, {'kind': 'upload', 'name': name, 'file': dest.name, 'language': language}, request_key=request_key)
            if require(jid)['request'].get('file') != dest.name:
                dest.unlink()  # Only the unused file from this duplicate request.
            return public(require(jid), True)
        except Exception:
            dest.unlink(missing_ok=True)
            raise
        finally:
            await file.close()

    @app.post('/api/workbench/tasks/{jid}/retry', status_code=202)
    def retry(jid: str, request_key: str = Query(..., min_length=8, max_length=128)):
        job = require(jid)
        if not public(job)['can_retry']:
            raise HTTPException(409, '只能重试失败或中断的任务；旧记录请重新提交来源')
        rid = store.create(job['title'], job['request'], retry_of=jid, request_key=request_key)
        return public(require(rid), True)

    def content_public(content, detail=False, run=None):
        jobs = content['jobs']
        latest = jobs[0]
        result = {k: content[k] for k in ('id', 'title', 'archived', 'trashed', 'created_at')}
        result.update(job=public(latest), attempt_count=len(jobs))
        if detail:
            selected = next((j for j in jobs if j['id'] == run), None) if run else None
            if run and not selected:
                raise HTTPException(404, '这次执行不属于当前内容')
            # Preserve previous useful results while a new attempt has none.
            reading = selected or next((j for j in jobs if any(j['result'].get(k) for k in CONTENT)), latest)
            result.update(job=public(latest, True), reading=public(reading, True),
                          attempts=[public(j) for j in jobs])
        return result

    def content_require(cid):
        library.sync()
        content = library.get(cid)
        if not content:
            raise HTTPException(404, '内容不存在')
        return content

    @app.get('/api/workbench/library')
    def content_listing(q: str = Query('', max_length=200), status: str = '', view: str = 'library',
                        page: int = Query(1, ge=1), size: int = Query(30, ge=1, le=100)):
        if view not in ('library', 'archive', 'trash', 'home'):
            raise HTTPException(422, '无效视图')
        data = library.listing(q, status, view, page, size)
        data['items'] = [content_public(c) for c in data['items']]
        data['worker_online'] = worker_online()
        return data

    @app.get('/api/workbench/library/{cid}')
    def content_detail(cid: str, run: str = ''):
        return content_public(content_require(cid), True, run or None)

    @app.post('/api/workbench/library/submit', status_code=202)
    def content_submit(body: URLJob):
        return submit(body, True)

    @app.post('/api/workbench/library/upload', status_code=202)
    async def content_upload(file: UploadFile = File(...), language: str = Form('zh'), request_key: str = Form(...)):
        return await upload(file, language, request_key, True)

    @app.post('/api/workbench/library/{cid}/retry', status_code=202)
    def content_retry(cid: str, body: RetryContent):
        content = content_require(cid)
        latest = content['jobs'][0]
        request = latest['request']
        if request.get('kind') not in ('url', 'upload'):
            raise HTTPException(409, '导入记录缺少处理参数，请重新提交来源')
        if request['kind'] == 'upload':
            name = request.get('file', '')
            if Path(name).name != name or not (store.root / 'uploads' / name).is_file():
                raise HTTPException(409, '原文件已缺失，请重新上传')
        try:
            cid, jid, reused = library.enqueue(content['title'], request, body.request_key, content['id'])
        except Conflict as exc:
            raise HTTPException(409, str(exc))
        return {'content_id': cid, 'job_id': jid, 'reused': reused}

    @app.post('/api/workbench/library/{cid}/action')
    def content_action(cid: str, body: ContentAction):
        content = content_require(cid)
        if body.action not in ('archive', 'unarchive', 'trash', 'restore', 'rename', 'cancel'):
            raise HTTPException(422, '不支持的操作')
        if body.action == 'rename' and not body.title.strip():
            raise HTTPException(422, '标题不能为空')
        try:
            if body.action == 'cancel':
                library.cancel(content['id'])
            else:
                library.change(content['id'], body.action, body.title)
        except Conflict as exc:
            raise HTTPException(409, str(exc))
        return content_public(library.get(content['id']), True)

    @app.get('/api/workbench/tasks/{jid}/export/{kind}')
    def export(jid: str, kind: str, format: str = 'md'):
        job = require(jid)
        if kind not in CONTENT or format not in ('md','txt'):
            raise HTTPException(400, '不支持的导出格式')
        text = job['result'].get(kind)
        if not text:
            raise HTTPException(404, '这个结果尚未生成')
        if format == 'txt':
            text = plain_text(text)
        name = re.sub(r'[^\w\- ]', '_', job['title'])[:80] or 'transcript'
        name = quote(f'{name}_{kind}.{format}')
        return Response(text, media_type='text/plain; charset=utf-8', headers={'Content-Disposition': f"attachment; filename*=UTF-8''{name}"})

    @app.get('/api/workbench/events')
    async def events(request: Request):
        async def stream():
            previous = None
            while not await request.is_disconnected():
                with store.connect() as c:
                    rev = c.execute('SELECT COALESCE(MAX(seq),0) FROM events').fetchone()[0]
                if rev != previous:
                    yield f'data: {json.dumps({"revision": rev})}\n\n'
                    previous = rev
                else:
                    yield ': heartbeat\n\n'
                await asyncio.sleep(3)
        return StreamingResponse(stream(), media_type='text/event-stream', headers={'X-Accel-Buffering': 'no'})

    return app


app = create_app()
