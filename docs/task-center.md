# Fork 任务中心

## 结构与边界

- `extensions/task_center/api.py`：独立 FastAPI 服务，`/workbench/` 和 `/api/workbench/*`。
- `store.py`：SQLite WAL、事务领取、状态事件、幂等提交、查询。
- `worker.py`：独立串行执行进程。单数据目录 `flock` 独占，防止第二执行器错误恢复或并发使用 GPU。
- `adapter.py`：唯一上游对接层。调用上游 `process_video_task` / `process_upload_task`，通过显式 observer 将更新写入 SQLite。不复制上游流水线。
- `migrate.py`：从只读旧目录导入文案。先备份 JSON，再按旧 ID 幂等导入，报告缺失结果文件。
- `static/workbench/`：无构建步骤、无 CDN、独立 ES modules。简化 Markdown 渲染仅生成安全 DOM；不执行原文 HTML、图片或链接。

新 API 不加载上游模型；长耗时调用只发生在 worker，因此不会阻塞 Web。首版使用单 worker，不使用 Redis/Celery。排队任务恢复，运行中任务在 worker 重启时标记 interrupted，由用户手动重试。不是逐阶段断点续跑。

### 上游修改清单（同步时重点检查）

1. `backend/main.py`：`TRANSCRIBER_TEMP_DIR` 配置及 `register_task_observer`。原入口未注册回调时仍保持原逻辑。
2. `backend/transcriber.py`：Whisper 环境变量，默认保留 CPU；修正空语言 Markdown 标记被解析成 `**` 的问题。
3. `backend/summarizer.py` / `translator.py`：读取 `OPENAI_MODEL`，保留原默认值。

GPU 配置来自原部署的需求，但没有覆盖上游 Dockerfile / Compose。增强部署文件单独维护。

## 安全与使用范围

这是个人信任域的管理应用，没有独立账号系统。容器端口仅绑定宿主机 127.0.0.1，生产发布必须通过已有 Access 策略，不能直接映射到 0.0.0.0。所有列表、结果、SSE 和下载路径必须受到同样保护。不能只保护 HTML。

单用户首版所有授权访问者共享历史；不支持多租户隔离。视频抓取复用上游网络能力，只供可信用户提交链接，不是开放抓取代理。保留源站防火墙，避免旁路。API 无跨域许可，并拒绝跨源浏览器写入。

不保存前端 API Key，worker 从外部 `.env` 读取服务器模型凭据；不把 `.env` 放进仓库或镜像。结果目录可能包含个人文案，应限制宿主访问并定期备份。执行器日志仅供主机管理员排错，不暴露给网页。

## 数据与导出

数据目录包括 `tasks.sqlite3`、`uploads/`、`media/` 和 `legacy-backups/`。文案以 SQLite 中的 result 为准，MD/TXT 由服务端导出；原始媒体和流水线临时文件独立保留，首版不自动清理。手动清理前需先备份，并注意上传文件用于重试。

首次历史迁移不会搬走或删除原媒体，只导入现有文案；缺失文件列入迁移报告。原始创建时间不存在时界面明确标为历史导入，不把导入时间冒充原始时间。

下载与任务链接需要登录；复制任务链接不是公开分享。首版没有 PDF/Word、在线编辑、任务删除、运行中强制取消或优先级。

## 启动隔离测试环境

以下是此部署机已验证的参数。命令不修改原服务：

```bash
cd /home/kai/AI-Video-Transcriber-fork
export TC_HOST_DATA=/home/kai/.local/share/transcriber-workbench-test
export TC_ENV_FILE=/home/kai/AI-Video-Transcriber/.env
export TC_MODEL_CACHE=/home/kai/AI-Video-Transcriber/model_cache
export TC_RUNTIME_IMAGE=ai-video-transcriber-ai-video-transcriber
docker compose -p transcriber-workbench -f deploy/compose.workbench.yml build web
docker compose -p transcriber-workbench -f deploy/compose.workbench.yml up -d
```

访问 `http://127.0.0.1:18000/workbench/`。外部通过 SSH 本地转发访问，尚未切换 Cloudflare 生产路由。

当前运行时复用本机已验证的 Python 3.12/CUDA 镜像。换机器时先用 `deploy/runtime.Dockerfile` 构建 `transcriber-runtime`，并验证 GPU/驱动；该干净构建路径还需单独验证。不要假设只克隆仓库就已经具备运行依赖。生产发布建议固定运行时镜像 digest。

## 历史导入

在停止向旧版本提交新任务、确认旧任务已结束后做最终迁移。测试期间可以先从只读副本导入。

```bash
docker run --rm --network none \
  -v /home/kai/AI-Video-Transcriber/temp:/legacy:ro \
  -v /home/kai/.local/share/transcriber-workbench-test:/data/task-center \
  --entrypoint python3 transcriber-workbench:test \
  -m extensions.task_center.migrate --source /legacy --data /data/task-center
```

## 测试与上游同步

```bash
docker run --rm --network none --entrypoint python3 transcriber-workbench:test \
  -m unittest discover -s tests/task_center -v
```

同步上游后重点检查：上述回调契约与函数签名、原始稿文件字段、终态字段、语言解析、模型配置。执行自动化测试后，再做一条真实音频及一条 URL 任务验收。不要在合并时直接用 ours/theirs 覆盖冲突。

旧 Web/CLI/MCP 文件保留。原始 API 不走新队列，所以不要同时把旧提交入口与新队列当成同一组 GPU 调度系统使用。切换生产后应只引导用户从工作台提交，旧界面用于回退，不作为第二并发提交入口。

生产切换：先备份数据、完成最终历史迁移、确认测试队列清空；用独立生产数据目录启动同一构建，然后经授权将业务 Tunnel 路由改到新 Web 端口。保留旧容器作为回滚，绝不重启 `cloudflared.service`（SSH）。不要直接把包含验收任务的测试数据库当生产库。
