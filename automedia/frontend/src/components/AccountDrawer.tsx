import { useEffect, useState } from 'react'
import { accountsApi, type Account, type Platform } from '@/lib/api'

/**
 * 账号添加/编辑抽屉 - 对照 accounts.html 的 .drawer。
 *
 * 模式:
 *   - 添加:填平台/昵称/主题 -> 保存 -> 可选立即登录
 *   - 编辑:改昵称/主题(平台不可改)
 *
 * 登录态获取(对照 accounts.html "打开浏览器登录"):
 *   保存账号后,点"打开浏览器登录"触发后端 Playwright 流程(阻塞,等用户在弹出的浏览器登录)
 */

const PLATFORM_OPTIONS: { value: Platform; label: string; helper?: string }[] = [
  { value: 'xhs', label: '小红书' },
  { value: 'dy', label: '抖音' },
  { value: 'ks', label: '快手' },
  { value: 'wx', label: '视频号(半自动)', helper: '视频号为半自动模式,AI 备好内容后需手动发布' },
]

interface Props {
  open: boolean
  /** 传入账号 = 编辑模式;undefined = 添加模式 */
  account?: Account | null
  onClose: () => void
  onSaved: () => void
}

export default function AccountDrawer({ open, account, onClose, onSaved }: Props) {
  const isEdit = !!account
  const [platform, setPlatform] = useState<Platform>('xhs')
  const [nickname, setNickname] = useState('')
  const [theme, setTheme] = useState('')
  const [saving, setSaving] = useState(false)
  const [loginLoading, setLoginLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [savedId, setSavedId] = useState<number | null>(null)

  useEffect(() => {
    if (open) {
      setPlatform(account?.platform ?? 'xhs')
      setNickname(account?.nickname ?? '')
      setTheme(account?.topic_theme ?? '')
      setError(null)
      setSavedId(account?.id ?? null)
    }
  }, [open, account])

  // 抽屉显隐 + overlay(对照 styles.css .drawer.show / .drawer-overlay.show)
  if (!open) return null

  const platformHelper = PLATFORM_OPTIONS.find((p) => p.value === platform)?.helper

  async function handleSave() {
    if (!nickname.trim()) {
      setError('昵称不能为空')
      return
    }
    setSaving(true)
    setError(null)
    try {
      if (isEdit && account) {
        await accountsApi.update(account.id, {
          nickname: nickname.trim(),
          topic_theme: theme.trim(),
        })
        setSavedId(account.id)
      } else {
        const created = await accountsApi.create({
          platform,
          nickname: nickname.trim(),
          topic_theme: theme.trim(),
        })
        setSavedId(created.id)
      }
      onSaved()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setSaving(false)
    }
  }

  async function handleLogin() {
    if (!savedId) {
      setError('请先保存账号')
      return
    }
    setLoginLoading(true)
    setError(null)
    try {
      // 阻塞调用:后端会开浏览器,用户登录后才返回
      await accountsApi.login(savedId, { timeout_seconds: 180 })
      onSaved()
      onClose()
    } catch (e) {
      setError(`登录失败: ${(e as Error).message}`)
    } finally {
      setLoginLoading(false)
    }
  }

  return (
    <>
      {/* overlay */}
      <div
        className="fixed inset-0 z-40 bg-black/50"
        onClick={onClose}
        aria-hidden="true"
      />

      {/* drawer */}
      <aside
        className="fixed top-0 right-0 bottom-0 z-50 bg-surface border-l border-border p-6 overflow-y-auto"
        style={{ width: 420 }}
        role="dialog"
        aria-modal="true"
        aria-label={isEdit ? '编辑账号' : '添加账号'}
      >
        <h2 className="text-lg font-semibold mb-6">{isEdit ? '编辑账号' : '添加账号'}</h2>

        {/* 平台 */}
        <div className="mb-4">
          <label className="block text-xs font-semibold mb-2 text-text-secondary">
            平台
          </label>
          <select
            className="w-full px-3 py-2 bg-bg border border-border-bright rounded-md text-text text-[13px] focus:outline-none focus:border-primary"
            style={{ boxShadow: 'none' }}
            value={platform}
            onChange={(e) => setPlatform(e.target.value as Platform)}
            disabled={isEdit} // 编辑时平台不可改
          >
            {PLATFORM_OPTIONS.map((p) => (
              <option key={p.value} value={p.value}>
                {p.label}
              </option>
            ))}
          </select>
          {platformHelper && (
            <div className="text-xs text-text-tertiary mt-1">{platformHelper}</div>
          )}
        </div>

        {/* 昵称 */}
        <div className="mb-4">
          <label className="block text-xs font-semibold mb-2 text-text-secondary">
            账号昵称
          </label>
          <input
            className="w-full px-3 py-2 bg-bg border border-border-bright rounded-md text-text text-[13px] focus:outline-none focus:border-primary"
            placeholder="如:科技前沿观察"
            value={nickname}
            onChange={(e) => setNickname(e.target.value)}
          />
        </div>

        {/* 主题 */}
        <div className="mb-4">
          <label className="block text-xs font-semibold mb-2 text-text-secondary">
            主题 / 领域
          </label>
          <input
            className="w-full px-3 py-2 bg-bg border border-border-bright rounded-md text-text text-[13px] focus:outline-none focus:border-primary"
            placeholder="如:AI / 科技 / 编程"
            value={theme}
            onChange={(e) => setTheme(e.target.value)}
          />
          <div className="text-xs text-text-tertiary mt-1">
            用于热点筛选和文案调性,多个用逗号分隔
          </div>
        </div>

        {/* 登录方式(对照 accounts.html,保存后才可登录) */}
        <div className="mb-4">
          <label className="block text-xs font-semibold mb-2 text-text-secondary">
            登录方式
          </label>
          <div className="bg-bg border border-border rounded-md p-3">
            <div className="text-[13px] text-text-secondary">
              {savedId
                ? '点击下方按钮打开浏览器,完成平台登录后自动获取登录态。'
                : '请先保存账号,再获取登录态。'}
            </div>
            <button
              className="w-full mt-3 px-3 py-2 border border-border-bright rounded-md text-[13px] font-semibold text-text hover:bg-surface disabled:opacity-50"
              onClick={handleLogin}
              disabled={!savedId || loginLoading}
            >
              {loginLoading ? '等待浏览器登录…' : '打开浏览器登录'}
            </button>
          </div>
          <div className="text-xs text-text-tertiary mt-1">
            登录态加密存储在本地,有效约 7 天
          </div>
        </div>

        {/* 错误提示 */}
        {error && (
          <div className="mb-4 px-3 py-2 rounded-md text-[13px] bg-danger-faint text-danger border border-danger/30">
            {error}
          </div>
        )}

        {/* 操作按钮 */}
        <div className="flex gap-2 mt-6">
          <button
            className="flex-1 px-3 py-2 bg-primary text-white rounded-md text-[13px] font-semibold hover:bg-primary-hover disabled:opacity-50"
            onClick={handleSave}
            disabled={saving}
          >
            {saving ? '保存中…' : isEdit ? '保存修改' : '保存账号'}
          </button>
          <button
            className="px-3 py-2 border border-border-bright rounded-md text-[13px] font-semibold text-text hover:bg-surface"
            onClick={onClose}
          >
            取消
          </button>
        </div>
      </aside>
    </>
  )
}
