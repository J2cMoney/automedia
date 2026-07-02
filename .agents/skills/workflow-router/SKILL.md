---
name: workflow-router
description: 每轮用户提出产品、代码、修复、审查或发布请求时先使用。判断任务属于 Fast Patch、Normal Feature 还是 Strict Release，给出推荐档位并写入 .codex/workflow-mode。
---

[任务]
    对用户请求先判档，再把任务路由到合适 Skill。只做分类、说明理由、必要时写 .codex/workflow-mode，不实现代码、不改产品文档、不跑测试。

[依赖检测]
    必需：用户当前请求、项目根目录。
    可选：Product-Spec.md、DEV-PLAN.md、项目代码、git 状态。缺失不阻塞判档，只降低判断置信度。

[文件结构]
    workflow-router/
    └── SKILL.md  # 本文件，无 references / templates

[第一性原则]
    先判档再动手：不要让小修误入完整流水线，也不要让发布任务走快修。
    Agent 先判断：不要默认把选择题丢给用户。先给推荐档位、理由和下一步。
    少问但不冒险：明显的直接做，边界不清或涉及发布成本、安全风险时才问用户确认。
    不偷偷降级：用户明确要求 strict 或任务已进入 strict 后，本轮不能降回 normal/fast。

[档位定义]
    Fast Patch：文案、样式微调、小 bug、单文件局部修改、不改变产品需求、不涉及数据/权限/支付/发布。后续用 fast-patch。
    Normal Feature：新增或调整用户可感知功能、跨多个文件、影响交互或接口，但不涉及发布、安全、数据迁移。后续按需用 product-spec-builder、dev-planner、dev-builder。
    Strict Release：0 到 1 新项目、从需求到发布、上线、打包、部署、认证、支付、数据库迁移、安全敏感改动，或用户明确说严格模式。后续走完整流水线。

[判档清单]
    选 Fast Patch，必须同时满足：改动局部、可逆、不改变需求、不改数据模型、不碰权限/密钥/支付/发布、验证能在几分钟内完成。
    选 Normal Feature，只要满足任一项：新增功能、改交互流程、跨多个文件、影响 API 或状态结构、需要同步少量文档。
    选 Strict Release，只要满足任一项：上线、部署、打包、发布、生产数据、数据库迁移、认证权限、支付、密钥、安全、从 0 到 1、用户明确要求 strict。

[工作流程]
    1. 读取用户请求，必要时快速扫项目状态。
    2. 按 [判档清单] 选一个档位，置信度分 high / medium / low。
    3. 明显 Fast Patch：说“我按 Fast Patch 快修处理”，写 .codex/workflow-mode 为 fast，直接路由 fast-patch。
    4. 明显 Normal Feature：说“我按 Normal Feature 常规功能处理”，写 .codex/workflow-mode 为 normal，路由到对应开发 Skill。
    5. 明显 Strict Release：说“我建议按 Strict Release 严格发布处理”，写 .codex/workflow-mode 为 strict；涉及上线、支付、数据、安全或长时间任务时先等用户确认。
    6. medium / low 置信度：短问一句确认，格式为“我建议按 [档位]，因为 [一句理由]。按这个继续吗？”

[输出格式]
    判档：[Fast Patch / Normal Feature / Strict Release]
    理由：[一句话]
    下一步：[调用哪个 Skill 或直接执行]
    需要确认：[是/否，若是则只问一个短问题]

[初始化]
    执行 [依赖检测]。
