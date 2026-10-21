/** Typed client for the resident app's API. */

export type JobState = 'queued' | 'running' | 'done' | 'failed' | 'cancelled'
export type JobKind = 'scan' | 'plan' | 'encode'

export interface Job {
  id: number | null
  kind: JobKind
  payload: Record<string, unknown>
  state: JobState
  result: Record<string, unknown> | null
  error: string | null
  created_at?: string | null
  started_at?: string | null
  finished_at?: string | null
}

/** Extract the movie folders a job operated on (multi-directory aware). */
export function jobPaths(job: Job): string[] {
  const payload = job.payload as { paths?: string[]; path?: string }
  if (payload.paths?.length) return payload.paths
  return payload.path ? [payload.path] : []
}

export interface EncodeInfo {
  preset: string | null
  crf: number | null
  vmaf: number | null
  input_size: number | null
  output_size: number | null
  saved_percent: number | null
  /** schema v5 benchmark metrics */
  encoder_preset?: string | null
  encode_seconds?: number | null
  probe_count?: number | null
  probe_seconds?: number | null
  tool_versions?: string | null
}

export interface Movie {
  path: string
  name: string
  category: string
  duration: string | null
  codec: string | null
  resolution: [number, number] | null
  size: number | null
  hdr: boolean
  missing?: boolean
  missing_since?: string | null
  encode: EncodeInfo | null
}

export interface Status {
  queue: Job[]
  watcher: 'running' | 'stopped'
  database: string
}

export interface Config {
  codec?: { hdr?: { color_space?: string[]; color_transfer?: string[] } }
  file?: { extensions?: string[]; output?: { seperator?: string } }
  ffmpeg?: { black_bars?: { MIN_WIDTH?: number; MIN_HEIGHT?: number } }
  handbrake?: { preset?: string; encoder?: string }
  language?: string
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  if (!response.ok) {
    const detail = await response.text().catch(() => response.statusText)
    throw new Error(`API ${response.status}: ${detail}`)
  }
  return response.json() as Promise<T>
}

export const api = {
  status: () => request<Status>('/api/status'),
  library: () => request<Movie[]>('/api/library'),
  jobs: (limit = 50) => request<Job[]>(`/api/jobs?limit=${limit}`),
  job: (id: number) => request<Job>(`/api/jobs/${id}`),
  config: () => request<Config>('/api/config'),
  saveConfig: (config: Config) =>
    request<Config>('/api/config', { method: 'PUT', body: JSON.stringify(config) }),
  enqueue: (body: {
    kind: JobKind
    path: string
    paths?: string[]
    output_dir?: string
    quality?: boolean
    target_vmaf?: number
    remove_bars?: boolean
    overwrite?: boolean
    workers?: number | null
    rescan?: boolean
    hash?: boolean
  }) => request<Job>('/api/jobs', { method: 'POST', body: JSON.stringify(body) }),
  cancel: (id: number) =>
    request<{ cancelled: number }>(`/api/jobs/${id}/cancel`, { method: 'POST' }),
  watch: (action: 'start' | 'stop', inbox?: string, output_dir?: string) =>
    request<{ watcher: string }>(`/api/watch/${action}`, {
      method: 'POST',
      body: JSON.stringify({ inbox, output_dir }),
    }),
}

/** Formats a byte count as a GB string ('—' for empty). */
export function fmtGb(bytes: number | null | undefined): string {
  if (!bytes || bytes <= 0) return '—'
  return `${(bytes / 1024 ** 3).toFixed(2)} GB`
}

/** Formats a saved percentage ('—' for none, +X% for growth). */
export function fmtSaved(percent: number | null | undefined): string {
  if (percent === null || percent === undefined) return '—'
  return percent <= 0 ? `+${Math.abs(percent).toFixed(1)}%` : `${percent.toFixed(1)}%`
}

/** Formats an ISO timestamp as a short "when" (time today, date+time before). */
export function fmtWhen(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso.endsWith('Z') || iso.includes('+') ? iso : iso + 'Z')
  if (Number.isNaN(d.getTime())) return '—'
  const time = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  if (d.toDateString() === new Date().toDateString()) return time
  return `${d.toLocaleDateString([], { day: '2-digit', month: 'short' })} ${time}`
}

/** Formats the span between two ISO timestamps as a human duration. */
export function fmtSpan(startIso: string | null | undefined, endIso: string | null | undefined): string | null {
  if (!startIso || !endIso) return null
  const s = Date.parse(startIso.endsWith('Z') || startIso.includes('+') ? startIso : startIso + 'Z')
  const e = Date.parse(endIso.endsWith('Z') || endIso.includes('+') ? endIso : endIso + 'Z')
  if (Number.isNaN(s) || Number.isNaN(e)) return null
  const seconds = Math.max(0, Math.round((e - s) / 1000))
  if (seconds < 60) return `${seconds}s`
  const m = Math.floor(seconds / 60)
  if (m < 60) return `${m}m ${seconds % 60}s`
  return `${Math.floor(m / 60)}h ${m % 60}m`
}

/** Formats a duration in seconds as a human string ('—' for none). */
export function fmtSeconds(seconds: number | null | undefined): string {
  if (seconds == null || seconds <= 0) return '—'
  if (seconds < 60) return `${seconds.toFixed(0)}s`
  const m = Math.floor(seconds / 60)
  const s = Math.round(seconds % 60)
  if (m < 60) return `${m}m ${s}s`
  const h = Math.floor(m / 60)
  return `${h}h ${m % 60}m`
}

/** Formats a saved-percentage-like value honestly: growth is not a gain. */
export function fmtSavedHonest(percent: number | null | undefined): {
  text: string
  grew: boolean
} {
  if (percent === null || percent === undefined) return { text: '—', grew: false }
  if (percent <= 0) return { text: `+${Math.abs(percent).toFixed(1)}% larger`, grew: true }
  return { text: `${percent.toFixed(1)}%`, grew: false }
}
