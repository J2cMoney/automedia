import { useEffect, useState, useCallback } from 'react'
import {
  statsApi,
  type TaskRecord,
  type AppConfig,
} from '@/lib/api'
import Badge from '@/components/Badge'

/**
 * 日志与设置 - Design-Brief SCREEN-6。
 *
 * 布局:
 *   1. AI/风控配置展示(只读:DeepSeek/GLM 模型 + 并发/限速参数,后端 GET /api/config)
 *   2. 任务日志表格(查 task_runs,带 flow_type/status 筛选)
 *
 * 五态:加载 / 空(无任务)/ 错误 / 成功(日志+配置)/ 无权限(后端未连接)
 */

const FLOW_LABEL: Record<string, string> = {
  hotspot: '热点采集',
  copy: '文案生成',
  video: '视频成片',
  publish: '发布',
  reply: '回评',
  test: '测试',
  video_extract: '高光提取',
  video_generate: '视频生成',
}

function taskStatusBadge(status: string) {
  if (status === 'finished') return <Badge variant="success">完成</Badge>
  if (status === 'running') return <Badge variant="info" pulse>运行中</Badge>
  if (status === 'failed') return <Badge variant="danger">失败</Badge>
  if (status === 'pending') return <Badge variant="neutral">等待</Badge>
  if (status === 'cancelled') return <Badge variant="warning">已取消</Badge>
  return <Badge variant="neutral">{status}</Badge>
}

export default function Settings() {
  const [tasks, setTasks] = useState<TaskRecord[]>([])
  const [config, setConfig] = useState<AppConfig | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [statusFilter, setStatusFilter] = useState<string>('')
  const [flowFilter, setFlowFilter] = useState<string>('')

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const params: { status?: string; flow_type?: string; limit?: number } = { limit: 100 }
      if (statusFilter) params.status = statusFilter
      if (flowFilter) params.flow_type = flowFilter
      const [ts, cfg] = await Promise.all([
        statsApi.tasks(params),
        statsApi.config().catch(() => null),
      ])
      setTasks(ts)
      setConfig(cfg)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setLoading(false)
    }
  }, [statusFilter, flowFilter])

  useEffect(() => {
    load()
  }, [load])

  if (loading) {
    return (
      <div className="p-6 max-w-[1400px]">
        <h1 className="text-lg font-semibold mb-6">日志与设置</h1>
        <div className="text-center py-16 text-text-secondary text-sm">加载中…</div>
      </div>
    )
  }

  const isBackendDown = error !== null && /fetch|network|ECONN|网络请求失败/i.test(error)

  return (
    <div className="p-6 max-w-[1400px]">
      {/* 顶部 */}
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-lg font-semibold">日志与设置</h1>
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

      {!isBackendDown && (
        <>
          {/* 配置展示(只读) */}
          {config && (
            <div className="mb-6">
              <h2 className="text-sm font-semibold mb-3 text-text-secondary uppercase tracking-wider">
                AI 与风控配置
              </h2>
              <div className="bg-surface border border-border rounded-md p-4">
                <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-[13px]">
                  <ConfigItem label="文本模型(DeepSeek)" value={config.deepseek_model} />
                  <ConfigItem label="视觉模型(GLM)" value={config.glm_model} />
                  <ConfigItem
                    label="浏览器并发上限"
                    value={`${config.max_browser_concurrency}`}
                  />
                  <ConfigItem
                    label="渲染并发上限"
                    value={`${config.max_render_concurrency}`}
                  />
                  <ConfigItem
                    label="发布间隔(分钟)"
                    value={`${config.publish_interval_minutes}`}
                  />
                  <ConfigItem
                    label="回评间隔(秒)"
                    value={`${config.reply_interval_seconds}`}
                  />
                  <ConfigItem
                    label="单次回评上限"
                    value={`${config.reply_max_per_poll}`}
                  />
                  <ConfigItem label="GLM 接口" value={config.glm_base_url} mono />
                </div>
                <div className="text-xs text-text-tertiary mt-3">
                  配置只读展示,密钥不在面板暴露。修改请编辑后端 .env 后重启服务。
                </div>
              </div>
            </div>
          )}

          {/* 任务日志 */}
          <h2 className="text-sm font-semibold mb-3 text-text-secondary uppercase tracking-wider">
            任务日志
          </h2>

          {/* 筛选 */}
          <div className="flex items-center gap-2 mb-3">
            <select
              className="px-3 py-1.5 bg-bg border border-border-bright rounded-md text-text text-[13px] focus:outline-none focus:border-primary"
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
            >
              <option value="">全部状态</option>
              <option value="pending">等待</option>
              <option value="running">运行中</option>
              <option value="finished">完成</option>
              <option value="failed">失败</option>
            </select>
            <select
              className="px-3 py-1.5 bg-bg border border-border-bright rounded-md text-text text-[13px] focus:outline-none focus:border-primary"
              value={flowFilter}
              onChange={(e) => setFlowFilter(e.target.value)}
            >
              <option value="">全部类型</option>
              {Object.entries(FLOW_LABEL).map(([k, v]) => (
                <option key={k} value={k}>{v}</option>
              ))}
            </select>
            <span className="text-xs text-text-tertiary ml-auto">{tasks.length} 条记录</span>
          </div>

          {/* 空态 */}
          {tasks.length === 0 ? (
            <div className="bg-surface border border-border rounded-md px-4 py-12 text-center text-text-secondary text-sm">
              {statusFilter || flowFilter
                ? '没有符合条件的任务记录'
                : '还没有任务记录,跑一次「开始今日运营」后这里会显示'}
            </div>
          ) : (
            <div className="bg-surface border border-border rounded-md overflow-hidden">
              <table className="w-full border-collapse text-[13px]">
                <thead>
                  <tr>
                    {['ID', '类型', '状态', '账号', '内容', '开始时间', '耗时', '错误'].map((h) => (
                      <th
                        key={h}
                        className="text-left px-3 py-2 text-[11px] font-semibold uppercase tracking-wider text-text-tertiary border-b border-border bg-bg"
                      >
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {tasks.map((t) => {
                    const duration = _calcDuration(t.started_at, t.finished_at)
                    return (
                      <tr key={t.id} className="hover:bg-surface-hover">
                        <td className="px-3 py-2 border-b border-border text-text-tertiary nums">
                          {t.id}
                        </td>
                        <td className="px-3 py-2 border-b border-border">
                          {FLOW_LABEL[t.flow_type] ?? t.flow_type}
                        </td>
                        <td className="px-3 py-2 border-b border-border">
                          {taskStatusBadge(t.status)}
                        </td>
                        <td className="px-3 py-2 border-b border-border text-text-secondary nums">
                          {t.account_id ?? '—'}
                        </td>
                        <td className="px-3 py-2 border-b border-border text-text-secondary nums">
                          {t.content_id ?? '—'}
                        </td>
                        <td className="px-3 py-2 border-b border-border text-text-tertiary text-xs nums">
                          {t.started_at
                            ? new Date(t.started_at).toLocaleString('zh-CN', {
                                month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
                              })
                            : '—'}
                        </td>
                        <td className="px-3 py-2 border-b border-border text-text-tertiary text-xs nums">
                          {duration}
                        </td>
                        <td className="px-3 py-2 border-b border-border text-danger text-xs max-w-[240px]">
                          {t.error_log ? (
                            <span className="truncate inline-block w-full" title={t.error_log}>
                              {t.error_log}
                            </span>
                          ) : (
                            <span className="text-text-tertiary">—</span>
                          )}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </div>
  )
}

function ConfigItem({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div>
      <div className="text-[11px] text-text-tertiary uppercase tracking-wider mb-1">{label}</div>
      <div className={mono ? 'font-mono text-xs text-text-secondary truncate' : 'text-text'}>
        {value}
      </div>
    </div>
  )
}

function _calcDuration(started: string | null, finished: string | null): string {
  if (!started || !finished) return '—'
  const ms = new Date(finished).getTime() - new Date(started).getTime()
  if (ms < 0) return '—'
  if (ms < 1000) return `${ms}ms`
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`
  return `${(ms / 60000).toFixed(1)}min`
}
