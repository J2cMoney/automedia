import { useEffect, useState, useCallback } from 'react'
import {
  accountsApi,
  statsApi,
  orchestratorApi,
  type Account,
  type Stats,
  type BatchStatus,
} from '@/lib/api'
import Badge from '@/components/Badge'

/**
 * 仪表盘 - Design-Brief SCREEN-1(P0 核心)。
 *
 * 对照 Design-Brief SCREEN-1 布局:
 *   1. 顶部:全局 CTA「开始今日运营」+ 刷新
 *   2. 摘要条:已发布 / 待发布 / 失败 / 评论已回复率(CMP-004 统计数字 + nums)
 *   3. 账号卡片网格(CMP-001:头像+平台图标+状态色点+最后产出时间)
 *   4. 异常告警列表(可折叠:登录失效 / 失败任务的账号)
 *
 * 五态覆盖(Spec 6.2 SCREEN-1):
 *   - 加载态:骨架文案
 *   - 空态:无账号 → 引导去账号管理
 *   - 错误态:某账号登录失效 → 卡片红点 + 告警
 *   - 成功态:点 CTA 后卡片转蓝「运行中」
 *   - 无权限态:后端未启动 → 极简文案
 */

// 平台元信息(沿用 Accounts.tsx 范式)
const PLATFORM_BADGE: Record<string, { char: string; cls: string; label: string }> = {
  xhs: { char: '红', cls: 'bg-platform-xhs', label: '小红书' },
  dy: { char: '抖', cls: 'bg-platform-dy border border-white', label: '抖音' },
  ks: { char: '快', cls: 'bg-platform-ks', label: '快手' },
  wx: { char: '视', cls: 'bg-platform-wx', label: '视频号' },
}

export default function Dashboard() {
  const [accounts, setAccounts] = useState<Account[]>([])
  const [stats, setStats] = useState<Stats | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // 编排批次状态(点 CTA 后轮询)
  const [batchId, setBatchId] = useState<string | null>(null)
  const [batch, setBatch] = useState<BatchStatus | null>(null)
  const [starting, setStarting] = useState(false)
  const [alertsOpen, setAlertsOpen] = useState(true)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [accs, st] = await Promise.all([
        accountsApi.list(),
        statsApi.stats().catch(() => null), // stats 失败不阻塞账号列表
      ])
      setAccounts(accs)
      setStats(st)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  // 轮询批次状态(范式照搬 Pipeline 的 crawl/video 轮询)
  useEffect(() => {
    if (!batchId) return
    let active = true
    const poll = async () => {
      try {
        const info = await orchestratorApi.batchStatus(batchId)
        if (!active) return
        setBatch(info)
        // 批次 finished 后刷新账号/统计
        if (info.status === 'finished') {
          await load()
          return
        }
      } catch {
        /* 忽略轮询错误 */
      }
      setTimeout(poll, 2000)
    }
    poll()
    return () => {
      active = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [batchId])

  // 「开始今日运营」:选所有有效登录账号跑全链路
  async function startDaily() {
    const validAccounts = accounts.filter(
      (a) => a.auth_status === 'valid' && a.status === 'active',
    )
    if (validAccounts.length === 0) {
      setError('没有有效登录的账号,请先在账号管理配置并登录')
      return
    }
    setStarting(true)
    setError(null)
    try {
      const resp = await orchestratorApi.startDaily({
        account_ids: validAccounts.map((a) => a.id),
      })
      setBatchId(resp.batch_id)
      setBatch(null)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setStarting(false)
    }
  }

  // 异常账号(登录失效 或 禁用)
  const abnormalAccounts = accounts.filter(
    (a) => a.auth_status === 'invalid' || a.status === 'disabled',
  )
  // 运行中的账号(批次里 status 不是终态)
  const runningAccountIds = batch
    ? Object.entries(batch.results)
        .filter(([, r]) => r.status !== 'pending_publish' && r.status !== 'failed')
        .map(([id]) => Number(id))
    : []
  // 批次中失败的账号
  const failedAccountResults = batch
    ? Object.entries(batch.results).filter(([, r]) => r.status === 'failed')
    : []
  const pendingPublishResults = batch
    ? Object.entries(batch.results).filter(([, r]) => r.status === 'pending_publish')
    : []

  // ---------- 渲染 ----------

  if (loading) {
    return (
      <div className="p-6 max-w-[1400px]">
        <h1 className="text-lg font-semibold mb-6">仪表盘</h1>
        <div className="text-center py-16 text-text-secondary text-sm">加载中…</div>
      </div>
    )
  }

  // 无权限态:后端未启动(网络请求失败 + 错误信息含连接拒绝)
  const isBackendDown =
    error !== null && /fetch|network|ECONN|网络请求失败/i.test(error) && accounts.length === 0

  return (
    <div className="p-6 max-w-[1400px]">
      {/* 顶部:标题 + CTA */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-lg font-semibold">仪表盘</h1>
          <div className="text-sm text-text-secondary mt-1">
            {accounts.length > 0
              ? `${accounts.length} 个账号 · 一眼看全局状态`
              : '账号矩阵总览与今日运营'}
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            className="px-3 py-2 border border-border-bright rounded-md text-[13px] font-semibold text-text hover:bg-surface"
            onClick={load}
          >
            刷新
          </button>
          <button
            className="px-4 py-2 bg-primary text-white rounded-md text-[13px] font-semibold hover:bg-primary-hover disabled:opacity-50"
            onClick={startDaily}
            disabled={starting || batch?.status === 'running'}
          >
            {starting
              ? '启动中…'
              : batch?.status === 'running'
                ? '运营进行中…'
                : '开始今日运营'}
          </button>
        </div>
      </div>

      {/* 错误提示 */}
      {error && !isBackendDown && (
        <div className="mb-4 px-3 py-2 rounded-md text-[13px] bg-danger-faint text-danger border border-danger/30">
          {error}
        </div>
      )}

      {/* 无权限态:后端未启动 */}
      {isBackendDown && (
        <div className="text-center py-16">
          <div className="text-base font-semibold mb-2">无法连接后端服务</div>
          <div className="text-sm text-text-secondary mb-4">
            请先启动后端服务(后端 API),再刷新本页
          </div>
          <button
            className="px-4 py-2 bg-primary text-white rounded-md text-[13px] font-semibold hover:bg-primary-hover"
            onClick={load}
          >
            重新连接
          </button>
        </div>
      )}

      {/* 空态:无账号 */}
      {!isBackendDown && accounts.length === 0 && (
        <div className="text-center py-16">
          <div className="text-base font-semibold mb-2">还没有账号</div>
          <div className="text-sm text-text-secondary mb-6 max-w-md mx-auto">
            仪表盘需要一个以上有效登录的账号。去账号管理添加你的第一个号,配置平台、主题并完成登录。
          </div>
          <a
            href="/accounts"
            className="inline-block px-4 py-2 bg-primary text-white rounded-md text-[13px] font-semibold hover:bg-primary-hover"
          >
            去账号管理
          </a>
        </div>
      )}

      {/* 主内容 */}
      {!isBackendDown && accounts.length > 0 && (
        <>
          {/* 摘要条(CMP-004) */}
          {stats && (
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6">
              <StatCard
                label="已发布"
                value={stats.published}
                dotClass="bg-success"
              />
              <StatCard
                label="待发布"
                value={stats.pending_publish}
                dotClass="bg-warning"
              />
              <StatCard
                label="生成失败"
                value={stats.failed}
                dotClass="bg-danger"
              />
              <StatCard
                label="评论已回复率"
                value={`${(stats.replied_rate * 100).toFixed(0)}%`}
                dotClass="bg-info"
                sub={`共 ${stats.comments_total} 条`}
              />
            </div>
          )}

          {/* 批次进度条(点 CTA 后显示) */}
          {batch && (
            <div className="mb-6 px-4 py-3 rounded-md bg-surface border border-border">
              <div className="flex items-center justify-between mb-2">
                <div className="flex items-center gap-2">
                  {batch.status === 'running' ? (
                    <Badge variant="info" pulse>运行中</Badge>
                  ) : (
                    <Badge variant="success">已完成</Badge>
                  )}
                  <span className="text-sm text-text-secondary">
                    批次 {batch.batch_id} · {batch.summary.pending_publish} 个账号待发布 ·{' '}
                    {batch.summary.failed} 个失败
                  </span>
                </div>
              </div>
              {/* 迷你进度条(CMP-003) */}
              <div className="h-1.5 bg-bg rounded-full overflow-hidden">
                <div
                  className="h-full bg-info transition-all duration-300"
                  style={{
                    width: `${
                      batch.summary.total > 0
                        ? ((batch.summary.pending_publish + batch.summary.failed) /
                            batch.summary.total) *
                          100
                        : 0
                    }%`,
                  }}
                />
              </div>
            </div>
          )}

          {/* 异常告警列表(可折叠) */}
          {(abnormalAccounts.length > 0 || failedAccountResults.length > 0) && (
            <div className="mb-6">
              <button
                className="flex items-center gap-2 text-[13px] font-semibold text-danger mb-2"
                onClick={() => setAlertsOpen((v) => !v)}
              >
                <span className="w-2 h-2 rounded-full bg-danger" />
                {abnormalAccounts.length + failedAccountResults.length} 条异常告警
                <span className="text-text-tertiary text-xs">
                  {alertsOpen ? '▾ 收起' : '▸ 展开'}
                </span>
              </button>
              {alertsOpen && (
                <div className="space-y-1">
                  {abnormalAccounts.map((a) => (
                    <div
                      key={`ab-${a.id}`}
                      className="flex items-center gap-2 px-3 py-2 rounded-md bg-danger-faint border border-danger/30 text-[13px]"
                    >
                      <span className="text-danger">●</span>
                      <span className="flex-1">
                        {a.nickname} · {PLATFORM_BADGE[a.platform]?.label ?? a.platform}
                      </span>
                      <span className="text-danger text-xs">
                        {a.auth_status === 'invalid'
                          ? '登录态失效,需重新登录'
                          : '账号已禁用'}
                      </span>
                      <a href="/accounts" className="text-xs text-primary hover:text-primary-hover">
                        处理 →
                      </a>
                    </div>
                  ))}
                  {failedAccountResults.map(([aid, r]) => (
                    <div
                      key={`bf-${aid}`}
                      className="flex items-center gap-2 px-3 py-2 rounded-md bg-danger-faint border border-danger/30 text-[13px]"
                    >
                      <span className="text-danger">●</span>
                      <span className="flex-1">账号 {aid} · {r.step} 环节失败</span>
                      <span className="text-danger text-xs truncate max-w-[300px]">
                        {r.error}
                      </span>
                      <a
                        href="/settings"
                        className="text-xs text-primary hover:text-primary-hover shrink-0"
                        title="去日志与设置查看详细错误"
                      >
                        查看日志 →
                      </a>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          {/* 账号卡片网格(CMP-001) */}
          <h2 className="text-sm font-semibold mb-3 text-text-secondary uppercase tracking-wider">
            账号状态
          </h2>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
            {accounts.map((acc) => {
              const pb = PLATFORM_BADGE[acc.platform] ?? { char: '?', cls: 'bg-border-bright', label: acc.platform }
              const isRunning = runningAccountIds.includes(acc.id)
              const isAbnormal = acc.auth_status === 'invalid' || acc.status === 'disabled'
              return (
                <a
                  key={acc.id}
                  href="/accounts"
                  className="block p-4 bg-surface border border-border rounded-md hover:bg-surface-hover transition-colors"
                >
                  {/* 卡片头:头像 + 平台 + 状态色点 */}
                  <div className="flex items-center justify-between mb-3">
                    <div className="flex items-center gap-2">
                      <div className="w-8 h-8 rounded-full bg-border-bright flex items-center justify-center text-sm shrink-0">
                        {acc.nickname.slice(0, 1)}
                      </div>
                      <div>
                        <div className="text-sm font-medium">{acc.nickname}</div>
                        <div className="flex items-center gap-1.5 text-xs text-text-tertiary">
                          <span className={`inline-flex items-center justify-center w-3.5 h-3.5 rounded-sm text-[9px] text-white ${pb.cls}`}>
                            {pb.char}
                          </span>
                          {pb.label}
                        </div>
                      </div>
                    </div>
                    {/* 状态色点(DP-002) */}
                    {isRunning ? (
                      <Badge variant="info" pulse>运行中</Badge>
                    ) : isAbnormal ? (
                      <Badge variant="danger">异常</Badge>
                    ) : acc.auth_status === 'valid' ? (
                      <Badge variant="success">正常</Badge>
                    ) : (
                      <Badge variant="neutral">未登录</Badge>
                    )}
                  </div>

                  {/* 主题 + 最后活动 */}
                  <div className="text-xs text-text-tertiary space-y-1">
                    <div>
                      主题:{acc.topic_theme || <span className="text-warning">未配置</span>}
                    </div>
                    <div>
                      最后活动:{new Date(acc.updated_at).toLocaleString('zh-CN', {
                        month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
                      })}
                    </div>
                  </div>
                </a>
              )
            })}
          </div>

          {/* 待发布快捷区(批次跑完后) */}
          {pendingPublishResults.length > 0 && (
            <div className="mt-6">
              <h2 className="text-sm font-semibold mb-3 text-text-secondary uppercase tracking-wider">
                待发布内容(已成片,等手动发布)
              </h2>
              <div className="text-xs text-text-tertiary mb-3">
                {pendingPublishResults.length} 条内容已自动跑完热点→文案→视频成片。点击下方前往流水线页逐条辅助发布(A-8 人机协同)。
              </div>
              <a
                href="/pipeline"
                className="inline-block px-4 py-2 bg-primary text-white rounded-md text-[13px] font-semibold hover:bg-primary-hover"
              >
                去流水线发布 →
              </a>
            </div>
          )}
        </>
      )}
    </div>
  )
}

// ---------- 子组件 ----------

function StatCard({
  label,
  value,
  dotClass,
  sub,
}: {
  label: string
  value: number | string
  dotClass: string
  sub?: string
}) {
  return (
    <div className="px-4 py-3 bg-surface border border-border rounded-md">
      <div className="flex items-center gap-1.5 text-xs text-text-tertiary mb-1">
        <span className={`w-1.5 h-1.5 rounded-full ${dotClass}`} />
        {label}
      </div>
      <div className="text-xl font-semibold nums">{value}</div>
      {sub && <div className="text-[11px] text-text-tertiary mt-0.5">{sub}</div>}
    </div>
  )
}
