import concurrent.futures
import json
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault('TC_DATA_DIR', tempfile.mkdtemp(prefix='tc-import-'))
from fastapi.testclient import TestClient
from extensions.task_center.store import Store
from extensions.task_center.api import create_app
from extensions.task_center.migrate import migrate


class WorkbenchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root / 'data')
        self.client = TestClient(create_app(self.root / 'data'))

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def submit(self, key='request-0001'):
        return self.client.post('/api/workbench/tasks', json={'url':'https://example.com/video', 'request_key':key})

    def test_submit_persists_and_is_idempotent(self):
        a, b = self.submit().json(), self.submit().json()
        self.assertEqual(a['id'], b['id'])
        self.assertEqual(a['status'], 'queued')
        self.assertEqual(Store(self.store.root).get(a['id'])['request']['language'], 'zh')
        self.assertNotIn('request', a)

    def test_atomic_serial_claim(self):
        for i in range(3): self.submit(f'request-{i:04}')
        with concurrent.futures.ThreadPoolExecutor(4) as pool:
            claimed = list(pool.map(lambda _: self.store.claim(), range(4)))
        jobs = [x for x in claimed if x]
        self.assertEqual(len(jobs), 1)
        self.assertEqual(self.store.listing()['counts'], {'running':1, 'queued':2})
        self.store.update(jobs[0]['id'],'completed','完成')
        self.assertIsNotNone(self.store.claim())

    def test_recovery_preserves_queue_and_history(self):
        a = self.submit().json()
        b = self.submit('request-0002').json()
        self.store.claim()
        Store(self.store.root).recover()
        self.assertEqual(self.store.get(a['id'])['status'], 'interrupted')
        self.assertEqual(self.store.get(b['id'])['status'], 'queued')
        retry = self.client.post(f"/api/workbench/tasks/{a['id']}/retry?request_key=retry-0001")
        self.assertEqual(retry.status_code, 202)
        self.assertEqual(retry.json()['retry_of'], a['id'])
        self.assertEqual(self.store.get(a['id'])['status'], 'interrupted')

    def test_export_and_detail(self):
        jid = self.submit().json()['id']
        self.store.update(jid,'completed','完成',result={'script':'# 标题\n\n**你好** 世界','raw':'原始文字'})
        detail = self.client.get('/api/workbench/tasks/'+jid).json()
        self.assertIn('script', detail['available'])
        for fmt in ('md','txt'):
            r = self.client.get(f'/api/workbench/tasks/{jid}/export/script?format={fmt}')
            self.assertEqual(r.status_code,200)
            self.assertIn('attachment',r.headers['content-disposition'])
            self.assertIn('你好',r.text)
            if fmt=='txt': self.assertNotIn('**',r.text)
        self.assertEqual(self.client.get(f'/api/workbench/tasks/{jid}/export/password').status_code,400)

    def test_upload_and_duplicate(self):
        args={'data':{'request_key':'upload-001','language':'zh'},'files':{'file':('hello.txt','测试文本','text/plain')}}
        a = self.client.post('/api/workbench/uploads',**args)
        b = self.client.post('/api/workbench/uploads',**args)
        self.assertEqual(a.status_code,202)
        self.assertEqual(a.json()['id'],b.json()['id'])
        self.assertEqual(len(list((self.store.root/'uploads').iterdir())),1)
        bad = self.client.post('/api/workbench/uploads',data=args['data'],files={'file':('bad.exe',b'x')})
        self.assertEqual(bad.status_code,422)

    def test_validation_origin_and_paths(self):
        self.assertEqual(self.client.post('/api/workbench/tasks',json={'url':'file:///etc/passwd','request_key':'request-0001'}).status_code,422)
        self.assertEqual(self.client.post('/api/workbench/tasks',json={'url':'https://u:pw@host/video','request_key':'request-0001'}).status_code,422)
        self.assertEqual(self.client.post('/api/workbench/tasks',headers={'origin':'https://evil.test'},json={'url':'https://example.com','request_key':'request-0001'}).status_code,403)
        self.assertEqual(self.client.get('/api/workbench/tasks/unknown').status_code,404)
        self.assertEqual(self.client.get('/api/workbench/tasks?size=0').status_code,422)

    def test_search_filter_and_pagination(self):
        for i in range(4): self.store.create(f'文稿 {i}',{'kind':'legacy'})
        data=self.client.get('/api/workbench/tasks?q=文稿&size=2&page=2').json()
        self.assertEqual(data['total'],4)
        self.assertEqual(len(data['items']),2)
        self.assertEqual(self.client.get('/api/workbench/tasks?status=completed').json()['total'],0)

    def test_migration_idempotent_and_non_destructive(self):
        source=self.root/'legacy';source.mkdir()
        (source/'raw.md').write_text('旧的原始文稿')
        records={'legacy-001':{'status':'completed','video_title':'旧任务','script':'旧文稿','raw_script_file':'raw.md'},'legacy-002':{'status':'processing','summary_path':'/app/temp/missing.md'}}
        (source/'tasks.json').write_text(json.dumps(records))
        before=(source/'tasks.json').read_bytes()
        a=migrate(source,self.store.root);b=migrate(source,self.store.root)
        self.assertEqual(a['imported'],2)
        self.assertEqual(b['imported'],0)
        self.assertEqual(len(a['missing']),1)
        self.assertEqual((source/'tasks.json').read_bytes(),before)
        self.assertEqual(self.store.get('legacy-001')['result']['raw'],'旧的原始文稿')
        self.assertEqual(self.store.get('legacy-002')['status'],'interrupted')

    def test_workbench_and_csp(self):
        r=self.client.get('/workbench/')
        self.assertEqual(r.status_code,200)
        self.assertIn('音视频转文字',r.text)
        self.assertIn("script-src 'self'",r.headers['content-security-policy'])
        self.assertEqual(self.client.get('/health').json()['status'],'ok')

    def test_upstream_adapter_contract_and_empty_language(self):
        from extensions.task_center.adapter import UpstreamAdapter
        adapter = UpstreamAdapter(self.store)
        self.assertTrue(callable(adapter.upstream.register_task_observer))
        self.assertTrue(callable(adapter.upstream.process_video_task))
        self.assertTrue(callable(adapter.upstream.process_upload_task))
        tr = adapter.upstream.transcriber
        tr.last_detected_language = None
        self.assertIsNone(tr.get_detected_language('**Detected Language:**'))
        self.assertEqual(tr.get_detected_language('**Detected Language:** zh'),'zh')
        jid=self.submit().json()['id']
        adapter.current=jid
        adapter.observe({jid:{'status':'completed','message':'完成','script':'结果','video_title':'验收'}})
        self.assertEqual(self.store.get(jid)['result']['script'],'结果')
        adapter.upstream.register_task_observer(None)

    def test_no_worker_reported_offline(self):
        self.assertFalse(self.client.get('/api/workbench/tasks').json()['worker_online'])

    def test_failed_task_preserves_partial_raw(self):
        jid=self.submit().json()['id']
        self.store.update(jid,'running','转录完成',result={'raw':'已生成的原文'})
        self.store.update(jid,'failed','摘要失败',error='上游服务暂不可用')
        self.assertEqual(self.client.get('/api/workbench/tasks/'+jid).json()['result']['raw'],'已生成的原文')


if __name__=='__main__': unittest.main()
