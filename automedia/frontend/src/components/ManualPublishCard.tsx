import { useState } from 'react'
import Badge from './Badge'
import type { WxPackage } from '../lib/api'

/**
 * 视频号手动发布卡片 - Design-Brief CMP-007。
 *
 * 视频号无开放发布 API,走半自动模式:后端把内容打包好(标题/正文/标签/复制文案/
 * 视频号助手链接),前端给用户一键复制 + 跳转助手页,用户手动粘贴发布。
 *
 * 对照 ContentCard 的卡片范式:bg-surface + border-border,字段密度紧凑。
 * 平台图标沿用 Accounts 页 wx 元信息:绿底(bg-platform-wx)"视"字。
 */
interface Props {
  pack: WxPackage
}

export function ManualPublishCard({ pack }: Props) {
  const [copied, setCopied] = useState(false)

  // 一键复制发布文案(标题+正文+标签拼成可粘贴的文本)
  async function handleCopy() {
    try {
      await navigator.clipboard.writeText(pack.copy_text)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {
      /* 剪贴板权限被拒或不可用,忽略:用户仍可手动选中复制 */
    }
  }

  return (
    <div className="p-3 bg-surface border border-border rounded-md">
      {/* 卡片头:视频号绿底"视"字图标 + 待发布徽章 */}
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <span className="inline-flex items-center justify-center w-6 h-6 rounded-sm bg-platform-wx text-white text-xs font-medium">
            视
          </span>
          <span className="text-sm font-medium">视频号待发布</span>
        </div>
        <Badge variant="warning">待发布</Badge>
      </div>

      {/* 字段区:标题/正文/标签/视频路径 */}
      <div className="space-y-2 text-[13px]">
        {pack.title && <div className="font-medium truncate">{pack.title}</div>}
        {pack.body && <div className="text-text-secondary line-clamp-2">{pack.body}</div>}
        {pack.tags.length > 0 && (
          <div className="flex flex-wrap gap-1">
            {pack.tags.map((t, i) => (
              <span key={i} className="text-xs px-2 py-0.5 bg-bg rounded-sm text-info">
                {t}
              </span>
            ))}
          </div>
        )}
        <div className="text-xs text-text-tertiary truncate">视频: {pack.video_path}</div>
      </div>

      {/* 操作按钮:一键复制 + 打开视频号助手 */}
      <div className="flex gap-2 mt-3">
        <button
          onClick={handleCopy}
          className="px-3 py-2 border border-border-bright rounded-md text-[13px] font-semibold text-text hover:bg-surface-hover"
        >
          {copied ? '已复制 ✓' : '一键复制'}
        </button>
        <a
          href={pack.channels_url}
          target="_blank"
          rel="noopener noreferrer"
          className="px-3 py-2 bg-primary text-white rounded-md text-[13px] font-semibold hover:bg-primary-hover"
        >
          打开视频号助手
        </a>
      </div>

      <div className="text-xs text-text-tertiary mt-2">
        视频号需手动发布,内容已为你打包好
      </div>
    </div>
  )
}
