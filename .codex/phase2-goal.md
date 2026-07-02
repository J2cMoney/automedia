/goal 完成 DEV-PLAN.md Phase 2：账号矩阵管理 + 前端骨架。Strict Release 模式（.codex/workflow-mode 已是 strict），按 Phase 完整流程执行，四步走验收全过才算完成。

先读这些原文（别靠记忆）：E:\AIproject\dd-test\DEV-PLAN.md 的「Phase 2」章节、Product-Spec.md 的 4.4 数据模型 + FLOW-6 + AC-4、Design-Brief.md 的 SCREEN-2 + CMP-002 状态徽章、design-prototype/accounts.html + styles.css（视觉单一真相源，UI 一切以设计稿为准）。再读 automedia/README.md 和 backend/app/ 现有代码接手 Phase 1 成果（config.py/db.py/queue.py/main.py/models/account.py 已就绪，Account 模型已有 platform/nickname/topic_theme/auth_state/status 字段）。

完成的标准：
1. 后端账号 CRUD API：automedia/backend/app/api/accounts.py 实现 GET/POST/PUT/DELETE /api/accounts，支持 4 平台（小红书/抖音/快手/视频号，Platform 枚举已在 models/account.py），并在 main.py 注册路由。
2. 登录态加密存储：services/crypto.py 用 Fernet 加密 cookie（密钥从 .env 读，新增 COOKIE_ENCRYPT_KEY 到 config.py 和 .env.example），services/auth.py 用 Playwright 开浏览器让用户登录后抓 cookie 并加密存入 account.auth_state。
3. 登录态健康检查：services/auth_health.py 校验 cookie 有效性（POST /api/accounts/{id}/health-check），手动改坏 cookie 能识别并标记失效、返回需重新登录。验收时构造一个坏 cookie 必须被检出。
4. CORS 配置：main.py 加 CORS 中间件允许开发态前端域 localhost:5173。
5. 前端骨架：automedia/frontend/ 用 Vite 8 + React 19 + Tailwind v4 + shadcn/ui 搭建，src/index.css 把 design-prototype/styles.css 的 CSS 变量（色彩/字体/间距/圆角）转成深色主题 token，src/lib/api.ts 封装 fetch（base URL 指后端 + 统一错误处理）。
6. 账号管理页：pages/Accounts.tsx 对照 accounts.html 实现紧凑表格（平台/昵称/主题/登录态徽章/状态/操作），components/AccountDrawer.tsx 实现右侧抽屉表单（添加/编辑），删除走二次确认弹窗（Danger 按钮 + 按 AC-4 提示「平台侧登录态需自行退出」）。
7. 侧边栏：components/Sidebar.tsx 实现 6 项导航（仪表盘/内容流水线/账号管理/评论中心/数据概览/日志设置），当前页高亮，对照 styles.css 的 .nav-item.active 样式。

四步走验收（Phase 完成硬门槛，每步贴证据）：
- Code Review：对照 DEV-PLAN Phase 2 交付清单逐项确认，检查有无超范围改动、有无硬编码 key、UI 是否对照设计稿。
- 测试完整性：给 crypto.py 和 auth_health.py 写关键单测（加解密往返、坏 cookie 检出），pytest 全过。
- 编译验证：后端 `python -c "import app.main"` 无错；前端 `pnpm build` 通过、`pnpm tsc --noEmit` 零错误。
- 功能测试：前端 pnpm dev 能起、深色主题、6 项导航可点；能加账号（含 Playwright 登录抓 cookie 加密入库）；表格显示账号 + 登录态徽章四态色；能删除（二次确认）；能编辑主题；改坏 cookie 健康检查能标记失效。

验证方式：
- 后端：uv run pytest backend/tests/ -v 全过；启动 uvicorn 后 curl 各 CRUD 端点返回正确。
- 前端：cd automedia/frontend && pnpm build 无错；pnpm dev 起来后浏览器看账号页对照 accounts.html 截图自检。
- 安全：grep 全项目确认无硬编码 key，.env 在 .gitignore，COOKIE_ENCRYPT_KEY 只从环境变量读。

约束：
- 只做 Phase 2 范围，不建 topics/contents/comments 表（Phase 3/5），不做热点/视频/分发（Phase 3+）。
- UI 严格对照设计稿 styles.css 和 accounts.html，不自由发挥；Design-Brief 冲突时以设计稿为准。
- 配置安全：.env 不提交、.env.example 提交、API key 和 COOKIE_ENCRYPT_KEY 只从环境变量读，绝不硬编码（Phase 1 已严守，沿用）。
- 环境前提：Docker Redis 容器 automedia-redis 已在跑（Phase 1 起的），后端用 uv，前端用 pnpm（本机已装 v11）。Playwright 首次需要 uv run playwright install chromium。
- 不可逆操作（commit/push/删除数据）停下来问用户，不自动做。
- 每完成一个子模块停下来贴证据自检，全过再进下一个；Phase 全部完成贴四步走证据汇报，等用户确认。

执行策略：目标导向，一条路不通换方法，多种都试过才停；长任务记一句进度日志。
