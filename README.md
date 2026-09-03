# AutoMedia

**An open-source, end-to-end content operations pipeline for solo social-media creators.**

AutoMedia automates the full workflow of running a multi-account content matrix on Chinese social platforms — from trending-topic discovery to publishing to audience interaction — so that a one-person team can operate like a media company.

```
Hot topics → AI copywriting → Video production → Multi-platform publishing → Comment replies → Unified analytics
```

## What it does

| Stage | Capability |
|---|---|
| 🔥 Topic discovery | Crawls platform hot-lists to surface topics worth covering |
| ✍️ Copywriting | LLM-generated, platform-native copy (text models behind a unified client) |
| 🎬 Video production | Idea → short video: frame extraction, vision-model editing decisions (shot selection, hooks, subtitles), TTS narration, programmatic rendering with **Remotion** |
| 📢 Publishing | Scheduled, account-authenticated publishing via browser automation — Douyin, Kuaishou, Xiaohongshu, WeChat Channels — with human-in-the-loop confirmation and risk-control spacing between posts |
| 💬 Comment engagement | Fetches comments and drafts AI replies for review before sending |
| 📊 Analytics | One dashboard across all accounts and platforms |

## Architecture

- **Backend** — Python 3.10+, FastAPI, SQLAlchemy + SQLite, Dramatiq + Redis task queue (crash-recoverable, survives restarts)
- **Frontend** — React + Vite + TypeScript dashboard
- **Video** — Remotion (React-based programmatic video), frame extraction, TTS + subtitles
- **Platform automation** — Playwright with encrypted cookie persistence (Fernet)
- **LLM clients** — unified interface: text models for copy/replies, vision models for edit decisions; all keys read strictly from environment variables

## Codex-native development

AutoMedia is built **with Codex CLI from day one**, and this repo dogfoods the complete setup:

- [`.codex/`](.codex/) — agent configs, workflow hooks, and an **evolution engine** that turns runtime signals into reviewed improvement proposals
- [`AGENTS.md`](AGENTS.md) — the agent operating manual
- [`Product-Spec.md`](Product-Spec.md) / [`DEV-PLAN.md`](DEV-PLAN.md) — spec-driven development with changelogs; every phase is specified, implemented, reviewed, and tested through the Codex loop

## Status

Phase 6 complete — full-pipeline orchestration, dashboard integration, and end-to-end field-tested fixes. Previously shipped: intelligent video editing (vision-model batch decisions + Remotion rendering), multi-platform publishing (human-in-the-loop v1.6), and automated comment replies. Active development; see [`DEV-PLAN-CHANGELOG.md`](DEV-PLAN-CHANGELOG.md).

The test suite covers every service module (`automedia/backend/tests/`).

## Quick start

```bash
git clone https://github.com/J2cMoney/automedia
cd automedia/automedia

uv sync --extra dev
cp .env.example .env        # add your API keys — .env is never committed
docker run -d --name automedia-redis -p 6379:6379 redis:7-alpine

uv run uvicorn app.main:app --host 127.0.0.1 --port 8000   # API + Swagger UI
uv run dramatiq --processes 1 --threads 1 app.queue        # task worker
```

See [`automedia/README.md`](automedia/README.md) for the detailed guide, or [`使用说明.md`](使用说明.md) for the Chinese user manual.

## Security notes

- `.env` is gitignored; only `.env.example` (placeholders) is committed
- API keys are read exclusively from environment variables — no hardcoded secrets
- Platform login cookies are encrypted at rest (Fernet) and never logged

## Roadmap

- Migrate copywriting/reply generation to OpenAI models and adopt vision models for the editing brain
- Grow the Codex "agent ops" loop: analytics-driven content plans proposed and implemented by agents, gated by the test suite
- Publish a real-world cost guide for running a 10-account matrix
