<script lang="ts">
  import { fmtWhen, fmtSpan, type Job } from '../api'

  /** The run log as an event log: every job with its timestamps, wall
   * duration, and expandable payload/result — the tool's audit trail. */

  let {
    history,
    oncancel,
    onretry,
  }: {
    history: Job[]
    oncancel: (id: number | null) => void
    onretry: (job: Job) => void
  } = $props()

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

  let now = $state(Date.now())
  $effect(() => {
    if (!history.some((j) => j.state === 'running')) return
    const timer = setInterval(() => (now = Date.now()), 1000)
    return () => clearInterval(timer)
  })

  function liveElapsed(job: Job): string | null {
    if (job.state !== 'running' || !job.started_at) return null
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

  // expandable detail (payload + result JSON)
  let expanded = $state<Set<number>>(new Set())
  function toggleExpand(id: number | null) {
    if (id === null) return
    const next = new Set(expanded)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    expanded = next
  }
  const expandedJobs = $derived(history.filter((j) => j.id !== null && expanded.has(j.id)))
</script>

<div class="page page-in">
  <header class="page-head">
    <h2>Jobs</h2>
    <p class="legend">{history.length} ENTRIES · THE FULL AUDIT TRAIL</p>
  </header>

  {#if history.length === 0}
    <div class="empty dim">no jobs yet — enqueue a scan to open the log</div>
  {:else}
    <div class="log">
      <div class="log-head legend">
        <span>#</span>
        <span>KIND</span>
        <span>STATE</span>
        <span>STARTED</span>
        <span>DURATION</span>
        <span></span>
      </div>
      {#each history as job (job.id)}
        <div class="entry" class:running={job.state === 'running'} class:failed={job.state === 'failed'} class:open={job.id !== null && expanded.has(job.id)}>
          <div class="row mono">
            <span class="id">{job.id}</span>
            <span class="kind">{job.kind}</span>
            <span class="state {stateClass(job.state)}">
              {job.state.toUpperCase()}{#if liveElapsed(job)} · {liveElapsed(job)}{/if}
            </span>
            <span class="when" title={job.started_at ?? ''}>{fmtWhen(job.started_at ?? job.created_at)}</span>
            <span class="dur">{job.state === 'done' ? fmtSpan(job.started_at, job.finished_at) ?? '—' : job.state === 'running' ? '…' : '—'}</span>
            <span class="ops">
              {#if job.state === 'running' || job.state === 'queued'}
                <button class="mini" onclick={() => oncancel(job.id)}>CANCEL</button>
              {:else if job.state === 'failed'}
                <button class="mini" onclick={() => onretry(job)}>RETRY</button>
              {/if}
              <button class="mini" onclick={() => toggleExpand(job.id)} aria-expanded={job.id !== null && expanded.has(job.id)}>
                {job.id !== null && expanded.has(job.id) ? 'HIDE' : 'DETAIL'}
              </button>
            </span>
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

          {#if job.id !== null && expanded.has(job.id)}
            <div class="detail">
              <div class="d-block">
                <span class="legend">PAYLOAD</span>
                <pre class="mono">{JSON.stringify(job.payload, null, 2)}</pre>
              </div>
              {#if job.result}
                <div class="d-block">
                  <span class="legend">RESULT</span>
                  <pre class="mono">{JSON.stringify(job.result, null, 2)}</pre>
                </div>
              {/if}
              <div class="d-block">
                <span class="legend">TIMESTAMPS</span>
                <pre class="mono">created {fmtWhen(job.created_at)} · started {fmtWhen(job.started_at)} · finished {fmtWhen(job.finished_at)}</pre>
              </div>
            </div>
          {/if}
        </div>
      {/each}
    </div>
  {/if}
</div>

<style>
  .page { display: flex; flex-direction: column; gap: 12px; }
  .page-head h2 { font-size: var(--fs-page); font-weight: 700; }
  .page-head .legend { margin-top: 2px; }
  .dim { color: var(--ink-dim); }
  .ok { color: var(--teal-ink); }
  .bad { color: var(--red); }
  .run { color: var(--amber); }
  .empty {
    padding: 60px 20px;
    text-align: center;
    border: 1px dashed var(--seam);
    border-radius: var(--radius);
  }
  .log {
    border: 1px solid var(--seam);
    border-radius: var(--radius);
    background: var(--panel);
    overflow-x: auto;
  }
  .log-head {
    display: grid;
    grid-template-columns: 44px 130px 150px 90px 80px 1fr;
    gap: 10px;
    padding: 8px 14px;
    border-bottom: 1px solid var(--seam);
  }
  .entry { padding: 0 14px; border-bottom: 1px solid var(--row-line); }
  .entry:last-child { border-bottom: none; }
  .entry.running { background: repeating-linear-gradient(45deg, rgba(224, 164, 94, 0.08) 0 4px, transparent 4px 8px), var(--panel); }
  .entry.failed { background: repeating-linear-gradient(45deg, rgba(242, 114, 101, 0.07) 0 4px, transparent 4px 8px), var(--panel); }
  .entry.open { background: var(--panel-raised); }
  .row {
    display: grid;
    grid-template-columns: 44px 130px 150px 90px 80px 1fr;
    gap: 10px;
    align-items: baseline;
    padding: 7px 0;
  }
  .id { color: var(--ink-faint); }
  .kind { font-weight: 600; }
  .when { color: var(--ink-dim); }
  .dur { color: var(--ink-dim); }
  .ops { display: flex; gap: 6px; justify-content: flex-end; }
  .mini {
    background: transparent;
    border: 1px solid var(--seam);
    border-radius: 3px;
    color: var(--ink-dim);
    padding: 3px 10px;
    cursor: pointer;
    font-size: 10.5px;
    letter-spacing: 1px;
    min-height: 24px;
  }
  .mini:hover { color: var(--ink); border-color: var(--ink-dim); }
  .trace {
    position: relative;
    height: 18px;
    margin: 2px 0 8px;
    background: var(--hatch-work);
    border: 1px solid var(--seam);
    border-radius: 3px;
    overflow: hidden;
  }
  .fill {
    position: absolute;
    left: 0; top: 0; bottom: 0;
    background: var(--hatch-progress);
    border-right: 2px solid var(--amber);
  }
  .reading {
    position: absolute;
    right: 6px; top: 2px;
    color: var(--ink);
    max-width: 85%;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .note { padding: 2px 0 8px; }
  .error { padding: 2px 0 8px; color: var(--red); white-space: normal; word-break: break-word; }
  .detail {
    padding: 8px 0 14px;
    display: flex;
    flex-direction: column;
    gap: 10px;
    border-top: 1px solid var(--row-line);
  }
  .d-block .legend { display: block; margin: 8px 0 4px; }
  .d-block pre {
    margin: 0;
    padding: 8px 10px;
    background: var(--ground);
    border: 1px solid var(--row-line);
    border-radius: 3px;
    font-size: 10.5px;
    line-height: 1.5;
    overflow: auto;
    max-height: 200px;
    white-space: pre-wrap;
    word-break: break-word;
  }
</style>
