# AutoMedia - 自媒体全自动运营流水线

一人公司用的自媒体全自动运营流水线:管多账号矩阵,覆盖查热点→写文案→视频剪辑→分发→回评全链路。

> 本目录是项目代码。需求文档、设计规范、开发计划在上级目录(E:\AIproject\dd-test\)。

## 当前阶段:Phase 1 - 基础设施 + 调度核心

已交付:
- ✅ 后端骨架(FastAPI + 路由 + 配置加载)
- ✅ 数据库骨架表(accounts + task_runs)
- ✅ LLM 客户端(DeepSeek 文本 + GLM 视觉,统一接口)
- ✅ 任务队列(Dramatiq + Redis,submit/status/result/retry + 重启可恢复)
- ✅ 配置安全(.env.example 提交,.env 不提交)

## 环境要求

- Python 3.10+
- Docker(跑 Redis)
- Windows/macOS/Linux 均可

## 快速开始

### 1. 装依赖
```bash
cd automedia
uv sync --extra dev
```

### 2. 配置密钥
```bash
cp .env.example .env
# 编辑 .env,填入真实的 DEEPSEEK_API_KEY 和 GLM_API_KEY
```

### 3. 启动 Redis(Docker)
```bash
docker run -d --name automedia-redis -p 6379:6379 redis:7-alpine
```
验证: `docker exec automedia-redis redis-cli ping` 应返回 `PONG`

### 4. 启动 API 服务
```bash
# Windows PowerShell / Git Bash
$env:PYTHONPATH="backend"  # PowerShell
# 或 export PYTHONPATH="backend"  # Git Bash
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```
访问: http://127.0.0.1:8000/docs (Swagger UI)

### 5. 启动 Worker(另开终端,执行异步任务)
```bash
cd automedia
export PYTHONPATH="backend"
uv run dramatiq --processes 1 --threads 1 app.queue
```

## 健康检查
```bash
curl http://127.0.0.1:8000/health
# 期望: {"status":"healthy","db":true,"redis":{"connected":true,...}}
```

## 测试队列
```bash
# 提交测试任务
curl -X POST http://127.0.0.1:8000/tasks/test -H "Content-Type: application/json" -d '{"seconds":3}'
# 查状态
curl http://127.0.0.1:8000/tasks/1
```

## 跑测试
```bash
cd automedia
uv run pytest backend/tests/ -v
```

## 项目结构
```
automedia/
├── backend/
│   ├── app/
│   │   ├── main.py          # FastAPI 入口 + 路由
│   │   ├── config.py        # 配置加载(从 .env)
│   │   ├── db.py            # 数据库连接 + Session
│   │   ├── queue.py         # 任务队列(Dramatiq + Redis)
│   │   ├── llm/client.py    # LLM 客户端(DeepSeek + GLM)
│   │   └── models/          # ORM 模型(account/task_run)
│   └── tests/               # 单元测试
├── data/                    # SQLite 数据库(gitignore)
├── output/                  # 视频产出(gitignore)
├── frames/                  # 视频抽帧(gitignore)
├── .env.example             # 配置模板(提交)
├── .env                     # 真实密钥(gitignore,不提交)
└── pyproject.toml           # 依赖管理(uv)
```

## 配置安全(硬约束)
- `.env` 不提交,`.env.example` 提交
- API key 只从环境变量读,代码里无硬编码
- 之前暴露过 key,本次严守规范
