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

export interface EncodeInfo {
  preset: string | null
  crf: number | null
  vmaf: number | null
  input_size: number | null
  output_size: number | null
  saved_percent: number | null
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
