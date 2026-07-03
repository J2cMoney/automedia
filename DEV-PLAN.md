# Development Plan — 自媒体全自动运营流水线

> 本文件记录项目的开发阶段划分、当前进度和剩余工作。
> 新 session 启动时应首先阅读此文件，了解项目状态后再继续开发。
>
> 单一真相源：Product-Spec.md 是功能范围，Design-Brief.md 是视觉契约，本文件是开发顺序。
> 任何 Spec 变更先改 Spec + CHANGELOG，再回头评估本计划是否要同步。

---

## 总览

| Phase | 名称 | 核心交付 | 依赖 |
|---|---|---|---|
| 1 | 基础设施 + 调度骨架 | 数据库/配置/LLM封装/任务队列 | — |
| 2 | 账号矩阵管理 + 前端骨架 | 账号 CRUD + 登录态 + 健康检查 + Web 骨架 + 账号页 | Phase 1 |
| 3 | 热点采集 + 文案生成 | 热榜爬取 + DeepSeek 文案 | Phase 2 |
| 4 | 视频智能剪辑 | agent 抽帧/决策/渲染两场景 | Phase 3 |
| 5 | 多平台分发 + 自动回评 | 3 平台自动发 + 视频号半自动 + 回评 | Phase 4 |
| 6 | 全链路串联 + 面板整合 | 一键跑全链路 + Web 面板完整态 | Phase 1-5 |

**总原则**：每个 Phase 完成必须能编译、能启动、能看到效果。不允许"写一堆跑不起来"的 Phase。

---

## 当前进度

| Phase | 状态 | 四步走验收 | 备注 |
|---|---|---|---|
| 1 | ✅ 完成 | Code Review / 95→96 测试 / 编译 / 功能 | 基础设施 + Dramatiq 队列(Windows 兼容) |
| 2 | ✅ 完成 | Code Review / 测试 / 编译 / 功能 | 账号矩阵 + 登录态加密 + 健康检查 + 前端骨架 |
| 3 | ✅ 完成 | Code Review(0 HIGH) / 96 测试 / 编译 / DeepSeek 真实调用跑通 | 热点采集(Playwright 自主爬,A-2 修订) + 文案生成 + 流水线页 |
| 4 | ✅ 完成 | Code Review(4 HIGH+2 MED 全修) / 217 测试 / 编译 / 场景A+B 真实跑通 | 视频智能剪辑(GLM 分批决策+Remotion 渲染+Edge-TTS+faster-whisper) |
| 5 | ⬜ 待开始 | — | 多平台分发 + 自动回评 |
| 6 | ⬜ 待开始 | — | 全链路串联 + 面板整合 |

**下一步**：Phase 5 多平台分发 + 自动回评。

**Phase 4 端到端验收记录**(2026-07-03):
- 场景 A 高光提取:真实风格口播视频(2分40秒)→ GLM-4v-flash 分批看帧(4批×5帧)准确识别高光段(核心方法 vs 铺垫/结尾)→ ffmpeg 重编码剪切拼接出成片 + clip_decision.json 落盘。修复:GLM 1210(高信息帧压 jpeg)、TTS 0 字幕(boundary=WordBoundary)、Remotion 绝对路径(staticFile+public 暂存)、路径穿越防护、音画同步(TTS 时长驱动 scene duration)。
- 场景 B 从零生成:DeepSeek 出 scene plan(英文 asset_keyword)→ Pexels 兜底(key 未配走纯色背景)→ Edge-TTS 11.1s 配音(25 WordBoundary cue)→ faster-whisper 备选 → Remotion 9:16 渲染(音画同步 5+3+3=11s + 字幕叠加)→ Content 写回。
- 已知限制:GLM 对合成色块/噪点判"无高光"(准确,符合 Spec FLOW-3 口播适用约束);真实口播画面识别良好。Remotion 个人/≤3人免费 License。

---

## Phase 1: 基础设施 + 调度核心

**交付内容**：
- 搭建后端骨架（FastAPI 应用 + 路由结构 + 配置加载 + .env 规范）
- 建核心骨架表（**只建 accounts + task_runs，其他表随业务 Phase 增量建**）
- 封装 LLM 客户端（DeepSeek 文本 + 智谱 GLM 视觉，统一接口 + 密钥从 .env 读）
- **搭任务队列（做到能用，不只骨架：能提交异步任务 + 查状态 + 存结果 + 失败重试）**
- 配置规范落地（`.env.example` 提交、`.env` 不提交、API key 只从环境变量读）

**关键文件**：
- `automedia/backend/app/main.py` — FastAPI 入口，路由注册
- `automedia/backend/app/config.py` — 配置加载（从 .env 读，pydantic-settings）
- `automedia/backend/app/db.py` — SQLite 连接 + **accounts/task_runs 两张表**（SQLAlchemy 2.x）
- `automedia/backend/app/models/account.py` — Account 模型
- `automedia/backend/app/models/task_run.py` — TaskRun 模型
- `automedia/backend/app/llm/client.py` — LLM 客户端（统一 `chat()` 文本 + `vision()` 视觉，POC 已验证两模型都能调通）
- `automedia/backend/app/queue.py` — 任务队列（Dramatiq + Redis，封装 submit/status/result/retry，**状态同步写 task_runs 表，服务重启读表恢复未完成任务**）
- `automedia/.env.example` — 配置模板（含所有 key 占位，提交到 git）
- `automedia/.gitignore` — 含 `.env`、`*.db`、`frames/`、`output/`

**验收标准**：
- 后端能启动，`GET /health` 返回 200
- accounts/task_runs 两张表能建，CRUD 测试数据能存能取
- LLM 文本接口能调通（DeepSeek 返回一段文案）
- LLM 视觉接口能调通（GLM 看一张测试图返回描述，复用 POC 脚本）
- 任务队能提交测试任务（sleep 3s）+ 查到 pending→running→finished 状态变化 + 失败任务能重试
- **任务队列用 Dramatiq（RQ 在 Windows 原生跑不了，见 DEV-PLAN-CHANGELOG 修订 3）**
- **任务状态写进 task_runs 表，重启后端服务，未完成任务状态不丢（能读到原状态）**
- `.env.example` 提交，`.env` 在 .gitignore 里

---

## Phase 2: 账号矩阵管理 + 前端骨架

**交付内容**：
- 实现账号 CRUD API（增删改查，支持 4 平台：小红书/抖音/快手/视频号）
- 实现登录态获取与加密存储（Playwright 打开浏览器让用户登录，抓 cookie，加密存库）
- **实现登录态健康检查（FLOW-6 MUST：发布/爬取前校验 cookie 有效性，失效则标记+提示重新登录）**
- **搭建前端骨架（React + Vite + Tailwind + shadcn/ui + 深色主题 token，对照设计稿 styles.css）**
- 实现账号管理 Web 页面（对应设计稿 SCREEN-2：表格 + 添加抽屉 + 删除二次确认）
- 接入前端侧边栏导航（6 个导航项，当前页高亮）

**关键文件**：
- `automedia/backend/app/api/accounts.py` — 账号 CRUD 路由
- `automedia/backend/app/services/auth.py` — 登录态获取服务（Playwright 开浏览器 + 抓 cookie + 加密）
- `automedia/backend/app/services/auth_health.py` — **登录态健康检查**（校验 cookie 是否过期/失效）
- `automedia/backend/app/services/crypto.py` — cookie 加密存储（Fernet）
- `automedia/frontend/` — React 前端骨架（Vite + Tailwind v4 + shadcn/ui + 深色 token）
- `automedia/frontend/src/index.css` — 深色主题 token（对照设计稿 styles.css 的 CSS 变量）
- `automedia/frontend/src/lib/api.ts` — **前后端联调配置**（API base URL + fetch 封装 + 统一错误处理）
- `automedia/backend/app/main.py` — **配置 CORS 允许前端域**（开发态 localhost:5173）
- `automedia/frontend/src/pages/Accounts.tsx` — 账号管理页（对照设计稿 accounts.html）
- `automedia/frontend/src/components/Sidebar.tsx` — 侧边栏导航（6 项）
- `automedia/frontend/src/components/AccountDrawer.tsx` — 添加/编辑抽屉表单

**验收标准**：
- 前端能启动，深色主题，侧边栏 6 项导航可点
- 能添加账号（选平台 + 填昵称 + 主题 + 浏览器登录），cookie 加密存库
- 账号列表表格显示测试账号（含登录态有效/失效徽章，对照设计稿四态色）
- 能删除账号（二次确认弹窗 + 平台侧登录态提示，对照 AC-4）
- 能编辑账号主题
- **手动改坏一个账号的 cookie，健康检查能识别并标记失效 + 显示"重新登录"**

---

## Phase 3: 热点采集 + 文案生成

**交付内容**：
- **建 topics + contents 表（Phase 1 只建了骨架表，这里增量建业务表）**
- 实现热点采集（MediaCrawler 集成，爬小红书/抖音/快手热榜，按账号主题过滤排序）
- 实现文案生成（DeepSeek 调用，按选题 + 账号主题 + 平台调性，产出标题/正文/标签 + 视频脚本）
- 实现内容流水线 Web 页面（对应设计稿 SCREEN-3：横向流水线节点）
- 任务队列承接热点爬取（长任务异步化）

**关键文件**：
- `automedia/backend/app/models/topic.py` — Topic 模型（Phase 3 新建表）
- `automedia/backend/app/models/content.py` — Content 模型（Phase 3 新建表，**视频相关字段留到 Phase 4 加列**）
- `automedia/backend/app/api/topics.py` — 选题 CRUD 路由
- `automedia/backend/app/api/contents.py` — 内容 CRUD 路由
- `automedia/backend/app/services/crawler.py` — 热榜爬取封装（Playwright + cookie 自主爬三平台热榜 + 按主题过滤；MediaCrawler 推 Phase 5，见已知风险 A-2 修订）
- `automedia/backend/app/services/copywriter.py` — 文案生成服务（DeepSeek，按平台调性适配）
- `automedia/backend/app/prompts/` — prompt 模板（热点筛选/文案生成/视频脚本）
- `automedia/frontend/src/pages/Pipeline.tsx` — 内容流水线页（对照设计稿 pipeline.html）

**验收标准**：
- 能爬取至少 1 个平台的热榜（20+ 条），按主题过滤后返回候选选题
- 选题确认后，能生成对应文案（标题/正文/标签），内容存入 Content 表
- 能生成视频脚本（分镜列表）
- 流水线页能显示"热点采集 ✓ → 文案生成 ✓"节点状态（对照设计稿四态色）

---

## Phase 4: 视频智能剪辑

**交付内容**：
- **Content 表加视频字段（video_path / script_scenes JSON / clip_decision JSON，Phase 3 留的列这里补）**
- 实现场景 A：高光提取（FFmpeg 抽帧 → GLM 视觉看帧找高光 → 输出剪辑决策 JSON → FFmpeg 剪切拼接）
- 实现场景 B：从零生成（文案脚本 → 出分镜 → Pexels 找素材 + TTS 配音 + Whisper 字幕 → Remotion 渲染成片）
- 实现混合素材模式（Pexels 找为主，手动上传兜底，找不到弹上传接口）
- 成片存入 Content 表，关联到后续分发环节
- **测试视频准备：Phase 4 开始前，用户准备 1 段自己录的口播长视频（≥2 分钟）用于验收场景 A**

**关键文件**：
- `automedia/backend/app/services/video/extractor.py` — 场景 A：长视频高光提取（FFmpeg 抽帧 + GLM 决策 + 剪切）
- `automedia/backend/app/services/video/generator.py` — 场景 B：从零生成（Pexels + TTS + Whisper + Remotion）
- `automedia/backend/app/services/video/frames.py` — FFmpeg 抽帧工具
- `automedia/backend/app/services/video/assets.py` — Pexels 素材获取 + 手动上传接口
- `automedia/backend/app/services/video/tts.py` — TTS 配音（Edge-TTS）
- `automedia/backend/app/services/video/subtitle.py` — Whisper 字幕时间轴
- `automedia/backend/app/services/video/render.py` — Remotion 渲染封装（HTML → MP4）
- `automedia/backend/app/services/video/agent.py` — 剪辑决策核心（GLM 视觉看帧 + 出 JSON 切点）

**验收标准**：
- **场景 A：用用户准备的长视频，抽帧 → LLM 看帧 → 输出 60s 高光剪辑成片**（注：POC 用示意图测过，真实视频需在此阶段二次验证 GLM 中文 OCR 能力）
- 场景 B：给一段文案，能找素材 + 配音 + 字幕 + 渲染出一条成片 MP4
- 渲染失败有重试，保留中间产物（抽帧/决策 JSON）
- 成片文件存到本地 `output/`，路径写入 Content 表

---

## Phase 5: 多平台分发 + 自动回评

**交付内容**：
- 实现 3 平台自动分发（小红书/抖音/快手，Playwright 模拟登录发布，复用 Phase 2 cookie）
- 实现视频号半自动分发（AI 备好标题/正文/标签/封面/视频打包，生成"待手动发布"卡片）
- 实现自动回评（MediaCrawler 抓评论 → DeepSeek 生成回复 → Playwright 模拟回复，限速防风控）
- 实现风控限速（同账号同平台两次发布间隔 ≥30 分钟，回评频率限速）

**关键文件**：
- `automedia/backend/app/services/publish/` — 分发服务目录
- `automedia/backend/app/services/publish/xhs.py` — 小红书发布
- `automedia/backend/app/services/publish/dy.py` — 抖音发布
- `automedia/backend/app/services/publish/ks.py` — 快手发布
- `automedia/backend/app/services/publish/wx.py` — 视频号半自动打包（生成待发布卡片数据）
- `automedia/backend/app/services/publish/base.py` — 分发基类（复用 cookie + Playwright + 限速）
- `automedia/backend/app/services/comment/` — 回评服务（抓评论 + 生成回复 + 模拟回复）
- `automedia/frontend/src/components/ManualPublishCard.tsx` — 视频号待发布卡片（对照设计稿 CMP-007）

**验收标准**：
- **首选你日常最常用的平台跑通自动发布**（具体哪个平台风控最松需实跑验证，建议从你手上 cookie 最稳定、日常最活跃的那个号开始，降低调试阻力）
- 发布失败有错误日志 + 重试机制
- 视频号半自动卡片能生成（标题/正文/标签/封面/视频文件路径齐全 + 复制按钮，对照 CMP-007）
- **同上平台跑通抓评论+自动回复**（与发布同平台，便于联调）
- 风控限速生效（同账号两次发布间隔 ≥30 分钟）

---

## Phase 6: 全链路串联 + 面板整合

**交付内容**：
- 实现全链路编排（"开始今日运营"按钮 → 自动跑 FLOW-1 到 FLOW-5 全流程，多账号并行调度）
- 完善任务调度（FLOW-8：渲染串行队列、发布风控限速、失败隔离、断点续跑）
- 完成仪表盘 Web 页面（对应设计稿 SCREEN-1：账号卡片网格 + 摘要条 + 异常告警列表）
- 完成评论中心页（SCREEN-4：评论与 AI 回复记录）
- 完成数据概览页（SCREEN-5：基础数据回显）
- 完成日志与设置页（SCREEN-6：任务日志 + AI/风控配置）
- 实现空/错/加载/成功/无权限五态全覆盖

**关键文件**：
- `automedia/backend/app/services/orchestrator.py` — 全链路编排器（串联 FLOW-1→5 + 调度）
- `automedia/backend/app/services/scheduler.py` — 调度器（并发控制 + 渲染限流 + 风控限速 + 断点续跑）
- `automedia/frontend/src/pages/Dashboard.tsx` — 仪表盘（对照设计稿 index.html）
- `automedia/frontend/src/pages/Comments.tsx` — 评论中心
- `automedia/frontend/src/pages/DataOverview.tsx` — 数据概览
- `automedia/frontend/src/pages/Settings.tsx` — 日志与设置
- `automedia/frontend/src/components/` — 全部组件完善（账号卡片/状态徽章/流水线节点/统计数字/CTA 按钮等）

**验收标准**：
- 点"开始今日运营"，1 个测试账号能跑完全链路（热点→文案→视频→发布→回评）
- 仪表盘能实时显示账号状态（运行中/已发布/失败/待手动发）
- 失败环节在仪表盘有红点告警 + 可查看日志 + 可重试
- 渲染排队不卡死机器（同时只跑 1 条渲染）
- 服务重启后未完成任务能恢复

---

## 技术栈

| 层级 | 技术 | 版本 | 说明 |
|------|------|------|------|
| 后端框架 | FastAPI | 0.138.x | Python 异步 API，跟 Playwright/Remotion 同语言 |
| 前端框架 | React | 19.x | Design Brief 定的 Vercel 风 |
| 前端构建 | Vite | 8.x | Rolldown 引擎，构建快 |
| UI 库 | Tailwind CSS + shadcn/ui | Tailwind v4.x | 深色紧凑开箱即用，对照设计稿 styles.css |
| 数据库 | SQLite | 内置 | 单机 ≤10 账号，轻量（未来 SaaS 化需迁 PG，Spec NON-2 非目标） |
| ORM | SQLAlchemy | 2.x | 表结构管理 + migration（Alembic） |
| 任务队列 | Dramatiq + Redis | 2.2.x | 异步长任务（渲染/发布），状态查询。原生支持 Windows（RQ 依赖 fork 在 Windows 跑不了，见 DEV-PLAN-CHANGELOG 修订 3） |
| 浏览器自动化 | Playwright | 1.61.x | 3 平台发布 + 评论 + MediaCrawler 底层 |
| 视频抽帧/剪切 | FFmpeg | imageio-ffmpeg | POC 已验证可用 |
| 视频渲染 | Remotion | 4.0.x | HTML/React 写视频，LLM 决策后渲染成片 |
| LLM 文本 | DeepSeek | deepseek-v4-flash | POC 验证：文本强+便宜，**看不了图**，只用于文案/回评 |
| LLM 视觉 | 智谱 GLM（官方开放平台 GLM-4V） | 最新 | OpenAI 兼容格式，POC 验证：能看抽帧做剪辑决策（中文 OCR 偏弱，真实视频需复验） |
| TTS | Edge-TTS | 免费 | 配音，备选豆包 TTS |
| 字幕 | Whisper | openai-whisper | 语音转时间轴 |
| 素材源 | Pexels API | 免费 | 无版权视频/图片 |
| 包管理（前端） | pnpm | 最新 | 速度快 |
| 包管理（后端） | uv（或 pip + requirements.txt） | 最新 | uv 更快，备选 pip |

## 数据库表

| 表名 | 所属 Phase | 用途 |
|------|-----------|------|
| `accounts` | **Phase 1 建表** / Phase 2 业务 | 账号矩阵（平台/昵称/主题/登录态/状态） |
| `task_runs` | **Phase 1 建表+业务** / 各 Phase 增量 | 任务运行记录（类型/状态/时间/错误日志） |
| `topics` | **Phase 3 建表+业务** | 选题候选（来源/热度/匹配账号/状态） |
| `contents` | **Phase 3 建表**（文案字段）/ **Phase 4 加列**（视频字段） | 内容（文案/脚本/视频路径/状态/发布链接） |
| `comments` | **Phase 5 建表+业务** | 评论（评论内容/AI 回复/状态） |

## 开发规则

**四步走（每个 Phase 完成必跑）**：
1. **Code Review** — 自审或 spawn code-reviewer，对照 Spec 和设计稿
2. **测试完整性** — 跑关键服务的单元测试（见下"测试策略"）
3. **编译验证** — 后端 `python -c "import automedia.backend"` 无报错；前端 `pnpm build` 通过
4. **功能测试** — 手动跑该 Phase 的验收标准，每条都要实地验证

**测试策略（关键单测 + 手动验收）**：
- **写单测的关键服务**：`llm/client.py`、`services/crawler.py`、`services/copywriter.py`、`services/video/agent.py`、`services/publish/base.py`、`queue.py`
- **手动验收**：UI 交互、Playwright 发布、视频渲染效果——这些靠 Phase 验收标准实地跑
- 不追求全单测覆盖，但关键服务必须有测试，且 CI/手动跑时必须通过

**其他**：
- 四步走全过才能 commit
- Commit message 用 feat、fix、refactor、chore 前缀
- 包管理器：前端 pnpm，后端 uv（或 pip）
- 每个 Phase 验收标准是硬门槛，没过不进下一个
- POC 已验证的约束必须遵守：GLM 看图/DeepSeek 不能看图/渲染 CPU 密集串行
- **配置安全：`.env` 不提交、`.env.example` 提交、API key 只从环境变量读**（key 已暴露过，必须严守）

## 已知风险与限制

| 风险 | Phase | 应对 |
|---|---|---|
| 平台风控封号 | Phase 5 | 限速 + 间隔 + 用户已知悉（Spec A-1） |
| 视频号无 API | Phase 5 | 半自动模式（Spec A-5） |
| Remotion 渲染 CPU 密集 | Phase 4/6 | 串行队列，同时只跑 1 条 |
| GLM 中文 OCR 弱 | Phase 4 | 真实视频帧需二次验证（POC 已知水分） |
| MediaCrawler 维护活跃度 | Phase 5 | ~~原 Phase 3~~ A-2 POC 结论(2026-07):MediaCrawler 是 CLI 工具非 pip 库,热榜非其强项(强项是评论/二级评论),集成要 clone 仓库+独立 Py3.11 环境+Node≥18,成本高。**Phase 3 热点采集改用 Playwright 自主爬热榜公开页**(复用 Phase 2 cookie)。MediaCrawler 推迟到 Phase 5 评论爬取时再评估(自主爬 vs API server)。crawler.py 内部封装自主爬,对外接口不变 |
