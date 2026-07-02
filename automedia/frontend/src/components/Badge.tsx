import type { ReactNode } from 'react'

/**
 * 状态徽章 - Design-Brief CMP-002 + styles.css .badge。
 *
 * 四态语义色(DP-002 核心语言,颜色 + 文字双编码,a11y 不单靠色):
 *   success(绿) / warning(黄) / danger(红) / info(蓝) / neutral(灰)
 *
 * 对照 styles.css:
 *   .badge-success/warning/danger/info/neutral + ::before 圆点
 */

type BadgeVariant = 'success' | 'warning' | 'danger' | 'info' | 'neutral'

const VARIANT_CLASS: Record<BadgeVariant, string> = {
  success: 'bg-success-faint text-success',
  warning: 'bg-warning-faint text-warning',
  danger: 'bg-danger-faint text-danger',
  info: 'bg-info-faint text-info',
  neutral: 'bg-surface text-text-secondary border border-border',
}

const DOT_CLASS: Record<BadgeVariant, string> = {
  success: 'bg-success',
  warning: 'bg-warning',
  danger: 'bg-danger',
  info: 'bg-info',
  neutral: 'bg-text-tertiary',
}

interface Props {
  variant: BadgeVariant
  children: ReactNode
  /** info 态的圆点带 pulse 动画(对照 styles.css .badge-info::before) */
  pulse?: boolean
}

export default function Badge({ variant, children, pulse = false }: Props) {
  return (
    <span
      className={[
        'inline-flex items-center gap-1 px-2 py-0.5 rounded-sm text-[11px] font-semibold leading-relaxed',
        VARIANT_CLASS[variant],
      ].join(' ')}
    >
      <span
        className={[
          'w-1.5 h-1.5 rounded-full',
          DOT_CLASS[variant],
          pulse ? 'animate-pulse' : '',
        ].join(' ')}
      />
      {children}
    </span>
  )
}
