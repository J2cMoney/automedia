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
  created_at: string
  updated_at: string
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
