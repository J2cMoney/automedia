import { useEffect, useState, useCallback } from 'react'
import {
  topicsApi,
  contentsApi,
  accountsApi,
  tasksApi,
  type Topic,
  type Content,
  type Account,
  type TaskInfo,
} from '@/lib/api'
import Badge from '@/components/Badge'

/**
 * 内容流水线页 - Design-Brief SCREEN-3 + pipeline.html 原型。
 *
 * Phase 3 范围:热点采集 → 文案生成 → 视频脚本 三个节点。
 * (视频混剪/分发/回评是 Phase 4/5 的,这里显示为 pending 占位)
 *
 * 对照 pipeline.html:
 *   - 横向流水线节点(CMP-006:图标+状态色+耗时,四态:待执行/运行中/完成/失败)
 *   - 顶部:选题来源 + 状态摘要
 *   - 底部:产出物列表(文案/脚本)
 *
 * 五态覆盖(Spec 6.2 SCREEN-3):
 *   - 空态:无账号 → 引导去账号管理;有账号无选题 → "开始采集热点"
 *   - 加载态:节点转圈
 *   - 错误态:失败节点红色 + 错误摘要 + 重试
 *   - 成功态:绿色节点链 + 产出物
 *   - 无权限态:无可用账号 → "请先配置账号"
 */

// 流水线节点状态(DP-002 四态色)
type NodeStatus = 'pending' | 'running' | 'done' | 'failed'

const NODE_STYLE: Record<
  NodeStatus,
  { border: string; icon: string; iconBg: string; label: string }
> = {
  pending: {
    border: 'border-border',
    icon: '○',
    iconBg: 'bg-bg text-text-tertiary',
    label: 'text-text-tertiary',
  },
  running: {
    border: 'border-info',
    icon: '◐',
    iconBg: 'bg-info-faint text-info animate-pulse',
    label: 'text-info',
  },
  done: {
    border: 'border-success',
    icon: '✓',
    iconBg: 'bg-success-faint text-success',
    label: 'text-success',
  },
  failed: {
    border: 'border-danger',
    icon: '✕',
    iconBg: 'bg-danger-faint text-danger',
    label: 'text-danger',
  },
}

export default function Pipeline() {
  const [accounts, setAccounts] = useState<Account[]>([])
  const [topics, setTopics] = useState<Topic[]>([])
  const [contents, setContents] = useState<Content[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // 选中的账号(决定为哪个号跑流水线)
  const [selectedAccountId, setSelectedAccountId] = useState<number | null>(null)

  // 爬取任务状态(异步任务轮询)
  const [crawlTaskId, setCrawlTaskId] = useState<number | null>(null)
  const [crawlTask, setCrawlTask] = useState<TaskInfo | null>(null)

  // 生成中的 content(展示文案生成进度)
  const [generating, setGenerating] = useState(false)

  // 排除词(Spec FLOW-1 MUST,逗号分隔输入)
  const [excludeWordsInput, setExcludeWordsInput] = useState('')

  // 详情抽屉
  const [viewContent, setViewContent] = useState<Content | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [accs, tps, cts] = await Promise.all([
        accountsApi.list(),
        topicsApi.list(),
        contentsApi.list(),
      ])
      setAccounts(accs)
      setTopics(tps)
      setContents(cts)
      // 默认选第一个有效登录的账号
      if (selectedAccountId === null && accs.length > 0) {
        setSelectedAccountId(accs[0].id)
      }
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setLoading(false)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    load()
  }, [load])

  // 轮询爬取任务状态
  useEffect(() => {
    if (!crawlTaskId) return
    let active = true
    const poll = async () => {
      try {
        const info = await tasksApi.get(crawlTaskId)
        if (!active) return
        setCrawlTask(info)
        if (info.status === 'finished' || info.status === 'failed') {
          // 完成后刷新选题列表
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
  }, [crawlTaskId])

  async function startCrawl() {
    if (!selectedAccountId) return
    setError(null)
    try {
      // 解析排除词(逗号/空格分隔,Spec FLOW-1 MUST)
      const exclude_words = excludeWordsInput
        .split(/[,\s，、]+/)
        .map((s) => s.trim())
        .filter(Boolean)
      const resp = await topicsApi.crawl({
        account_id: selectedAccountId,
        exclude_words,
        max_results: 20,
      })
      setCrawlTaskId(resp.task_id)
      setCrawlTask(null)
    } catch (e) {
      setError((e as Error).message)
    }
  }

  async function generateCopy(topic: Topic) {
    if (!selectedAccountId) return
    setGenerating(true)
    setError(null)
    try {
      await topicsApi.adopt(topic.id)
      await topicsApi.generate(topic.id, { account_id: selectedAccountId })
      await load()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setGenerating(false)
    }
  }

  // ---------- 计算流水线节点状态 ----------
  const selectedAccount = accounts.find((a) => a.id === selectedAccountId)
  const candidateTopics = topics.filter((t) => t.status === 'candidate')
  const adoptedTopics = topics.filter((t) => t.status === 'adopted')
  const accountContents = contents.filter((c) => c.account_id === selectedAccountId)
  const successContents = accountContents.filter((c) => c.status === 'pending_review')
  const failedContents = accountContents.filter((c) => c.status === 'failed')

  // 爬取节点状态
  const crawlNode: NodeStatus = crawlTask
    ? crawlTask.status === 'finished'
      ? 'done'
      : crawlTask.status === 'failed'
        ? 'failed'
        : 'running'
    : candidateTopics.length > 0 || adoptedTopics.length > 0
      ? 'done'
      : 'pending'

  // 文案生成节点状态
  const copyNode: NodeStatus = generating
    ? 'running'
    : failedContents.length > 0 && successContents.length === 0
      ? 'failed'
      : successContents.length > 0
        ? 'done'
        : 'pending'

  // 视频脚本节点(脚本随文案一起产出,有成功文案就有脚本)
  const scriptNode: NodeStatus = copyNode === 'done' ? 'done' : copyNode

  // 后续 Phase 4/5 的节点,pending 占位
  const pipelineNodes = [
    { key: 'hotspot', name: '热点采集', status: crawlNode, detail: crawlStatusDetail() },
    { key: 'copy', name: '文案生成', status: copyNode, detail: copyStatusDetail() },
    { key: 'script', name: '视频脚本', status: scriptNode, detail: scriptStatusDetail() },
    { key: 'video', name: '视频混剪', status: 'pending' as NodeStatus, detail: 'Phase 4' },
    { key: 'publish', name: '分发', status: 'pending' as NodeStatus, detail: 'Phase 5' },
    { key: 'comment', name: '回评', status: 'pending' as NodeStatus, detail: 'Phase 5' },
  ]

  function crawlStatusDetail(): string {
    if (crawlTask?.status === 'running') return '采集中…'
    if (crawlTask?.status === 'failed') return '采集失败'
    if (crawlNode === 'done') return `${candidateTopics.length + adoptedTopics.length} 条候选`
    return '等待中'
  }
  function copyStatusDetail(): string {
    if (generating) return '生成中…'
    if (copyNode === 'done') return `已出稿 ${successContents.length} 篇`
    if (copyNode === 'failed') return '生成失败'
    return '等待中'
  }
  function scriptStatusDetail(): string {
    if (scriptNode === 'done') {
      const scenes = successContents[0]?.video_script?.length ?? 0
      return scenes ? `分镜 ${scenes} 个` : '已生成'
    }
    return '等待中'
  }

  // ---------- 渲染 ----------
  if (loading) {
    return (
      <div className="p-6 max-w-[1400px]">
        <h1 className="text-lg font-semibold mb-6">内容流水线</h1>
        <div className="text-center py-16 text-text-secondary text-sm">加载中…</div>
      </div>
    )
  }

  return (
    <div className="p-6 max-w-[1400px]">
      {/* 顶部 */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-lg font-semibold">内容流水线</h1>
          {selectedAccount && (
            <div className="text-sm text-text-secondary mt-1">
              {selectedAccount.nickname} · {platformLabel(selectedAccount.platform)} · 主题「{selectedAccount.topic_theme || '未配置'}」
            </div>
          )}
        </div>
        <div className="flex items-center gap-2">
          <select
            className="px-3 py-2 bg-bg border border-border-bright rounded-md text-text text-[13px] focus:outline-none focus:border-primary"
            value={selectedAccountId ?? ''}
            onChange={(e) => setSelectedAccountId(Number(e.target.value))}
          >
            {accounts.length === 0 && <option value="">无账号</option>}
            {accounts.map((a) => (
              <option key={a.id} value={a.id}>
                {a.nickname} · {platformLabel(a.platform)}
              </option>
            ))}
          </select>
        </div>
      </div>

      {/* 错误提示 */}
      {error && (
        <div className="mb-4 px-3 py-2 rounded-md text-[13px] bg-danger-faint text-danger border border-danger/30">
          {error}
        </div>
      )}

      {/* 五态:无可用账号 */}
      {accounts.length === 0 ? (
        <EmptyState
          title="还没有账号"
          hint="内容流水线需要一个有效登录的账号来采集热点和生成文案。去账号管理添加你的第一个号。"
          ctaLabel="去账号管理"
          ctaTo="/accounts"
        />
      ) : accounts.length > 0 && accounts.every((a) => a.auth_status !== 'valid') ? (
        <EmptyState
          title="账号登录态失效"
          hint="所有账号的登录态都失效了,无法采集热点。请先在账号管理重新登录。"
          ctaLabel="去账号管理"
          ctaTo="/accounts"
        />
      ) : (
        <>
          {/* 流水线状态摘要 */}
          <div className="flex items-center gap-3 mb-6">
            {crawlNode === 'running' || generating ? (
              <Badge variant="info" pulse>
                运行中
              </Badge>
            ) : crawlNode === 'done' || copyNode === 'done' ? (
              <Badge variant="success">有产出</Badge>
            ) : (
              <Badge variant="neutral">待启动</Badge>
            )}
            <span className="text-sm text-text-secondary">
              {candidateTopics.length > 0
                ? `${candidateTopics.length} 条候选选题待确认`
                : '今日尚未采集热点'}
            </span>
          </div>

          {/* 横向流水线(对照 pipeline.html .pipeline) */}
          <div className="flex items-stretch gap-2 overflow-x-auto pb-2">
            {pipelineNodes.map((node, i) => (
              <div key={node.key} className="flex items-stretch gap-2">
                <PipelineNode
                  name={node.name}
                  status={node.status}
                  detail={node.detail}
                  onRetry={node.key === 'hotspot' ? startCrawl : undefined}
                />
                {i < pipelineNodes.length - 1 && (
                  <div className="flex items-center text-text-tertiary text-lg px-1">→</div>
                )}
              </div>
            ))}
          </div>

          {/* 操作区:采集热点 */}
          <Section title="热点采集">
            <div className="flex items-center justify-between mb-3">
              <span className="text-sm text-text-secondary">
                {crawlTask?.status === 'running'
                  ? '正在采集热榜,请稍候…'
                  : candidateTopics.length > 0
                    ? '已采集到以下候选选题,选择采纳后生成文案'
                    : '点按钮采集账号所在平台的热榜'}
              </span>
              <button
                className="px-3 py-2 bg-primary text-white rounded-md text-[13px] font-semibold hover:bg-primary-hover disabled:opacity-50 shrink-0"
                onClick={startCrawl}
                disabled={crawlNode === 'running'}
              >
                {crawlNode === 'running' ? '采集中…' : '采集热点'}
              </button>
            </div>
            {/* 排除词(Spec FLOW-1 MUST:支持排除词) */}
            <div className="flex items-center gap-2 mb-3">
              <label className="text-xs text-text-tertiary shrink-0">排除词</label>
              <input
                type="text"
                className="flex-1 px-3 py-1.5 bg-bg border border-border rounded-md text-[13px] text-text focus:outline-none focus:border-primary"
                placeholder="逗号分隔,命中的词条会被过滤(如:广告,培训,线下)"
                value={excludeWordsInput}
                onChange={(e) => setExcludeWordsInput(e.target.value)}
              />
            </div>
            <div className="space-y-1">
              {candidateTopics.length === 0 && !adoptedTopics.length && (
                <div className="text-center py-8 text-text-secondary text-sm">
                  还没有候选选题,点上方"采集热点"开始
                </div>
              )}
              {topics
                .filter((t) => t.status !== 'discarded')
                .map((t) => (
                  <TopicRow
                    key={t.id}
                    topic={t}
                    onGenerate={() => generateCopy(t)}
                    onDiscard={async () => {
                      await topicsApi.discard(t.id)
                      await load()
                    }}
                    generating={generating}
                  />
                ))}
            </div>
          </Section>

          {/* 产出物:文案 + 脚本 */}
          <Section title="本次产出">
            {accountContents.length === 0 ? (
              <div className="text-center py-8 text-text-secondary text-sm">
                还没有产出,采纳选题后会自动生成文案和脚本
              </div>
            ) : (
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                {accountContents.map((c) => (
                  <ContentCard key={c.id} content={c} onView={() => setViewContent(c)} />
                ))}
              </div>
            )}
          </Section>
        </>
      )}

      {/* 内容详情抽屉(文案全文 + 脚本分镜) */}
      {viewContent && (
        <>
          <div className="fixed inset-0 z-[60] bg-black/60" onClick={() => setViewContent(null)} />
          <div className="fixed right-0 top-0 z-[61] h-screen w-[90%] max-w-[480px] bg-surface border-l border-border overflow-y-auto">
            <div className="flex items-center justify-between p-4 border-b border-border sticky top-0 bg-surface">
              <h2 className="text-base font-semibold">内容详情</h2>
              <button
                className="text-text-tertiary hover:text-text text-xl leading-none"
                onClick={() => setViewContent(null)}
              >
                ✕
              </button>
            </div>
            <div className="p-4 space-y-4">
              <div>
                <div className="text-[11px] text-text-tertiary uppercase tracking-wider mb-1">标题</div>
                <div className="text-sm">{viewContent.title}</div>
              </div>
              <div>
                <div className="text-[11px] text-text-tertiary uppercase tracking-wider mb-1">正文</div>
                <div className="text-sm text-text-secondary whitespace-pre-wrap">{viewContent.body}</div>
              </div>
              <div>
                <div className="text-[11px] text-text-tertiary uppercase tracking-wider mb-1">标签</div>
                <div className="flex flex-wrap gap-1">
                  {viewContent.tags.map((tag) => (
                    <span key={tag} className="text-xs px-2 py-0.5 bg-bg rounded-sm text-info">
                      {tag}
                    </span>
                  ))}
                </div>
              </div>
              <div>
                <div className="text-[11px] text-text-tertiary uppercase tracking-wider mb-1">
                  视频脚本({viewContent.video_script.length} 个分镜)
                </div>
                <div className="space-y-2">
                  {viewContent.video_script.map((s) => (
                    <div key={s.index} className="text-sm border-l-2 border-border-bright pl-3 py-1">
                      <div className="text-xs text-text-tertiary mb-0.5">
                        分镜 {s.index} · {s.duration}s
                      </div>
                      <div className="text-text-secondary">
                        <span className="text-text">口播:</span> {s.narration}
                      </div>
                      <div className="text-text-secondary">
                        <span className="text-text">画面:</span> {s.visual}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </div>
        </>
      )}
    </div>
  )
}

// ---------- 子组件 ----------

function PipelineNode({
  name,
  status,
  detail,
  onRetry,
}: {
  name: string
  status: NodeStatus
  detail: string
  onRetry?: () => void
}) {
  const s = NODE_STYLE[status]
  return (
    <div
      className={`flex flex-col items-center justify-center gap-1.5 px-4 py-3 rounded-md border bg-surface min-w-[120px] ${s.border}`}
    >
      <div className={`w-7 h-7 rounded-full flex items-center justify-center text-sm ${s.iconBg}`}>
        {s.icon}
      </div>
      <div className="text-[13px] font-semibold">{name}</div>
      <div className={`text-xs ${s.label}`}>{detail}</div>
      {status === 'failed' && onRetry && (
        <button
          className="mt-1 px-2 py-1 bg-primary text-white rounded text-xs text-[11px] hover:bg-primary-hover w-full"
          onClick={onRetry}
        >
          重试
        </button>
      )}
    </div>
  )
}

function TopicRow({
  topic,
  onGenerate,
  onDiscard,
  generating,
}: {
  topic: Topic
  onGenerate: () => void
  onDiscard: () => void
  generating: boolean
}) {
  return (
    <div className="flex items-center gap-3 px-3 py-2 bg-surface border border-border rounded-md hover:bg-surface-hover">
      <div className="flex-1 min-w-0">
        <div className="text-sm truncate">{topic.title}</div>
        <div className="text-xs text-text-tertiary mt-0.5">
          {platformLabel(topic.source_platform)} · 热度 {topic.heat_score.toFixed(0)} · 匹配度{' '}
          {(topic.match_score * 100).toFixed(0)}%
        </div>
      </div>
      {topic.status === 'candidate' ? (
        <div className="flex gap-1 shrink-0">
          <button
            className="px-2 py-1 text-xs bg-primary text-white rounded hover:bg-primary-hover disabled:opacity-50"
            onClick={onGenerate}
            disabled={generating}
          >
            {generating ? '生成中…' : '采纳生成'}
          </button>
          <button
            className="px-2 py-1 text-xs text-text-tertiary hover:text-text rounded"
            onClick={onDiscard}
          >
            弃用
          </button>
        </div>
      ) : (
        <Badge variant={topic.status === 'adopted' ? 'success' : 'neutral'}>
          {topic.status === 'adopted' ? '已采纳' : '已弃用'}
        </Badge>
      )}
    </div>
  )
}

function ContentCard({ content, onView }: { content: Content; onView: () => void }) {
  const failed = content.status === 'failed'
  return (
    <div className="p-3 bg-surface border border-border rounded-md">
      <div className="flex items-center justify-between mb-1">
        <Badge variant={failed ? 'danger' : 'success'}>
          {failed ? '失败' : '已出稿'}
        </Badge>
        <span className="text-xs text-text-tertiary">
          {content.video_script.length} 个分镜
        </span>
      </div>
      <div className="text-sm font-medium mb-1 truncate">
        {content.title || '(生成失败)'}
      </div>
      {failed ? (
        <div className="text-xs text-danger mb-2">{content.error_log}</div>
      ) : (
        <div className="text-xs text-text-secondary line-clamp-2 mb-2">
          {content.body}
        </div>
      )}
      <button
        className="text-xs text-primary hover:text-primary-hover"
        onClick={onView}
        disabled={failed}
      >
        {failed ? '查看错误' : '查看全文 →'}
      </button>
    </div>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="mt-8">
      <h2 className="text-sm font-semibold mb-3 text-text-secondary uppercase tracking-wider">
        {title}
      </h2>
      {children}
    </div>
  )
}

function EmptyState({
  title,
  hint,
  ctaLabel,
  ctaTo,
}: {
  title: string
  hint: string
  ctaLabel: string
  ctaTo: string
}) {
  return (
    <div className="text-center py-16">
      <div className="text-base font-semibold mb-2">{title}</div>
      <div className="text-sm text-text-secondary mb-6 max-w-md mx-auto">{hint}</div>
      <a
        href={ctaTo}
        className="inline-block px-4 py-2 bg-primary text-white rounded-md text-[13px] font-semibold hover:bg-primary-hover"
      >
        {ctaLabel}
      </a>
    </div>
  )
}

function platformLabel(p: string): string {
  const m: Record<string, string> = { xhs: '小红书', dy: '抖音', ks: '快手', wx: '视频号' }
  return m[p] ?? p
}
