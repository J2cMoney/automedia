import { useEffect, useState, useCallback } from 'react'
import {
  commentsApi,
  tasksApi,
  type Comment,
  type CommentStatus,
} from '@/lib/api'
import Badge from '@/components/Badge'

/**
 * 评论中心 - Design-Brief SCREEN-4。
 *
 * 对照 Design-Brief SCREEN-4:
 *   - 表格:内容/评论者/评论内容/AI 回复/状态徽章/时间
 *   - 按状态筛选(全部/待回/已回/转人工)
 *   - 触发回评按钮(调 commentsApi.triggerReply,轮询任务状态)
 *
 * 五态覆盖:
 *   - 加载:骨架
 *   - 空:无评论 → 引导先发布内容
 *   - 错误:加载失败提示
 *   - 成功:评论表格
 *   - 无权限:后端未连接
 */

const STATUS_LABEL: Record<CommentStatus, string> = {
  pending: '待回复',
  replied: '已回复',
  manual: '转人工',
}

function statusBadge(status: CommentStatus) {
  if (status === 'replied') return <Badge variant="success">已回复</Badge>
  if (status === 'pending') return <Badge variant="warning">待回复</Badge>
  return <Badge variant="neutral">转人工</Badge>
}

export default function Comments() {
  const [list, setList] = useState<Comment[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [filter, setFilter] = useState<CommentStatus | ''>('')

  // 回评任务(触发后轮询)
  const [replyContentId, setReplyContentId] = useState<number | null>(null)
  const [replyTaskId, setReplyTaskId] = useState<number | null>(null)
  const [replyMsg, setReplyMsg] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await commentsApi.list(filter ? { status: filter } : {})
      setList(data)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setLoading(false)
    }
  }, [filter])

  useEffect(() => {
    load()
  }, [load])

  // 轮询回评任务状态(范式照搬 Pipeline 轮询)
  useEffect(() => {
    if (!replyTaskId) return
    let active = true
    const poll = async () => {
      try {
        const info = await tasksApi.get(replyTaskId)
        if (!active) return
        if (info.status === 'finished' || info.status === 'failed') {
          setReplyMsg(
            info.status === 'finished'
              ? '✓ 回评完成,刷新列表查看'
              : `✕ 回评失败:${info.error_log || '未知错误'}`,
          )
          await load()
          return
        }
      } catch {
        /* 忽略 */
      }
      setTimeout(poll, 2000)
    }
    poll()
    return () => {
      active = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [replyTaskId])

  async function triggerReply(contentId: number) {
    setError(null)
    setReplyMsg(null)
    setReplyContentId(contentId)
    try {
      const resp = await commentsApi.triggerReply(contentId)
      setReplyTaskId(resp.task_id)
      setReplyMsg('回评进行中…')
    } catch (e) {
      setError((e as Error).message)
    }
  }

  // 唯一的 content_id 集合(用于回评触发按钮)
  const contentIds = Array.from(new Set(list.map((c) => c.content_id)))

  // 按内容分组,便于按内容触发回评
  const groupedByContent = contentIds.map((cid) => ({
    contentId: cid,
    comments: list.filter((c) => c.content_id === cid),
  }))

  // ---------- 渲染 ----------

  if (loading) {
    return (
      <div className="p-6 max-w-[1400px]">
        <h1 className="text-lg font-semibold mb-6">评论中心</h1>
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
          <h1 className="text-lg font-semibold">评论中心</h1>
          <div className="text-sm text-text-secondary mt-1">
            {list.length} 条评论 · 按内容分组
          </div>
        </div>
        <select
          className="px-3 py-2 bg-bg border border-border-bright rounded-md text-text text-[13px] focus:outline-none focus:border-primary"
          value={filter}
          onChange={(e) => setFilter(e.target.value as CommentStatus | '')}
        >
          <option value="">全部状态</option>
          <option value="pending">待回复</option>
          <option value="replied">已回复</option>
          <option value="manual">转人工</option>
        </select>
      </div>

      {/* 错误提示 */}
      {error && !isBackendDown && (
        <div className="mb-4 px-3 py-2 rounded-md text-[13px] bg-danger-faint text-danger border border-danger/30">
          {error}
        </div>
      )}

      {/* 回评进度 */}
      {replyMsg && (
        <div
          className={`mb-4 px-3 py-2 rounded-md text-[13px] border ${
            replyMsg.startsWith('✓')
              ? 'bg-success-faint text-success border-success/30'
              : replyMsg.startsWith('✕')
                ? 'bg-danger-faint text-danger border-danger/30'
                : 'bg-info-faint text-info border-info/30'
          }`}
        >
          {replyMsg}
        </div>
      )}

      {/* 无权限:后端未连接 */}
      {isBackendDown && (
        <div className="text-center py-16">
          <div className="text-base font-semibold mb-2">无法连接后端服务</div>
          <div className="text-sm text-text-secondary mb-4">请先启动后端服务</div>
          <button
            className="px-4 py-2 bg-primary text-white rounded-md text-[13px] font-semibold hover:bg-primary-hover"
            onClick={load}
          >
            重新连接
          </button>
        </div>
      )}

      {/* 空态 */}
      {!isBackendDown && list.length === 0 && !error && (
        <div className="text-center py-16">
          <div className="text-base font-semibold mb-2">
            {filter ? `没有${STATUS_LABEL[filter as CommentStatus]}状态的评论` : '还没有评论'}
          </div>
          <div className="text-sm text-text-secondary mb-6 max-w-md mx-auto">
            评论来自已发布的内容。先到流水线页发布内容,平台有新评论后这里会显示。
          </div>
          <a
            href="/pipeline"
            className="inline-block px-4 py-2 bg-primary text-white rounded-md text-[13px] font-semibold hover:bg-primary-hover"
          >
            去流水线
          </a>
        </div>
      )}

      {/* 评论列表(按内容分组) */}
      {!isBackendDown && list.length > 0 && (
        <div className="space-y-6">
          {groupedByContent.map((group) => (
            <div key={group.contentId} className="bg-surface border border-border rounded-md overflow-hidden">
              {/* 内容头 */}
              <div className="flex items-center justify-between px-4 py-3 border-b border-border bg-bg">
                <div className="text-sm">
                  <span className="text-text-tertiary">内容 #{group.contentId}</span>
                  <span className="text-text-secondary ml-2">
                    {group.comments.length} 条评论 ·{' '}
                    {group.comments.filter((c) => c.status === 'replied').length} 已回复
                  </span>
                </div>
                <button
                  className="px-3 py-1.5 bg-primary text-white rounded-md text-xs font-semibold hover:bg-primary-hover disabled:opacity-50"
                  onClick={() => triggerReply(group.contentId)}
                  disabled={replyContentId === group.contentId && replyMsg === '回评进行中…'}
                >
                  {replyContentId === group.contentId && replyMsg === '回评进行中…'
                    ? '回评中…'
                    : '触发回评'}
                </button>
              </div>
              {/* 评论表格 */}
              <table className="w-full border-collapse text-[13px]">
                <thead>
                  <tr>
                    {['评论者', '评论内容', 'AI 回复', '状态', '时间'].map((h) => (
                      <th
                        key={h}
                        className="text-left px-4 py-2 text-[11px] font-semibold uppercase tracking-wider text-text-tertiary border-b border-border"
                      >
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {group.comments.map((c) => (
                    <tr key={c.id} className="hover:bg-surface-hover">
                      <td className="px-4 py-2 border-b border-border text-text-secondary">
                        {c.author || '匿名'}
                      </td>
                      <td className="px-4 py-2 border-b border-border max-w-[280px]">
                        <div className="truncate">{c.text}</div>
                      </td>
                      <td className="px-4 py-2 border-b border-border max-w-[280px] text-text-secondary">
                        {c.ai_reply ? (
                          <div className="truncate">{c.ai_reply}</div>
                        ) : (
                          <span className="text-text-tertiary">—</span>
                        )}
                      </td>
                      <td className="px-4 py-2 border-b border-border">
                        {statusBadge(c.status)}
                      </td>
                      <td className="px-4 py-2 border-b border-border text-text-tertiary text-xs nums">
                        {new Date(c.created_at).toLocaleString('zh-CN', {
                          month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
                        })}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
