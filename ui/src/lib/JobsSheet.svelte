<script lang="ts">
  import type { Job } from './api'

  /** SHEET 03 — JOBS: the run log. Every enqueue is an entry; running work
   * shows its live trace (stage, percent, elapsed); failures carry retry. */

  let { history, oncancel, onretry }: {
    history: Job[]
    oncancel: (id: number | null) => void
    onretry: (job: Job) => void
  } = $props()

  const kindLabel: Record<string, string> = {
    scan: 'scan',
    plan: 'plan',
    encode: 'encode · quality mode',
  }

  interface ProgressData {
    stage?: string
    detail?: string
    done?: number
    total?: number
    movie?: string
    percent?: number
  }
  const progress = (job: Job) =>
    (job.result as { progress?: ProgressData } | null)?.progress ?? null

  // live elapsed, ticking while running
  let now = $state(Date.now())
  $effect(() => {
    const anyRunning = history.some((j) => j.state === 'running')
    if (!anyRunning) return
    const timer = setInterval(() => (now = Date.now()), 1000)
    return () => clearInterval(timer)
  })

  function elapsed(job: Job): string | null {
    if (job.state !== 'running') return null
    if (!job.started_at) return null
    const iso = job.started_at.endsWith('Z') || job.started_at.includes('+')
      ? job.started_at
      : job.started_at + 'Z'
    const seconds = Math.max(0, Math.floor((now - Date.parse(iso)) / 1000))
    const h = Math.floor(seconds / 3600)
    const m = Math.floor((seconds % 3600) / 60)
    const s = seconds % 60
    return h > 0 ? `${h}h${String(m).padStart(2, '0')}m` : `${m}m${String(s).padStart(2, '0')}s`
  }

  const queuedNote = (job: Job) => {
    if (job.state !== 'queued') return ''
    const p = job.payload as Record<string, unknown>
    const parts: string[] = []
    if (p.quality) parts.push(`quality · VMAF>${p.target_vmaf ?? 95}`)
    else parts.push('fixed preset')
    if (p.remove_bars) parts.push('bars removed')
    return parts.join(' · ')
  }

  function stateClass(state: string): string {
    return state === 'done' ? 'ok' : state === 'failed' ? 'bad' : state === 'running' ? 'run' : 'dim'
  }
</script>

<div class="block">
  <div class="head">
    <span class="legend">SHEET 03 — RUN LOG</span>
    <span class="mono dim">{history.length} entries</span>
  </div>

  {#if history.length === 0}
    <div class="empty dim">no jobs yet — enqueue a scan to open the ledger</div>
  {:else}
    <div class="table-wrap">
      {#each history as job (job.id)}
        <div class="entry" class:running={job.state === 'running'} class:failed={job.state === 'failed'}>
          <div class="entry-head">
            <span class="mono id">#{job.id}</span>
            <span class="name">{kindLabel[job.kind] ?? job.kind}</span>
            <span class="mono state {stateClass(job.state)}">
              {job.state.toUpperCase()}{#if elapsed(job)} · {elapsed(job)}{/if}
            </span>
            {#if job.state === 'running' || job.state === 'queued'}
              <button class="act quiet" onclick={() => oncancel(job.id)}>CANCEL</button>
            {:else if job.state === 'failed'}
              <button class="act quiet" onclick={() => onretry(job)}>RETRY</button>
            {/if}
          </div>

          {#if job.state === 'running'}
            {@const p = progress(job)}
            <div class="trace" role="progressbar" aria-valuenow={p?.percent ?? 0} aria-valuemin="0" aria-valuemax="100" aria-label="{job.kind} progress">
              <div class="fill" style="width: {p?.percent ?? 2}%"></div>
              <span class="mono reading">
                {p?.movie ? `${p.movie} · ` : ''}{p?.detail ?? 'starting…'}{p?.percent != null ? ` · ${p.percent}%` : ''}
              </span>
            </div>
          {:else if job.state === 'queued' && queuedNote(job)}
            <div class="mono dim note">{queuedNote(job)}</div>
          {:else if job.error}
            <div class="mono error">{job.error}</div>
          {/if}
        </div>
      {/each}
    </div>
  {/if}
</div>

<style>
  .block {
    display: flex;
    flex-direction: column;
    gap: 10px;
    flex: 1;
    min-height: 0;
  }
  .head { display: flex; justify-content: space-between; align-items: center; }
  .table-wrap {
    flex: 1;
    overflow: auto;
    border: 1px solid var(--ink);
    background: var(--panel);
    display: flex;
    flex-direction: column;
  }
  .empty { padding: 40px; text-align: center; }
  .entry {
    padding: 10px 16px;
    border-bottom: 1px solid var(--row-line);
  }
  .entry:last-child { border-bottom: none; }
  .entry.running {
    background: repeating-linear-gradient(45deg, rgba(180, 83, 9, 0.08) 0 4px, transparent 4px 8px), var(--panel);
  }
  .entry.failed { background: repeating-linear-gradient(45deg, rgba(179, 56, 44, 0.07) 0 4px, transparent 4px 8px), var(--panel); }
  .entry-head {
    display: flex;
    align-items: baseline;
    gap: 12px;
  }
  .id { color: var(--ink-faint); }
  .name { font-weight: 600; flex: 1; }
  .state.ok { color: var(--teal-ink); }
  .state.bad { color: var(--red); }
  .state.run { color: var(--amber); }
  .dim { color: var(--ink-dim); }
  .act {
    background: transparent;
    border: 1px solid var(--seam);
    color: var(--ink-dim);
    padding: 2px 10px;
    cursor: pointer;
    font-size: 10px;
    letter-spacing: var(--track-legend);
  }
  .act:hover { color: var(--ink); border-color: var(--ink-dim); }
  .act.quiet:hover { color: var(--red); border-color: var(--red); }
  .trace {
    position: relative;
    height: 22px;
    margin-top: 8px;
    background: var(--hatch-work);
    border: 1px solid var(--seam);
  }
  .fill {
    position: absolute;
    left: 0; top: 0; bottom: 0;
    background: var(--hatch-progress);
    border-right: 2px solid var(--amber);
  }
  .reading {
    position: absolute;
    right: 6px; top: 3px;
    color: var(--ink);
    max-width: 85%;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .note { margin-top: 6px; }
  .error {
    margin-top: 6px;
    color: var(--red);
    white-space: normal;
    word-break: break-word;
  }
</style>
