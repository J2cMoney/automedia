import { useEffect, useState } from 'react'
import {
  accountsApi,
  type Account,
  type AuthStatus,
  type Platform,
} from '@/lib/api'
import Badge from '@/components/Badge'
import AccountDrawer from '@/components/AccountDrawer'

/**
 * 账号管理页 - Design-Brief SCREEN-2 + accounts.html。
 *
 * 对照 accounts.html 结构:
 *   1. 顶部:标题 + 平台筛选 + 添加按钮
 *   2. 表格:账号/平台/主题/登录态徽章/状态/最后活动/操作
 *   3. 删除二次确认弹窗(Danger 按钮 + AC-4 平台侧登录态提示)
 *   4. 添加/编辑抽屉(右侧滑出)
 */

// ---------- 平台元信息 ----------
const PLATFORM_BADGE: Record<Platform, { char: string; cls: string; label: string }> = {
  xhs: { char: '红', cls: 'bg-platform-xhs', label: '小红书' },
  dy: { char: '抖', cls: 'bg-platform-dy border border-white', label: '抖音' },
  ks: { char: '快', cls: 'bg-platform-ks', label: '快手' },
  wx: { char: '视', cls: 'bg-platform-wx', label: '视频号' },
}

/** 登录态徽章四态色(对照 accounts.html badge-success/danger,Design-Brief CMP-002)。 */
function authBadge(status: AuthStatus, hasAuth: boolean) {
  if (!hasAuth || status === 'unknown') {
    return <Badge variant="neutral">未登录</Badge>
  }
  if (status === 'valid') return <Badge variant="success">有效</Badge>
  return <Badge variant="danger">失效</Badge>
}

export default function Accounts() {
  const [list, setList] = useState<Account[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [filter, setFilter] = useState<Platform | ''>('')

  // 抽屉
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [editing, setEditing] = useState<Account | null>(null)

  // 删除确认弹窗
  const [deleting, setDeleting] = useState<Account | null>(null)
  const [deleteLoading, setDeleteLoading] = useState(false)

  async function load() {
    setLoading(true)
    setError(null)
    try {
      const data = await accountsApi.list(
        filter ? { platform: filter } : undefined,
      )
      setList(data)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filter])

  function openAdd() {
    setEditing(null)
    setDrawerOpen(true)
  }

  function openEdit(acc: Account) {
    setEditing(acc)
    setDrawerOpen(true)
  }

  async function confirmDelete() {
    if (!deleting) return
    setDeleteLoading(true)
    try {
      await accountsApi.remove(deleting.id)
      setDeleting(null)
      await load()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setDeleteLoading(false)
    }
  }

  async function handleHealthCheck(acc: Account) {
    try {
      await accountsApi.healthCheck(acc.id)
      await load()
    } catch (e) {
      setError((e as Error).message)
    }
  }

  return (
    <div className="p-6 max-w-[1400px]">
      {/* 顶部 */}
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-lg font-semibold">
          账号管理{' '}
          <span className="text-text-tertiary text-sm font-normal">
            ({list.length} / 10)
          </span>
        </h1>
        <div className="flex items-center gap-2">
          <select
            className="px-3 py-2 bg-bg border border-border-bright rounded-md text-text text-[13px] focus:outline-none focus:border-primary"
            value={filter}
            onChange={(e) => setFilter(e.target.value as Platform | '')}
          >
            <option value="">全部平台</option>
            <option value="xhs">小红书</option>
            <option value="dy">抖音</option>
            <option value="ks">快手</option>
            <option value="wx">视频号</option>
          </select>
          <button
            className="inline-flex items-center gap-1 px-3 py-2 bg-primary text-white rounded-md text-[13px] font-semibold hover:bg-primary-hover"
            onClick={openAdd}
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
              <path d="M12 5v14M5 12h14" />
            </svg>
            添加账号
          </button>
        </div>
      </div>

      {/* 错误提示 */}
      {error && (
        <div className="mb-4 px-3 py-2 rounded-md text-[13px] bg-danger-faint text-danger border border-danger/30">
          {error}
        </div>
      )}

      {/* 表格(对照 accounts.html .table-wrap) */}
      <div className="bg-surface border border-border rounded-md overflow-hidden">
        <table className="w-full border-collapse text-[13px]">
          <thead>
            <tr>
              {['账号', '平台', '主题', '登录态', '状态', '最后活动', '操作'].map(
                (h) => (
                  <th
                    key={h}
                    className="text-left px-4 py-2 text-[11px] font-semibold uppercase tracking-wider text-text-tertiary border-b border-border bg-bg"
                  >
                    {h}
                  </th>
                ),
              )}
            </tr>
          </thead>
          <tbody>
            {loading && (
              <tr>
                <td colSpan={7} className="px-4 py-12 text-center text-text-secondary">
                  加载中…
                </td>
              </tr>
            )}
            {!loading && list.length === 0 && (
              <tr>
                <td colSpan={7} className="px-4 py-12 text-center text-text-secondary">
                  还没有账号,点右上角"添加账号"添加第一个
                </td>
              </tr>
            )}
            {list.map((acc) => {
              const p = PLATFORM_BADGE[acc.platform]
              return (
                <tr key={acc.id} className="hover:bg-surface-hover">
                  <td className="px-4 py-3 border-b border-border">
                    <div className="flex items-center gap-2">
                      <div className="w-7 h-7 rounded-full bg-border-bright flex items-center justify-center text-xs shrink-0">
                        {acc.nickname.slice(0, 1)}
                      </div>
                      {acc.nickname}
                    </div>
                  </td>
                  <td className="px-4 py-3 border-b border-border">
                    <div className="flex items-center gap-1.5">
                      <span
                        className={`inline-flex items-center justify-center w-4 h-4 rounded-sm text-[10px] text-white ${p.cls}`}
                      >
                        {p.char}
                      </span>
                      {p.label}
                    </div>
                  </td>
                  <td className="px-4 py-3 border-b border-border">{acc.topic_theme || '—'}</td>
                  <td className="px-4 py-3 border-b border-border">
                    {authBadge(acc.auth_status, acc.has_auth)}
                  </td>
                  <td className="px-4 py-3 border-b border-border">
                    {acc.status === 'active' ? (
                      <Badge variant="neutral">启用</Badge>
                    ) : (
                      <Badge variant="warning">禁用</Badge>
                    )}
                  </td>
                  <td className="px-4 py-3 border-b border-border text-text-tertiary text-xs nums">
                    {new Date(acc.updated_at).toLocaleString('zh-CN', {
                      month: '2-digit',
                      day: '2-digit',
                      hour: '2-digit',
                      minute: '2-digit',
                    })}
                  </td>
                  <td className="px-4 py-3 border-b border-border">
                    <div className="flex gap-1">
                      {acc.auth_status === 'invalid' && (
                        <button
                          className="px-2 py-1 text-xs border border-border-bright rounded text-text hover:bg-bg"
                          onClick={() => openEdit(acc)}
                          title="重新登录"
                        >
                          重新登录
                        </button>
                      )}
                      <button
                        className="px-2 py-1 text-xs text-text-secondary hover:bg-bg hover:text-text rounded"
                        onClick={() => handleHealthCheck(acc)}
                        title="健康检查"
                      >
                        检查
                      </button>
                      <button
                        className="px-2 py-1 text-xs text-text-secondary hover:bg-bg hover:text-text rounded"
                        onClick={() => openEdit(acc)}
                      >
                        编辑
                      </button>
                      <button
                        className="px-2 py-1 text-xs text-text-secondary hover:bg-bg hover:text-text rounded"
                        onClick={() => setDeleting(acc)}
                      >
                        删除
                      </button>
                    </div>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      <div className="text-xs text-text-tertiary mt-4">
        共 {list.length} 个账号 · 矩阵上限 10 · 删除账号将清除其所有内容、评论、任务记录
      </div>

      {/* 添加/编辑抽屉 */}
      <AccountDrawer
        open={drawerOpen}
        account={editing}
        onClose={() => setDrawerOpen(false)}
        onSaved={load}
      />

      {/* 删除二次确认弹窗(对照 accounts.html .modal,AC-4 平台侧登录态提示) */}
      {deleting && (
        <>
          <div className="fixed inset-0 z-[60] bg-black/60" />
          <div
            className="fixed inset-0 z-[61] flex items-center justify-center"
            role="dialog"
            aria-modal="true"
            aria-label="删除账号确认"
          >
            <div className="bg-surface border border-border rounded-lg p-6 w-[90%] max-w-[400px] shadow-md">
              <h2 className="text-lg font-semibold mb-2">删除账号</h2>
              <div className="text-[13px] text-text-secondary mb-6">
                确认删除{' '}
                <strong className="text-text">
                  {deleting.nickname} · {PLATFORM_BADGE[deleting.platform].label}
                </strong>
                ?
                <br />
                <br />
                该账号的<strong>所有内容、评论、任务记录</strong>将一并清除,且无法恢复。
                <br />
                <br />
                <span className="text-xs text-text-tertiary">
                  注意:平台侧的登录态需你自行到{PLATFORM_BADGE[deleting.platform].label}{' '}
                  App 退出,本工具无法主动登出。
                </span>
              </div>
              <div className="flex gap-2 justify-end">
                <button
                  className="px-3 py-2 border border-border-bright rounded-md text-[13px] font-semibold text-text hover:bg-bg"
                  onClick={() => setDeleting(null)}
                  disabled={deleteLoading}
                >
                  取消
                </button>
                <button
                  className="px-3 py-2 bg-danger text-white rounded-md text-[13px] font-semibold hover:brightness-110 disabled:opacity-50"
                  onClick={confirmDelete}
                  disabled={deleteLoading}
                >
                  {deleteLoading ? '删除中…' : '确认删除'}
                </button>
              </div>
            </div>
          </div>
        </>
      )}
    </div>
  )
}
