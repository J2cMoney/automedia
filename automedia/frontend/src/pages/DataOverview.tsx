import { useEffect, useState, useCallback } from 'react'
import {
  statsApi,
  accountsApi,
  contentsApi,
  type Stats,
  type Account,
  type Content,
} from '@/lib/api'

/**
 * 数据概览 - Design-Brief SCREEN-5。
 *
 * Spec NON-7 边界:v1 只做基础数据回显(发布数/评论数等原始指标),
 * 不做趋势分析/归因/预测等 BI 能力。
 *
 * 布局:
 *   1. 统计卡片网格(发布/待发布/失败/评论数/已回复率)
 *   2. 各账号发布数明细表(前端聚合 contents + accounts)
 *
 * 五态:加载 / 空(无数据)/ 错误 / 成功(数据卡片)/ 无权限(后端未连接)
 */

export default function DataOverview() {
  const [stats, setStats] = useState<Stats | null>(null)
  const [accounts, setAccounts] = useState<Account[]>([])
  const [contents, setContents] = useState<Content[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [st, accs, cts] = await Promise.all([
        statsApi.stats(),
        accountsApi.list(),
        contentsApi.list(),
      ])
      setStats(st)
      setAccounts(accs)
      setContents(cts)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  // 各账号发布数明细(前端聚合)
  const accountStats = accounts.map((acc) => {
    const accContents = contents.filter((c) => c.account_id === acc.id)
    return {
      account: acc,
      total: accContents.length,
      published: accContents.filter((c) => c.status === 'published').length,
      pending: accContents.filter((c) => c.status === 'approved').length,
      failed: accContents.filter((c) => c.status === 'failed').length,
    }
  })

  if (loading) {
    return (
      <div className="p-6 max-w-[1400px]">
        <h1 className="text-lg font-semibold mb-6">数据概览</h1>
        <div className="text-center py-16 text-text-secondary text-sm">加载中…</div>
      </div>
    )
  }

  const isBackendDown = error !== null && /fetch|network|ECONN|网络请求失败/i.test(error)

  return (
    <div className="p-6 max-w-[1400px]">
      {/* 顶部 */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-lg font-semibold">数据概览</h1>
          <div className="text-sm text-text-secondary mt-1">
            基础数据回显 · 不做趋势分析
          </div>
        </div>
        <button
          className="px-3 py-2 border border-border-bright rounded-md text-[13px] font-semibold text-text hover:bg-surface"
          onClick={load}
        >
          刷新
        </button>
      </div>

      {/* 错误提示 */}
      {error && !isBackendDown && (
        <div className="mb-4 px-3 py-2 rounded-md text-[13px] bg-danger-faint text-danger border border-danger/30">
          {error}
        </div>
      )}

      {/* 无权限 */}
      {isBackendDown && (
        <div className="text-center py-16">
          <div className="text-base font-semibold mb-2">无法连接后端服务</div>
          <button
            className="px-4 py-2 bg-primary text-white rounded-md text-[13px] font-semibold hover:bg-primary-hover"
            onClick={load}
          >
            重新连接
          </button>
        </div>
      )}

      {/* 空态 */}
      {!isBackendDown && stats && stats.contents_total === 0 && !error && (
        <div className="text-center py-16">
          <div className="text-base font-semibold mb-2">还没有数据</div>
          <div className="text-sm text-text-secondary mb-6">
            跑一次「开始今日运营」生成内容后,这里会显示发布数、评论数等基础数据。
          </div>
          <a
            href="/"
            className="inline-block px-4 py-2 bg-primary text-white rounded-md text-[13px] font-semibold hover:bg-primary-hover"
          >
            去仪表盘
          </a>
        </div>
      )}

      {/* 成功态:统计卡片 + 明细表 */}
      {!isBackendDown && stats && stats.contents_total > 0 && (
        <>
          {/* 统计卡片网格 */}
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-3 mb-6">
            <StatCard label="内容总数" value={stats.contents_total} dotClass="bg-info" />
            <StatCard label="已发布" value={stats.published} dotClass="bg-success" />
            <StatCard label="待发布" value={stats.pending_publish} dotClass="bg-warning" />
            <StatCard label="评论总数" value={stats.comments_total} dotClass="bg-info" />
            <StatCard
              label="已回复率"
              value={`${(stats.replied_rate * 100).toFixed(0)}%`}
              dotClass="bg-success"
              sub={`${stats.replied} / ${stats.comments_total}`}
            />
          </div>

          {/* 各账号发布数明细 */}
          <h2 className="text-sm font-semibold mb-3 text-text-secondary uppercase tracking-wider">
            各账号明细
          </h2>
          <div className="bg-surface border border-border rounded-md overflow-hidden">
            <table className="w-full border-collapse text-[13px]">
              <thead>
                <tr>
                  {['账号', '平台', '总内容', '已发布', '待发布', '失败'].map((h) => (
                    <th
                      key={h}
                      className="text-left px-4 py-2 text-[11px] font-semibold uppercase tracking-wider text-text-tertiary border-b border-border bg-bg"
                    >
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {accountStats.length === 0 && (
                  <tr>
                    <td colSpan={6} className="px-4 py-8 text-center text-text-secondary">
                      暂无账号数据
                    </td>
                  </tr>
                )}
                {accountStats.map((row) => (
                  <tr key={row.account.id} className="hover:bg-surface-hover">
                    <td className="px-4 py-3 border-b border-border">{row.account.nickname}</td>
                    <td className="px-4 py-3 border-b border-border text-text-secondary">
                      {row.account.platform_label}
                    </td>
                    <td className="px-4 py-3 border-b border-border nums">{row.total}</td>
                    <td className="px-4 py-3 border-b border-border nums text-success">
                      {row.published}
                    </td>
                    <td className="px-4 py-3 border-b border-border nums text-warning">
                      {row.pending}
                    </td>
                    <td className="px-4 py-3 border-b border-border nums text-danger">
                      {row.failed}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="text-xs text-text-tertiary mt-4">
            数据为本地基础回显,不含趋势分析或归因(Spec NON-7)
          </div>
        </>
      )}
    </div>
  )
}

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
