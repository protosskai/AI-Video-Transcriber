import concurrent.futures
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault('TC_DATA_DIR', tempfile.mkdtemp(prefix='tc-library-import-'))
from fastapi.testclient import TestClient
from extensions.task_center.api import create_app
from extensions.task_center.library import Conflict, source_key


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = create_app(Path(self.temp.name))
        self.client = TestClient(self.app)
        self.store, self.lib = self.app.state.store, self.app.state.library

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def submit(self, key='submit-0001', url='https://youtu.be/abc', language='zh'):
        r = self.client.post('/api/workbench/library/submit', json=dict(url=url, language=language, request_key=key))
        self.assertEqual(r.status_code, 202, r.text)
        return r.json()

    def retry(self, cid, key='retry-0001'):
        return self.client.post(f'/api/workbench/library/{cid}/retry', json={'request_key':key})

    def action(self, cid, action, **kw):
        return self.client.post(f'/api/workbench/library/{cid}/action', json=dict(action=action, **kw))

    def test_source_dedup_and_language(self):
        a = self.submit()
        b = self.submit('submit-0002', 'https://www.youtube.com/watch?v=abc&utm_source=x')
        self.assertEqual(a['content_id'], b['content_id'])
        self.assertTrue(b['reused'])
        self.assertNotEqual(a['content_id'], self.submit('submit-0003', language='en')['content_id'])

    def test_upload_hash_dedup_cleans_unused_file(self):
        ids=[]
        for i in range(2):
            r=self.client.post('/api/workbench/library/upload', data={'request_key':f'upload-000{i}'}, files={'file':(f'{i}.txt',b'hello')})
            self.assertEqual(r.status_code,202,r.text)
            ids.append(r.json()['job_id'])
        self.assertEqual(ids[0],ids[1])
        self.assertEqual(len(list((self.store.root/'uploads').iterdir())),1)

    def test_retry_idempotency_and_old_result(self):
        a=self.submit();cid,jid=a['content_id'],a['job_id']
        self.store.update(jid,'failed','失败',result={'raw':'旧原文'},error='test')
        b=self.retry(cid).json()
        self.assertNotEqual(jid,b['job_id'])
        self.assertEqual(b,self.retry(cid).json() | {'reused':False})
        self.assertEqual(self.retry(cid,'retry-0002').json()['job_id'],b['job_id'])
        d=self.client.get('/api/workbench/library/'+cid).json()
        self.assertEqual(d['reading']['id'],jid)
        self.assertEqual(d['attempt_count'],2)
        self.store.update(b['job_id'],'completed','完成',result={'script':'新结果'})
        self.assertEqual(self.retry(cid).json()['job_id'],b['job_id'])
        self.assertEqual(self.retry(cid,'retry-0002').json()['job_id'],b['job_id'])
        self.assertEqual(self.client.get(f'/api/workbench/tasks/{jid}/export/raw').text,'旧原文')
        self.assertEqual(self.client.get(f'/api/workbench/library/{cid}?run=unrelated').status_code,404)

    def test_legacy_chain_and_link_migration(self):
        req={'kind':'url','url':'https://example.com','language':'zh'}
        a=self.store.create('old',req)
        self.store.update(a,'failed','failed')
        b=self.store.create('retry',req,retry_of=a)
        separate=self.store.create('independent',req)
        self.lib.sync();self.lib.sync()
        self.assertEqual(self.lib.resolve(b),a)
        self.assertEqual(self.lib.resolve(separate),separate)
        self.assertEqual(self.client.get('/api/workbench/library/'+b).json()['attempt_count'],2)

    def test_soft_delete_restore_and_active_guard(self):
        a=self.submit();cid,jid=a['content_id'],a['job_id']
        self.assertEqual(self.action(cid,'trash').status_code,409)
        self.assertEqual(self.action(cid,'archive').status_code,409)
        self.store.update(jid,'completed','完成',result={'script':'保留'})
        self.assertEqual(self.action(cid,'rename',title='新标题').json()['title'],'新标题')
        for action,view in [('archive','archive'),('trash','trash')]:
            self.assertEqual(self.action(cid,action).status_code,200)
            self.assertEqual(self.client.get('/api/workbench/library?view='+view).json()['total'],1)
            self.assertEqual(self.retry(cid).status_code,409)
        self.action(cid,'restore')
        self.assertEqual(self.client.get('/api/workbench/library').json()['total'],1)
        self.assertEqual(self.store.get(jid)['result']['script'],'保留')

    def test_concurrent_retry_creates_one_attempt(self):
        a=self.submit();self.store.update(a['job_id'],'failed','failed')
        req=self.store.get(a['job_id'])['request']
        with concurrent.futures.ThreadPoolExecutor(8) as pool:
            results=list(pool.map(lambda i:self.lib.enqueue('retry',req,f'parallel-{i}',a['content_id']),range(8)))
        self.assertEqual(len({r[1] for r in results}),1)
        self.assertEqual(len(self.lib.get(a['content_id'])['jobs']),2)

    def test_cancel_claim_race(self):
        a=self.submit()
        def cancel():
            try:self.lib.cancel(a['content_id']);return True
            except Conflict:return False
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            c=pool.submit(cancel);claim=pool.submit(self.store.claim)
            cancelled,claimed=c.result(),claim.result()
        self.assertNotEqual(cancelled,bool(claimed))
        self.assertIn(self.store.get(a['job_id'])['status'],('running','cancelled'))

    def test_cancel_then_retry_and_validation(self):
        a=self.submit();self.assertEqual(self.action(a['content_id'],'cancel').status_code,200)
        self.assertIsNone(self.store.claim())
        self.assertEqual(self.retry(a['content_id']).status_code,202)
        self.assertEqual(self.action(a['content_id'],'rename',title=' ').status_code,422)
        self.assertEqual(self.client.get('/api/workbench/library?view=invalid').status_code,422)


if __name__=='__main__':unittest.main()
