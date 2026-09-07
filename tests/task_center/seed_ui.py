"""Synthetic acceptance content; refuse a production data directory."""
import os
from extensions.task_center.store import Store
from extensions.task_center.library import Library

if os.getenv('TC_ACCEPTANCE') != '1':
    raise SystemExit('Only run in the disposable acceptance container')
store = Store('/data/task-center')
library = Library(store)
for item in library.listing()['items']:
    job = item['jobs'][0]
    store.update(job['id'], 'failed', '下载失败', result={'raw': '这是失败前保留的原文。'}, error='SSL: UNEXPECTED_EOF_WHILE_READING (验收夹具)')
cid, jid, _ = library.enqueue('如何把知识变成自己的理解', {'kind':'url','url':'https://example.com/reading-demo','language':'zh'}, 'ui-reading-demo')
store.update(jid, 'completed', '完成', result={
    'script':'# 如何把知识变成自己的理解\n\n阅读不是信息的堆积，而是建立连接的过程。\n\n## 一、先提出问题\n\n带着一个具体问题开始阅读，决定哪些内容值得留下。\n\n## 二、用自己的语言复述\n\n- 记录核心观点\n- 区分事实与推断\n- 保留值得核对的原始来源\n\n## 三、回到实际场景\n\n把新知识用在一个小问题上，然后根据反馈修正理解。\n\n> 理解的证据，不是记住了多少，而是能够解释什么。',
    'raw':'这是用于界面验收的识别原文。<script>alert(1)</script>\n\n第一段内容。',
    'summary':'## 核心观点\n\n提问、复述、应用，构成学习的反馈循环。',
    'translation':'## Learning through practice\n\nAsk, explain, and apply.'})
print(cid)
