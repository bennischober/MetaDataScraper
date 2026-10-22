/** Typed client for the resident app's API. */

export type JobState = 'queued' | 'running' | 'done' | 'failed' | 'cancelled'
export type JobKind = 'scan' | 'plan' | 'encode' | 'dedupe'

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
  /** schema v7 drift fields (null for pre-v7 rows); present in the
   * library list, snapshot and /api/movie alike since 3f77d08 */
  generation?: number | null
  input_bitrate?: number | null
  output_bitrate?: number | null
}

/** One historical encode of a movie (GET /api/movie history entry).
 * The schema-v7 fields (generation, bitrates, chain links) make the row
 * a provenance-chain entry; bitrates are None for pre-v7 rows. */
export interface EncodeRecord extends EncodeInfo {
  /** the encodes row's primary key — the only safe each-key (timestamps
   * collide when two runs finish in the same second) */
  id?: number
  encoded_at: string | null
  input_bitrate?: number | null
  output_bitrate?: number | null
  generation?: number
  source_file?: string | null
  output_file?: string | null
  /** the library file this row belongs to — chain rows may belong to the
   * parent encode's file, not the viewed one (schema v7 chain) */
  file_path?: string | null
  file_name?: string | null
}

export interface MovieDetail {
  file: Omit<Movie, 'encode'> & { generation?: number }
  history: EncodeRecord[]
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
  /** the resident app's own memory (MiB), self-measured every 5 min —
   * staying lean is a measured fact (4534e07); null when unmeasurable */
  rss_mib?: number | null
}

export interface Config {
  codec?: { hdr?: { color_space?: string[]; color_transfer?: string[] } }
  file?: { extensions?: string[]; output?: { seperator?: string } }
  ffmpeg?: { black_bars?: { MIN_WIDTH?: number; MIN_HEIGHT?: number } }
  handbrake?: { preset?: string; encoder?: string; max_threads?: number }
  resources?: { idle_priority?: boolean }
  integrations?: {
    jellyfin?: { url?: string; token_env?: string }
  }
  language?: string
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  if (!response.ok) {
    const detail = await response.text().catch(() => response.statusText)
    throw new Error(humanizeError(response.status, detail))
  }
  return response.json() as Promise<T>
}

/** Raw error text → operator language. The audit trail keeps the raw
 * exception (diagnosis needs it); the surfaces that INTERRUPT (the red
 * strip, the preflight) translate known cases into what happened and what
 * to do. Unknown text passes through untouched — never invented. */
const ERROR_TRANSLATIONS: [RegExp, (m: RegExpMatchArray) => string][] = [
  [
    /API (\d+): [\s\S]*?"detail"\s*:\s*"((?:[^"\\]|\\.)*)"/,
    (m) => `${m[1]} — ${m[2].replace(/\\(.)/g, '$1')}`,
  ],
  [/API 503:/, () => 'the app is still starting — try again in a moment'],
  [
    /'(\w+)' object has no attribute '(\w+)'/,
    (m) => `internal data mismatch (${m[1]} field '${m[2]}' missing) — likely a pre-v7 database row; rescan the title`,
  ],
  [
    /\[Errno 22\] Invalid argument/,
    () => 'a file could not be read (invalid path or unreadable source) — check the title in FOLDER',
  ],
  [
    /\[Errno 2\] No such file or directory: '([^']+)'/,
    (m) => `file not found: ${m[1].split(/[\\/]/).pop()} — rescan the library`,
  ],
  [
    /\[WinError 32\] The process cannot access the file/,
    () => 'the file is locked by another program — close it and retry',
  ],
  [
    /HTTPConnectionPool|ReadTimeout|ConnectionError/,
    () => 'an external tool did not answer in time — retry; if it persists, check the tool install',
  ],
  [
    /interrupted by app restart/,
    () => 'the run was interrupted by an app restart — RETRY resumes it',
  ],
]

export function humanizeError(status: number | null, raw: string): string {
  const text = raw.trim()
  for (const [pattern, say] of ERROR_TRANSLATIONS) {
    const m = text.match(pattern)
    if (m) return say(m)
  }
  if (status != null) return `API ${status}: ${text}`
  return text
}

/** The library ETag: kept across polls, sent as If-None-Match; a 304
 * keeps the previous array (the poll becomes nearly free). */
let libraryEtag: string | null = null

export interface Snapshot {
  status: Status
  jobs: Job[]
  /** null when the library is unchanged (client keeps its copy). */
  library: Movie[] | null
  library_etag: string
}

export const api = {
  /** One round-trip for the whole poll: status + jobs + library (ETag-aware). */
  snapshot: async (): Promise<Snapshot> => {
    const response = await fetch('/api/snapshot', {
      headers: libraryEtag ? { 'If-None-Match': libraryEtag } : {},
    })
    if (!response.ok) throw new Error(`API ${response.status}: snapshot`)
    const snap = (await response.json()) as Snapshot
    libraryEtag = snap.library_etag
    return snap
  },
  status: () => request<Status>('/api/status'),
  /** Fetches the library with ETag caching: returns null on 304 (unchanged). */
  libraryChanged: async (): Promise<Movie[] | null> => {
    const response = await fetch('/api/library', {
      headers: libraryEtag ? { 'If-None-Match': libraryEtag } : {},
    })
    if (response.status === 304) return null
    if (!response.ok) throw new Error(`API ${response.status}: library`)
    libraryEtag = response.headers.get('ETag')
    return (await response.json()) as Movie[]
  },
  library: () => request<Movie[]>('/api/library'),
  jobs: (limit = 50) => request<Job[]>(`/api/jobs?limit=${limit}`),
  job: (id: number) => request<Job>(`/api/jobs/${id}`),
  /** One movie's full record: file facts + the COMPLETE encode history. */
  movie: (path: string) => {
    const params = new URLSearchParams({ path })
    return request<MovieDetail>(`/api/movie?${params}`)
  },
  config: () => request<Config>('/api/config'),
  saveConfig: (config: Config) =>
    request<Config>('/api/config', { method: 'PUT', body: JSON.stringify(config) }),
  enqueue: (body: {
    kind: JobKind
    path?: string
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
  reveal: (path: string) =>
    request<{ revealed: string }>('/api/reveal', { method: 'POST', body: JSON.stringify({ path }) }),
  /** Moves a duplicate to the OS recycle bin (recoverable, backend-confirmed). */
  deleteToTrash: (path: string) =>
    request<{ deleted: string; to: string }>('/api/duplicates/delete', {
      method: 'POST',
      body: JSON.stringify({ path }),
    }),
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

/** Formats an ISO timestamp as a short "when" (time today, date+time before).
 * The backend stores UTC ('datetime now' in sqlite is UTC); naive strings
 * get Z appended here at the API boundary — the one place that decides. */
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
