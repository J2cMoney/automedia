/**
 * API 封装 - Phase 2 前后端联调配置。
 *
 * - base URL:开发态走 Vite proxy(/api -> 127.0.0.1:8000),生产态从环境变量读
 * - fetch 封装:统一错误处理、JSON 解析、4xx/5xx 抛业务错误
 * - 类型:与后端 pydantic 响应模型对齐
 */

// ---------- 类型(对齐后端 accounts.py 响应模型) ----------

export type Platform = 'xhs' | 'dy' | 'ks' | 'wx'
export type AuthStatus = 'valid' | 'invalid' | 'unknown'
export type AccountStatus = 'active' | 'disabled'

export interface Account {
  id: number
  platform: Platform
  platform_label: string
  nickname: string
  topic_theme: string
  auth_status: AuthStatus
  has_auth: boolean
  status: AccountStatus
  created_at: string
  updated_at: string
}

export interface HealthCheckResult {
  id: number
  auth_status: AuthStatus
  healthy: boolean
  message: string
}

export interface ApiError {
  status: number
  message: string
  detail?: unknown
}

// ---------- 基础 fetch 封装 ----------

const BASE_URL = import.meta.env.VITE_API_BASE ?? ''

export class HttpError extends Error implements ApiError {
  status: number
  detail?: unknown
  constructor(status: number, message: string, detail?: unknown) {
    super(message)
    this.name = 'HttpError'
    this.status = status
    this.detail = detail
  }
}

async function request<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  let resp: Response
  try {
    resp = await fetch(`${BASE_URL}${path}`, {
      headers: {
        'Content-Type': 'application/json',
        ...(options.headers ?? {}),
      },
      ...options,
    })
  } catch (e) {
    throw new HttpError(0, `网络请求失败: ${(e as Error).message}`)
  }

  // 204 No Content(DELETE 成功)
  if (resp.status === 204) {
    return undefined as T
  }

  let body: unknown = null
  const text = await resp.text()
  if (text) {
    try {
      body = JSON.parse(text)
    } catch {
      body = text
    }
  }

  if (!resp.ok) {
    const detail = (body as { detail?: string } | null)?.detail
    const msg = typeof detail === 'string' ? detail : `请求失败 (${resp.status})`
    throw new HttpError(resp.status, msg, body)
  }

  return body as T
}

// ---------- 账号 API ----------

export const accountsApi = {
  list(params?: { platform?: Platform }): Promise<Account[]> {
    const qs = params?.platform ? `?platform=${params.platform}` : ''
    return request<Account[]>(`/api/accounts${qs}`)
  },

  get(id: number): Promise<Account> {
    return request<Account>(`/api/accounts/${id}`)
  },

  create(data: {
    platform: Platform
    nickname: string
    topic_theme: string
  }): Promise<Account> {
    return request<Account>(`/api/accounts`, {
      method: 'POST',
      body: JSON.stringify(data),
    })
  },

  update(
    id: number,
    data: { nickname?: string; topic_theme?: string; status?: AccountStatus },
  ): Promise<Account> {
    return request<Account>(`/api/accounts/${id}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    })
  },

  remove(id: number): Promise<void> {
    return request<void>(`/api/accounts/${id}`, { method: 'DELETE' })
  },

  healthCheck(id: number): Promise<HealthCheckResult> {
    return request<HealthCheckResult>(
      `/api/accounts/${id}/health-check`,
      { method: 'POST' },
    )
  },

  /** 触发浏览器登录(会阻塞直到用户登录完成或超时)。 */
  login(id: number, opts?: { timeout_seconds?: number }): Promise<Account> {
    return request<Account>(`/api/accounts/${id}/login`, {
      method: 'POST',
      body: JSON.stringify(opts ?? {}),
    })
  },
}

// ---------- 选题/热点类型(对齐后端 topics.py) ----------

export type TopicStatus = 'candidate' | 'adopted' | 'discarded'

export interface Topic {
  id: number
  source_platform: Platform
  title: string
  heat_score: number
  source_url: string | null
  matched_account_ids: number[]
  status: TopicStatus
  match_score: number
  created_at: string
}

export interface CrawlResponse {
  task_id: number
  account_id: number
  message: string
}

export interface TaskInfo {
  id: number
  flow_type: string
  status: string
  message_id: string | null
  retry_count: number
  started_at: string | null
  finished_at: string | null
  error_log: string | null
  result: string | null
  created_at: string
}

export const topicsApi = {
  list(params?: { status?: TopicStatus; platform?: Platform }): Promise<Topic[]> {
    const qs = new URLSearchParams()
    if (params?.status) qs.set('status', params.status)
    if (params?.platform) qs.set('platform', params.platform)
    const q = qs.toString()
    return request<Topic[]>(`/api/topics${q ? `?${q}` : ''}`)
  },

  get(id: number): Promise<Topic> {
    return request<Topic>(`/api/topics/${id}`)
  },

  adopt(id: number): Promise<Topic> {
    return request<Topic>(`/api/topics/${id}/adopt`, { method: 'POST' })
  },

  discard(id: number): Promise<Topic> {
    return request<Topic>(`/api/topics/${id}/discard`, { method: 'POST' })
  },

  /** 触发热点爬取(异步任务,返回 task_id)。 */
  crawl(data: {
    account_id: number
    exclude_words?: string[]
    max_results?: number
  }): Promise<CrawlResponse> {
    return request<CrawlResponse>(`/api/topics/crawl`, {
      method: 'POST',
      body: JSON.stringify(data),
    })
  },

  /** 选题采纳后生成文案+脚本,产出 Content。 */
  generate(id: number, data: {
    account_id: number
    scene_count?: number
  }): Promise<{ content_id: number; status: string }> {
    return request(`/api/topics/${id}/generate`, {
      method: 'POST',
      body: JSON.stringify(data),
    })
  },
}

// ---------- 内容类型(对齐后端 contents.py) ----------

export type ContentStatus =
  | 'generating'
  | 'pending_review'
  | 'approved'
  | 'publishing'
  | 'published'
  | 'failed'

export interface VideoScene {
  index: number
  narration: string
  visual: string
  duration: number
}

export interface Content {
  id: number
  account_id: number
  topic_id: number | null
  title: string | null
  body: string | null
  tags: string[]
  video_script: VideoScene[]
  status: ContentStatus
  platform_post_url: string | null
  error_log: string | null
  // Phase 4 视频字段
  video_path: string | null
  script_scenes: { scenes: ScenePlan[] } | null
  clip_decision: { segments: HighlightSegment[]; summary: string } | null
  created_at: string
  updated_at: string
}

/** 场景 B 渲染用分镜计划(对齐后端 agent.ScenePlan.to_dict) */
export interface ScenePlan {
  index: number
  narration: string
  visual: string
  asset_keyword: string
  duration: number
  asset_path: string | null
}

/** 场景 A 剪辑决策切点(对齐后端 agent.HighlightSegment) */
export interface HighlightSegment {
  start: number
  end: number
  reason: string
}

export const contentsApi = {
  list(params?: { account_id?: number; status?: ContentStatus }): Promise<Content[]> {
    const qs = new URLSearchParams()
    if (params?.account_id) qs.set('account_id', String(params.account_id))
    if (params?.status) qs.set('status', params.status)
    const q = qs.toString()
    return request<Content[]>(`/api/contents${q ? `?${q}` : ''}`)
  },

  get(id: number): Promise<Content> {
    return request<Content>(`/api/contents/${id}`)
  },
}

// ---------- 任务状态(对齐后端 main.py TaskStatusResponse) ----------

export const tasksApi = {
  get(id: number): Promise<TaskInfo> {
    return request<TaskInfo>(`/tasks/${id}`)
  },
}

// ---------- Phase 4:视频生成(对齐后端 videos.py) ----------

export interface VideoTaskResponse {
  task_id: number
  content_id: number
  message: string
}

export interface UploadResponse {
  path: string
  filename: string
  size: number
}

export interface VideoStatus {
  content_id: number
  video_path: string | null
  script_scenes: { scenes: ScenePlan[] } | null
  clip_decision: { segments: HighlightSegment[]; summary: string } | null
  status: ContentStatus
}

export const videosApi = {
  /** 上传源长视频(场景 A 输入),返回本地路径。 */
  upload(file: File): Promise<UploadResponse> {
    const form = new FormData()
    form.append('file', file)
    // 注意:multipart 不能预设 Content-Type,浏览器自动设 boundary
    return fetch(`${BASE_URL}/api/videos/upload`, {
      method: 'POST',
      body: form,
    }).then(async (r) => {
      const text = await r.text()
      const body = text ? JSON.parse(text) : null
      if (!r.ok) {
        throw new HttpError(r.status, body?.detail ?? `上传失败 (${r.status})`, body)
      }
      return body as UploadResponse
    })
  },

  /** 提交场景 A 高光提取任务(异步)。 */
  extract(data: {
    content_id: number
    source_video_path: string
    target_duration?: number
  }): Promise<VideoTaskResponse> {
    return request<VideoTaskResponse>(`/api/videos/extract`, {
      method: 'POST',
      body: JSON.stringify(data),
    })
  },

  /** 提交场景 B 从零生成任务(异步)。 */
  generate(data: {
    content_id: number
    whisper_fallback?: boolean
  }): Promise<VideoTaskResponse> {
    return request<VideoTaskResponse>(`/api/videos/generate`, {
      method: 'POST',
      body: JSON.stringify(data),
    })
  },

  /** 查视频生成状态。 */
  status(contentId: number): Promise<VideoStatus> {
    return request<VideoStatus>(`/api/videos/${contentId}`)
  },
}

// ---------- Phase 5:多平台分发(对齐后端 publish.py) ----------

/** 视频号手动发布打包内容(对齐后端 publish.WxPackage)。 */
export interface WxPackage {
  title: string
  body: string
  tags: string[]
  video_path: string
  cover_path: string | null
  copy_text: string
  channels_url: string
}

/** 触发自动发布后返回的异步任务标识(小红书/抖音/快手)。 */
export interface PublishTaskResponse {
  task_id: number
  content_id: number
  message: string
}

export const publishApi = {
  /** 触发自动发布(异步任务,小红书/抖音/快手)。headless 控制是否隐藏浏览器窗口。 */
  trigger(contentId: number, headless = true): Promise<PublishTaskResponse> {
    return request<PublishTaskResponse>(`/api/publish/${contentId}`, {
      method: 'POST',
      body: JSON.stringify({ headless }),
    })
  },

  /** 打包视频号手动发布所需的内容(标题/正文/标签/复制文案/助手链接)。 */
  packageWx(contentId: number): Promise<WxPackage> {
    return request<WxPackage>(`/api/publish/wx/${contentId}/package`, {
      method: 'POST',
    })
  },
}

// ---------- Phase 5:评论回复(对齐后端 comments.py) ----------

/** 评论处理状态(pending 待回复 / replied 已回复 / manual 转人工)。 */
export type CommentStatus = 'pending' | 'replied' | 'manual'

/** 评论记录(对齐后端 comments.Comment 响应模型)。 */
export interface Comment {
  id: number
  content_id: number
  platform_comment_id: string | null
  author: string | null
  text: string
  ai_reply: string | null
  status: CommentStatus
  replied_at: string | null
  error_log: string | null
  created_at: string
}

export const commentsApi = {
  /** 查评论列表,可按内容和状态过滤。 */
  list(params: { contentId?: number; status?: CommentStatus } = {}): Promise<Comment[]> {
    const qs = new URLSearchParams()
    if (params.contentId) qs.set('content_id', String(params.contentId))
    if (params.status) qs.set('status', params.status)
    const q = qs.toString()
    return request<Comment[]>(`/api/comments${q ? `?${q}` : ''}`)
  },

  /** 触发 AI 批量回复评论(异步任务)。maxReplies 可选,限制本次回复条数。 */
  triggerReply(contentId: number, maxReplies?: number): Promise<{
    task_id: number
    content_id: number
    message: string
  }> {
    return request(`/api/comments/${contentId}/reply`, {
      method: 'POST',
      body: JSON.stringify(maxReplies != null ? { max_replies: maxReplies } : {}),
    })
  },
}

// ---------- Phase 6:全链路编排(对齐后端 api/orchestrator.py) ----------

/** 编排批次结果(单账号)。 */
export interface AccountBatchResult {
  account_id: number
  status: 'pending_publish' | 'failed' | string
  step: string
  content_id: number | null
  topic_id: number | null
  error: string | null
}

/** 编排批次状态摘要。 */
export interface BatchSummary {
  total: number
  pending_publish: number
  failed: number
  running: boolean
}

/** 编排批次状态(对应 GET /api/orchestrator/batches/{id})。 */
export interface BatchStatus {
  batch_id: string
  status: 'running' | 'finished' | string
  started_at: string
  finished_at: string | null
  account_ids: number[]
  summary: BatchSummary
  results: Record<string, AccountBatchResult>
}

/** 待发布内容(对应 GET /api/orchestrator/pending)。 */
export interface PendingContent {
  content_id: number
  account_id: number | null
  account_nickname: string | null
  platform: Platform | null
  title: string | null
  video_path: string | null
  has_video: boolean
}

export const orchestratorApi = {
  /** 启动"今日运营"全链路(后台跑,立即返回 batch_id)。到视频成片即停(A-8)。 */
  startDaily(data: {
    account_ids: number[]
    exclude_words?: string[]
    max_topics?: number
    scene_count?: number
    video_whisper_fallback?: boolean
  }): Promise<{ batch_id: string; account_ids: number[]; message: string }> {
    return request('/api/orchestrator/daily', {
      method: 'POST',
      body: JSON.stringify(data),
    })
  },

  /** 查批次状态(各账号进度 + 摘要)。 */
  batchStatus(batchId: string): Promise<BatchStatus> {
    return request<BatchStatus>(`/api/orchestrator/batches/${batchId}`)
  },

  /** 列所有待发布 Content(approved 态)。 */
  pending(): Promise<PendingContent[]> {
    return request<PendingContent[]>('/api/orchestrator/pending')
  },
}

// ---------- Phase 6:统计 + 任务日志 + 配置(对齐后端 api/stats.py) ----------

/** 数据概览聚合(对应 GET /api/stats,NON-7 边界:只回显)。 */
export interface Stats {
  contents_total: number
  published: number
  pending_publish: number
  pending_review: number
  failed: number
  comments_total: number
  replied: number
  reply_pending: number
  replied_rate: number
}

/** 任务记录(对应 GET /api/tasks)。 */
export interface TaskRecord {
  id: number
  flow_type: string
  status: string
  message_id: string | null
  retry_count: number
  account_id: number | null
  content_id: number | null
  started_at: string | null
  finished_at: string | null
  error_log: string | null
  result: string | null
  created_at: string
}

/** 只读配置(对应 GET /api/config,绝不返回密钥)。 */
export interface AppConfig {
  deepseek_model: string
  glm_model: string
  glm_base_url: string
  max_browser_concurrency: number
  max_render_concurrency: number
  publish_interval_minutes: number
  reply_interval_seconds: number
  reply_max_per_poll: number
}

export const statsApi = {
  /** 数据概览聚合。 */
  stats(): Promise<Stats> {
    return request<Stats>('/api/stats')
  },

  /** 任务列表(带筛选,默认最近 50 条)。 */
  tasks(params: {
    limit?: number
    status?: string
    flow_type?: string
  } = {}): Promise<TaskRecord[]> {
    const qs = new URLSearchParams()
    if (params.limit) qs.set('limit', String(params.limit))
    if (params.status) qs.set('status', params.status)
    if (params.flow_type) qs.set('flow_type', params.flow_type)
    const q = qs.toString()
    return request<TaskRecord[]>(`/api/tasks${q ? `?${q}` : ''}`)
  },

  /** 只读配置展示。 */
  config(): Promise<AppConfig> {
    return request<AppConfig>('/api/config')
  },
}
