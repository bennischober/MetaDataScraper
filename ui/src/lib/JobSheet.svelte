<script lang="ts">
  import type { Job } from './api'

  let { job, oncancel }: { job: Job; oncancel?: (id: number) => void } = $props()

  const kindLabel: Record<string, string> = {
    scan: 'scan',
    plan: 'plan',
    encode: 'encode · quality mode',
  }

  const movieName = $derived(
    (job.payload?.path as string | undefined)?.split(/[\\/]/).pop() ?? ''
  )

  // live progress from the backend (stage, detail, done/total, percent)
  interface ProgressData {
    stage?: string
    detail?: string
    done?: number
    total?: number
    movie?: string
    percent?: number
  }
  const progress = $derived(
    (job.result as { progress?: ProgressData } | null)?.progress ?? null
  )

  // density on the zone ramp per stage: solid = measured work done,
  // hatched = work in progress, empty = work ahead
  const stageFraction = $derived.by(() => {
    if (job.state === 'done') return 1.0
    if (progress?.percent !== undefined && progress.percent > 0) {
      return Math.min(1, progress.percent / 100)
    }
    if (progress?.done !== undefined && progress.total) {
      return progress.done / progress.total
    }
    return 0
  })

  // live elapsed time, ticking while running
  let now = $state(Date.now())
  $effect(() => {
    if (job.state !== 'running') return
    const timer = setInterval(() => (now = Date.now()), 1000)
    return () => clearInterval(timer)
  })
  const elapsed = $derived.by(() => {
    if (job.state === 'done' || job.state === 'failed') return null
    if (!job.started_at) return null
    const seconds = Math.max(0, Math.floor((now - Date.parse(job.started_at + 'Z')) / 1000))
    const h = Math.floor(seconds / 3600)
    const m = Math.floor((seconds % 3600) / 60)
    const s = seconds % 60
    return h > 0 ? `${h}h${String(m).padStart(2, '0')}m` : `${m}m${String(s).padStart(2, '0')}s`
  })

  // queued jobs show their parameters (recognition over recall)
  const queuedNote = $derived.by(() => {
    if (job.state !== 'queued') return ''
    const p = job.payload as Record<string, unknown>
    const parts: string[] = []
    if (p.quality) parts.push(`quality · VMAF>${p.target_vmaf ?? 95}`)
    else parts.push('fixed preset')
    if (p.remove_bars) parts.push('bars removed')
    return parts.join(' · ')
  })
</script>

<div class="sheet" class:active={job.state === 'running'} class:failed={job.state === 'failed'}>
  <div class="head">
    <span class="title">
      {movieName || job.kind}
      <span class="dim note">— {kindLabel[job.kind] ?? job.kind}</span>
    </span>
    <span class="mono state" class:ok={job.state === 'done'} class:bad={job.state === 'failed'}>
      {job.state.toUpperCase()}
      {#if elapsed}<span class="dim"> · {elapsed}</span>{/if}
    </span>
  </div>

      {#if job.state === 'running'}
        <!-- density trace: fill = percent (within-encode) or batch fraction -->
        <div
          class="trace"
          role="progressbar"
          aria-valuenow={Math.round(stageFraction * 100)}
          aria-valuemin="0"
          aria-valuemax="100"
          aria-label="job progress"
        >
          <div class="fill" style="width: {Math.max(stageFraction * 100, 2)}%"></div>
          <span class="mono reading">
            {progress?.percent !== undefined && progress.percent > 0
              ? `${progress.percent.toFixed(1)}%`
              : progress?.detail ?? 'starting…'}
          </span>
        </div>
      {:else if job.state === 'queued' && queuedNote}
    <div class="queued-note mono dim">{queuedNote}</div>
  {:else if job.error}
    <div class="error mono">{job.error}</div>
  {/if}

  {#if job.state === 'running' || job.state === 'queued'}
    <button class="cancel mono" onclick={() => oncancel?.(job.id!)} aria-label="Cancel job">
      CANCEL
    </button>
  {/if}
</div>

<style>
  .sheet {
    border: 1px solid var(--seam);
    background: var(--panel);
    padding: 12px 16px;
    position: relative;
  }
  .sheet.active {
    background: var(--panel-active);
  }
  .sheet.failed {
    border-color: var(--bad);
  }
  .head {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    gap: 12px;
  }
  .title {
    font-weight: 600;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .note { font-size: 11px; font-weight: 400; }
  .state.ok { color: var(--ok); }
  .state.bad { color: var(--bad); }
  .dim { color: var(--ink-dim); }
  .trace {
    height: 22px;
    margin-top: 8px;
    background: var(--hatch-work);
    border: 1px solid var(--seam);
    position: relative;
  }
  .fill {
    position: absolute;
    left: 0; top: 0; bottom: 0;
    background: var(--hatch-progress);
    border-right: 2px solid var(--ok);
  }
  .reading {
    position: absolute;
    right: 6px; top: 3px;
    color: var(--ink);
    max-width: 80%;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .queued-note { margin-top: 6px; font-size: var(--fs-data); }
  .error {
    margin-top: 6px;
    color: var(--bad);
    font-size: var(--fs-data);
    white-space: normal;
    word-break: break-word;
  }
  .cancel {
    position: absolute;
    right: 12px;
    bottom: -14px;
    background: var(--ground);
    border: 1px solid var(--seam);
    color: var(--ink-dim);
    padding: 2px 10px;
    cursor: pointer;
    font-size: 10px;
    letter-spacing: var(--track-legend);
  }
  .cancel:hover { color: var(--bad); border-color: var(--bad); }
</style>
