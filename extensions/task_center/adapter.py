"""Only boundary to upstream main. Runs exclusively in the worker process."""
import asyncio
import os
import re
import sys
from pathlib import Path

RESULT_KEYS = ('script', 'summary', 'translation', 'no_speech', 'detected_language', 'summary_language')


def redact(text):
    text = str(text)
    for key in ('OPENAI_API_KEY',):
        value = os.getenv(key)
        if value:
            text = text.replace(value, '[REDACTED]')
    text = re.sub(r'https?://\S+', '[remote endpoint]', text)
    return text[:1500]


class UpstreamAdapter:
    def __init__(self, store):
        self.store = store
        self.media = store.root / 'media'
        self.media.mkdir(exist_ok=True)
        os.environ['TRANSCRIBER_TEMP_DIR'] = str(self.media)
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'backend'))
        import main
        self.upstream = main
        self.current = None
        main.register_task_observer(self.observe)

    def observe(self, records):
        if not self.current or self.current not in records:
            return
        data = records[self.current]
        result = {k: data[k] for k in RESULT_KEYS if k in data and data[k] is not None}
        raw = data.get('raw_script_file')
        if raw and Path(raw).name == raw and (self.media / raw).is_file():
            result['raw'] = (self.media / raw).read_text(encoding='utf-8')
        status = {'processing': 'running', 'error': 'failed', 'completed': 'completed'}.get(data.get('status'), 'running')
        self.store.update(self.current, status, redact(data.get('message', '正在处理')),
                          result=result, error=redact(data['error']) if data.get('error') else None,
                          title=data.get('video_title'))

    async def run(self, job):
        self.current = job['id']
        request = job['request']
        self.upstream.tasks[job['id']] = {'status': 'processing'}
        try:
            if request['kind'] == 'url':
                await self.upstream.process_video_task(job['id'], request['url'], request['language'], want_video=request.get('keep_video', False))
            else:
                path = self.store.root / 'uploads' / request['file']
                if not path.is_file() or path.parent.resolve() != (self.store.root / 'uploads').resolve():
                    raise ValueError('上传文件缺失，请重新上传')
                await self.upstream.process_upload_task(job['id'], path, request['name'], job['title'], path.suffix.lower(), request['language'])
            self.observe(self.upstream.tasks)
            if self.store.get(job['id'])['status'] == 'running':
                raise RuntimeError('上游未返回终态')
        finally:
            self.upstream.tasks.pop(job['id'], None)
            self.current = None
