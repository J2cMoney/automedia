# DEV-PLAN 变更记录

> 任何 DEV-PLAN 变更先记这里再改正文。倒序排列，最新在上。
> Spec 变更导致的 DEV-PLAN 同步，注明对应的 Product-Spec-CHANGELOG 版本。

---

## [Phase 3 完成] - 2026-07-02 - 热点采集 + 文案生成（四步走全过）

### 交付物
- **数据层**：topics + contents 表（content.py 视频字段按计划留 Phase 4）
- **热点采集**：crawler.py（Playwright + Phase 2 cookie 自主爬三平台热榜，按 topic_theme 过滤排序 + 排除词）
- **文案生成**：copywriter.py + prompts/（DeepSeek，4 平台调性，标题/正文/标签 + 视频脚本分镜）
- **API**：topics/contents 路由 + crawl_hotspot_task actor（队列承接热点爬取）
- **前端**：Pipeline.tsx（横向 6 节点四态色，对照 pipeline.html + SCREEN-3）+ 侧边栏导航接入

### 四步走验收
1. **Code Review**：spawn code-reviewer 两阶段，0 HIGH，2 MEDIUM + 5 LOW 全部已修
2. **测试**：96 passed（crawler 16 + copywriter 24 + topics_api 11 + Phase 1/2 原有）
3. **编译**：后端 import OK（12 路由）+ 前端 tsc 0 error + vite build ✓
4. **功能**：真实 DeepSeek 调用跑通文案+脚本生成（小红书调性正确，标签提取修复后 8 个）

### 验收中发现并修复的真实缺陷
- **标签丢失**（功能测试）：LLM 把标签写进正文末尾而非 tags 字段 → 强化 prompt + `_extract_trailing_tags` 兜底
- **异常信息泄漏**（S2-MED-1）：generate 路由异常细节透传前端 → 改固定文案
- **排除词 UI 缺失**（S1-MED-1）：FLOW-1 MUST 后端有前端没接 → 补输入框
- **task_run 未回填 account_id**（S2-LOW-2）：submit 加 run_account_id 透传
- **React key spread 警告**：PipelineNode 改显式传参

### 待手动验收（不阻塞 Phase 4）
真实 Playwright 热榜爬取需有效登录态账号 cookie，用户登录平台后在流水线页实测。

---

## [修订 4] - 2026-07-02 - A-2 POC 结论：热点采集弃用 MediaCrawler，改 Playwright 自主爬

### 背景
DEV-PLAN 原 Phase 3 指定 `crawler.py` = MediaCrawler 封装。Phase 3 启动前联网确认 MediaCrawler 2026-07 现状（Product-Spec 假设 A-2 要求 POC 验证）：

- **形态**：CLI 工具 / 爬虫项目，不是 pip 库。集成需 clone 仓库 + 独立 Python 3.11 环境 + Node ≥18 + Playwright 浏览器，作为外部进程或 HTTP API server(8080) 跑。
- **热榜非一等公民**：核心场景是按关键词/ID 爬笔记和评论，热榜要靠搜索或特定页间接抓。
- **真正强项是评论/二级评论**，对应 Phase 5 FLOW-5 需求。
- **License**：Apache-2.0（可商用，无许可问题）。
- 53.9K stars，活跃维护。

项目 Phase 2 已引入 Playwright（账号登录态获取），各平台热榜是公开页面，直接爬更轻、更可控、不引外部仓库依赖。

### 变更清单
- **Phase 3 热点采集**：`crawler.py` 改为 Playwright + Phase 2 cookie 自主爬三平台（小红书/抖音/快手）热榜公开页，按账号 topic_theme 过滤排序。对外接口（`crawl_hot_topics`）不变，下游不受影响。
- **MediaCrawler 引入推迟到 Phase 5**：评论爬取时再评估（自主 Playwright 爬 vs MediaCrawler API server），不在 Phase 3 决定。
- DEV-PLAN.md：Phase 3 crawler.py 关键文件说明 + 已知风险表 A-2 行同步修订。
- Product-Spec A-2「MediaCrawler 开源版够用」结论不变，只是落地位置从 Phase 3 移到 Phase 5。

### 影响
- Phase 3 不增加 MediaCrawler 外部依赖，开发环境保持单 Python 项目，Windows 兼容性更好。
- crawler.py 仍是 Phase 3 关键单测目标，测试策略不变。

---

## [修订 3] - 2026-07-02 - 任务队列 RQ → Dramatiq（Windows 兼容性硬阻塞）

### 背景
Phase 1 开发时实测发现：**RQ 2.7.0 在 Windows 原生跑不了**。RQ 的 scheduler.py 在 import 时硬编码 `get_context('fork')`，而 Windows 没有 fork（只有 spawn），直接抛 `ValueError: cannot find context for 'fork'`。这不是版本 bug，是 RQ 架构依赖 fork 语义（[GitHub Issue #2369](https://github.com/rq/rq/issues/2369)）。即使 Redis 用 Docker 跑，Python 端的 RQ worker 仍 import 就崩。

目标用户在 Windows 开发，DEV-PLAN 原指定 RQ 与开发环境冲突。

### 变更清单

| # | 位置 | 变更 | 类型 |
|---|---|---|---|
| 1 | 技术栈表 | 任务队列 RQ + Redis → Dramatiq + Redis（2.2.x） | 🔴 依赖换 |
| 2 | Phase 1 关键文件 queue.py | RQ → Dramatiq 封装 | 联动 |
| 3 | Phase 1 验收标准 | 注明队列用 Dramatiq + 原因 | 🟡 补说明 |

### 为什么选 Dramatiq
- **原生支持 Windows**（Dramatiq 官方对比表明确列出 Windows 支持，RQ 没有）
- Redis broker 复用（你已选 Docker 跑 Redis，broker 不变）
- API 接近 RQ（actor 装饰器 + broker），迁移成本低
- 内置 retry middleware（失败重试是 Phase 1 验收要求）
- 比 Celery 轻，比 RQ 更跨平台

### 不选其他的原因
- WSL 跑 RQ：要求用户全程 WSL 开发，Windows/WSL 路径切换麻烦，违反"开发环境不变"原则
- Huey：更轻但默认 SQLite 队列，与已选的 Docker Redis 方案冲突
- 自写 SQLite 队列：能做但偏离成熟库路线，后续 Phase 4/5 高并发场景要重写

### 教训
DEV-PLAN 技术栈表选型时没核实库的平台兼容性。规则补充：**技术栈选型除版本核实外，必须核实目标开发/运行平台的兼容性**，尤其是涉及进程模型（fork vs spawn）、系统调用的库。

---

## [修订 4] - 2026-07-02 - GLM 视觉从火山方舟中转改智谱官方（Phase 1 实施时）

### 背景
Phase 1 验收 4（GLM 视觉调通）时，POC 用的火山方舟中转 token（ark-32d49ea7...）已失效（401 not active）。用户改用智谱官方开放平台（open.bigmodel.cn）的 GLM-4V。

### 变更清单

| # | 位置 | 变更 | 类型 |
|---|---|---|---|
| 1 | 技术栈表 LLM 视觉 | 火山方舟中转 GLM-5.2 → 智谱官方 GLM-4V（OpenAI 兼容） | 🔴 端点换 |
| 2 | backend/app/llm/client.py | GLM 从 Anthropic SDK → OpenAI SDK（image_url base64 格式） | 🔴 代码改 |
| 3 | pyproject.toml | 去掉 anthropic 依赖（不再需要） | 🟢 清理 |
| 4 | .env.example / config.py | GLM_BASE_URL/GLM_MODEL 默认值改智谱官方 | 联动 |

### 为什么改
- 火山方舟中转 token 是 POC 临时用的，已失效
- 智谱官方开放平台（open.bigmodel.cn）原生提供 GLM-4V-Flash（免费）和 GLM-4.6V，OpenAI 兼容格式
- 统一用 OpenAI SDK，去掉 anthropic 依赖，两个 LLM 调用方式一致（image_url base64），降低维护成本
- GLM-4V-Flash 免费适合 Phase 1 验收，正式剪辑可用 GLM-4.6V

---

## [修订 2] - 2026-07-02 - 第二轮评审（用户要求复审）

### 背景
第一轮评审后用户要求"再评一次"。第二轮换视角（逐行读 + grep 扫结构），抓到第一轮漏掉的 4 个问题，其中 1 个是低级操作错误。

### 变更清单

| # | 位置 | 变更 | 类型 |
|---|---|---|---|
| 1 | Phase 4 | **删除重复段**：关键文件+验收标准整段重复两遍（第一轮 Edit 操作残留，旧版覆盖回修订版） | 🔴 低级错误 |
| 2 | Phase 1 queue.py + 验收 | 任务队列补"状态同步写 task_runs 表，服务重启读表恢复"——Phase 6 的"重启可恢复"才有根基 | 🟡 补持久化 |
| 3 | Phase 5 验收 | 风控松紧排序"小红书<快手<抖音"是我编的没核实 → 改成"用你日常最常用的平台"（诚实表述） | 🟡 删编造 |
| 4 | Phase 2 关键文件 | 加 `frontend/src/lib/api.ts`（前后端联调）+ `main.py` 配 CORS——6 Phase 都有前端但没说怎么对接 | 🟢 补联调 |

### 教训（已入 evolution signals）
自审自己写的东西，眼睛会跳过自己的错误。第一轮逐行读没发现 Phase 4 重复，第二轮 grep 扫结构才抓到。
**规则：自审要换视角（逐行读 + 结构扫描双管齐下），不能只读一遍就放行。**

---

## [修订 1] - 2026-07-02 - 第一轮评审（自审）

### 背景
DEV-PLAN 初版生成后，按规约做反失败自检。对照 Spec、Design-Brief 逐项挑刺。

### 变更清单

**4 个致命（会导致返工）：**

| # | 位置 | 变更 | 类型 |
|---|---|---|---|
| 1 | Phase 1 | 不再一次性建全部表 → 只建 accounts/task_runs，其他表随业务 Phase 增量建（topics Phase 3、contents Phase 3建+Phase 4加列、comments Phase 5） | 🔴 致命 |
| 2 | Phase 1→2 | 前端骨架从 Phase 1 挪到 Phase 2，跟账号 UI 一起做 → 消灭 Phase 1 前端断档 | 🔴 致命 |
| 3 | Phase 1 | 任务队列从"骨架"升级为"能提交+查状态+存结果+重试" → 后续 Phase 直接用 | 🔴 致命 |
| 4 | Phase 2 | 加登录态健康检查（Spec FLOW-6 MUST）→ 手动改坏 cookie 能被检测标记 | 🔴 致命 |

**3 个补全（规约硬性要求）：**

| # | 位置 | 变更 | 类型 |
|---|---|---|---|
| 5 | 技术栈表 | 版本号全部 WebSearch 验证填上（FastAPI 0.138/React 19/Vite 8/Tailwind v4/Playwright 1.61/Remotion 4.0） | 🟡 补版本 |
| 6 | 开发规则 | 测试策略明确：关键单测+手动验收，列了具体要测的 6 个服务 | 🟡 补策略 |
| 7 | 开发规则 + Phase 1 | 配置安全写死：`.env` 不提交、`.env.example` 提交（key 已暴露过，必须严守） | 🟡 补安全 |

**2 个边界补充：**

| # | 位置 | 变更 | 类型 |
|---|---|---|---|
| 8 | Phase 4 | 加"测试视频由用户提前准备（≥2分钟口播）" | 🟢 补前置 |
| 9 | Phase 5 | 指定首选平台跑通（修订 2 又改成"日常最常用的平台"，见上） | 🟢 补策略 |

---

## [初版] - 2026-07-02 - DEV-PLAN 生成

### 背景
基于 Product-Spec.md v1.5 + Design-Brief.md + 设计稿（3 屏 HTML）+ POC 结论（GLM 看图通过、DeepSeek 不能看图），按 6 Phase 严格依赖正序生成。

### 关键决策
- 6 Phase 拆法（用户选）：基础设施→账号→热点文案→视频→分发回评→整合
- 技术栈：FastAPI + React + Vite + Tailwind + shadcn/ui + Playwright + Remotion + SQLite + RQ
- LLM 分工：DeepSeek 文本（文案/回评）+ 智谱 GLM 视觉（视频剪辑）—— POC 验证
- 项目目录：automedia/（用户确认）
- 测试策略：关键单测 + 手动验收（用户选）
