import { NavLink } from 'react-router-dom'

/**
 * 侧边栏导航 - Design-Brief §2.1 导航结构 + accounts.html 的 sidebar。
 *
 * 6 项导航(对应 SCREEN):
 *   1. 仪表盘(SCREEN-1)   2. 内容流水线(SCREEN-3)  3. 账号管理(SCREEN-2)
 *   4. 评论中心(SCREEN-4) 5. 数据概览(SCREEN-5)    6. 日志与设置(SCREEN-6)
 *
 * 当前页高亮对照 styles.css .nav-item.active:
 *   文字主色 + 背景变 surface + 左边框 primary 色
 */
interface NavItem {
  to: string
  label: string
  icon: React.ReactNode
}

const NAV_ITEMS: NavItem[] = [
  {
    to: '/',
    label: '仪表盘',
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5}>
        <rect x="3" y="3" width="7" height="7" rx="1" />
        <rect x="14" y="3" width="7" height="7" rx="1" />
        <rect x="3" y="14" width="7" height="7" rx="1" />
        <rect x="14" y="14" width="7" height="7" rx="1" />
      </svg>
    ),
  },
  {
    to: '/pipeline',
    label: '内容流水线',
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5}>
        <circle cx="5" cy="12" r="2" />
        <circle cx="12" cy="12" r="2" />
        <circle cx="19" cy="12" r="2" />
        <path d="M7 12h3M14 12h3" />
      </svg>
    ),
  },
  {
    to: '/accounts',
    label: '账号管理',
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5}>
        <circle cx="9" cy="8" r="3" />
        <path d="M3 20c0-3 3-5 6-5s6 2 6 5" />
        <circle cx="17" cy="9" r="2.5" />
        <path d="M15 20c0-2 1.5-4 4-4" />
      </svg>
    ),
  },
  {
    to: '/comments',
    label: '评论中心',
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5}>
        <path d="M21 12c0 4-4 8-9 8-1.5 0-3-.4-4-1l-4 1 1-4c-.6-1-1-2.5-1-4 0-4 4-8 9-8s8 4 8 8z" />
      </svg>
    ),
  },
  {
    to: '/data',
    label: '数据概览',
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5}>
        <path d="M3 3v18h18" />
        <rect x="7" y="10" width="3" height="8" />
        <rect x="12" y="6" width="3" height="12" />
        <rect x="17" y="13" width="3" height="5" />
      </svg>
    ),
  },
  {
    to: '/settings',
    label: '日志与设置',
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5}>
        <circle cx="12" cy="12" r="3" />
        <path d="M19 12c0 .7-.1 1.4-.2 2l2 1.5-2 3.5-2.4-1c-1 .8-2.2 1.4-3.4 1.8L13 22h-4l-.4-2.5c-1.2-.4-2.4-1-3.4-1.8l-2.4 1-2-3.5 2-1.5c-.1-.6-.2-1.3-.2-2s.1-1.4.2-2l-2-1.5 2-3.5 2.4 1c1-.8 2.2-1.4 3.4-1.8L9 2h4l.4 2.5c1.2.4 2.4 1 3.4 1.8l2.4-1 2 3.5-2 1.5c.1.6.2 1.3.2 2z" />
      </svg>
    ),
  },
]

export default function Sidebar() {
  return (
    <aside
      className="sticky top-0 h-screen flex flex-col border-r border-border bg-bg py-4"
      style={{ width: 220 }}
    >
      {/* Logo */}
      <div className="px-4 mb-6 text-lg font-semibold tracking-tight">
        AutoMedia<span className="text-primary">.</span>
      </div>

      {/* 导航 */}
      <nav className="flex-1">
        {NAV_ITEMS.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.to === '/'}
            className={({ isActive }) =>
              [
                'flex items-center gap-2 px-4 py-2 text-[13px] border-l-2 transition-colors',
                isActive
                  ? 'text-text bg-surface border-primary' // .nav-item.active
                  : 'text-text-secondary border-transparent hover:bg-surface hover:text-text', // .nav-item
              ].join(' ')
            }
          >
            <span className="w-4 h-4 shrink-0 opacity-80">{item.icon}</span>
            {item.label}
          </NavLink>
        ))}
      </nav>

      {/* 底部 */}
      <div className="px-4 py-3 text-xs text-text-tertiary border-t border-border">
        v1.0 · 本地运行
      </div>
    </aside>
  )
}
