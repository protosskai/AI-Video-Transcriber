"""Standalone workbench API; bind to loopback and publish ONLY behind Access."""
import asyncio
import fcntl
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


def create_app(root=None):
    store = Store(root or os.environ.get('TC_DATA_DIR', '/data/task-center'))
    app = FastAPI(title='Transcriber Workbench', docs_url=None, redoc_url=None)
    app.state.store = store
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
        return FileResponse(static / 'index.html')

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
    def submit(body: URLJob):
        validate_language(body.language)
        url = body.url.strip()
        parts = urlsplit(url)
        if parts.scheme not in ('http', 'https') or not parts.hostname or parts.username or parts.password:
            raise HTTPException(422, '请输入不含账号密码的 HTTP/HTTPS 视频链接')
        jid = store.create(parts.hostname + ' / ' + (parts.path.rsplit('/', 1)[-1] or '视频'),
                           {'kind': 'url', 'url': url, 'language': body.language, 'keep_video': body.keep_video}, request_key=body.request_key)
        return public(require(jid), True)

    @app.post('/api/workbench/uploads', status_code=202)
    async def upload(file: UploadFile = File(...), language: str = Form('zh'), request_key: str = Form(...)):
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
        try:
            with dest.open('xb') as handle:
                while chunk := await file.read(1024 * 1024):
                    size += len(chunk)
                    if size > maximum:
                        raise HTTPException(413, f'文件超过 {maximum // 1024 // 1024} MB 限制')
                    handle.write(chunk)
            if not size:
                raise HTTPException(422, '不能上传空文件')
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
