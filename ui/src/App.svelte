<script lang="ts">
  import { onMount } from 'svelte'
  import { api, type Movie, type Status, type Job, fmtGb, fmtSaved } from './lib/api'
  import StatusStrip from './lib/StatusStrip.svelte'
  import ZoneRamp from './lib/ZoneRamp.svelte'
  import JobSheet from './lib/JobSheet.svelte'
  import LibraryTable from './lib/LibraryTable.svelte'
  import Settings from './lib/Settings.svelte'

  let status: Status | null = $state(null)
  let movies: Movie[] = $state([])
  let history: Job[] = $state([])
  let error = $state<string | null>(null)
  let busy = $state(false)
  let showPlans = $state(false)
  let showSettings = $state(false)

  // persisted defaults (recognition over recall)
  let scanPath = $state(localStorage.getItem('ms.path') ?? 'D:\\Movies')
  let outputDir = $state(localStorage.getItem('ms.out') ?? 'D:\\Encoded')
  let targetVmaf = $state(Number(localStorage.getItem('ms.vmaf') ?? 95))
  let removeBars = $state(localStorage.getItem('ms.bars') === '1')

  function persist() {
    localStorage.setItem('ms.path', scanPath)
    localStorage.setItem('ms.out', outputDir)
    localStorage.setItem('ms.vmaf', String(targetVmaf))
    localStorage.setItem('ms.bars', removeBars ? '1' : '0')
  }

  // derived plan/encode results of the latest finished jobs
  interface PlanEntry {
    movie: string
    state: string
    crf?: number | null
    vmaf?: number | null
    estimated_size?: number
    saved_percent?: number
    reason?: string
    error?: string
  }
  interface OutcomeEntry {
    movie: string
    state: string
    crf?: number | null
    vmaf?: number | null
    input_size?: number
    output_size?: number
    error?: string
  }
  const lastPlan = $derived(
    history.find((j) => j.kind === 'plan' && j.state === 'done' && j.result)
  )
  const planEntries = $derived(
    (lastPlan?.result as { plans?: PlanEntry[] } | null)?.plans ?? []
  )
  const lastEncode = $derived(
    history.find((j) => j.kind === 'encode' && j.state === 'done' && j.result)
  )
  const encodeEntries = $derived(
    (lastEncode?.result as { outcomes?: OutcomeEntry[] } | null)?.outcomes ?? []
  )

  async function refresh() {
    try {
      const [s, lib, jobs] = await Promise.all([api.status(), api.library(), api.jobs(30)])
      status = s
      movies = lib
      history = jobs
      error = null
    } catch (e) {
      error = e instanceof Error ? e.message : String(e)
    }
  }

  async function enqueue(kind: 'scan' | 'plan' | 'encode') {
    if (!scanPath.trim()) {
      error = 'movie folder is required'
      return
    }
    busy = true
    persist()
    try {
      await api.enqueue({
        kind,
        path: scanPath,
        output_dir: kind === 'encode' ? outputDir || undefined : undefined,
        quality: kind !== 'scan',
        target_vmaf: Math.min(100, Math.max(70, Number(targetVmaf) || 95)),
        remove_bars: removeBars,
      })
      if (kind === 'plan') showPlans = true
      await refresh()
    } catch (e) {
      error = e instanceof Error ? e.message : String(e)
    } finally {
      busy = false
    }
  }

  async function cancelJob(id: number | null) {
    if (id === null) return
    await api.cancel(id)
    await refresh()
  }

  async function toggleWatcher() {
    if (!status) return
    try {
      if (status.watcher === 'running') {
        await api.watch('stop')
      } else {
        await api.watch('start', scanPath, outputDir)
      }
      await refresh()
    } catch (e) {
      error = e instanceof Error ? e.message : String(e)
    }
  }

  async function retryJob(job: Job) {
    await enqueue(job.kind)
  }

  onMount(() => {
    refresh()
    const timer = setInterval(refresh, 3000)
    return () => clearInterval(timer)
  })

  const runningJobs = $derived(
    history.filter((j) => j.state === 'running' || j.state === 'queued')
  )
  const failedJobs = $derived(history.filter((j) => j.state === 'failed').slice(0, 3))
</script>

<div class="app">
  {#if status}
    <StatusStrip {status} onwatch={toggleWatcher} />
  {:else}
    <div class="strip-loading dim">connecting…</div>
  {/if}

  <div class="content">
    <ZoneRamp targetVmaf={targetVmaf} />

    {#if error}
      <div class="api-error mono">
        {error}
        <button class="dismiss" onclick={() => (error = null)} aria-label="Dismiss error">✕</button>
      </div>
    {/if}

    <!-- live job area -->
    <div class="jobs">
      {#each runningJobs as job (job.id)}
        <JobSheet {job} oncancel={cancelJob} />
      {:else}
        <div class="idle dim">no active jobs — the queue is empty</div>
      {/each}
    </div>

    <!-- failed jobs with recovery -->
    {#each failedJobs as job (job.id)}
      <div class="failed-row">
        <span class="mono bad">#{job.id} {job.kind} failed</span>
        <span class="mono dim error-text">{job.error}</span>
        <button class="retry mono" onclick={() => retryJob(job)}>RETRY</button>
      </div>
    {/each}

    <!-- actions -->
    <div class="actions">
      <input
        class="mono"
        bind:value={scanPath}
        placeholder="movie folder"
        aria-label="Movie folder path"
      />
      <input
        class="mono"
        bind:value={outputDir}
        placeholder="output folder"
        aria-label="Output folder path (encode)"
      />
      <label class="mono dim" title="VMAF quality floor (70-100)">
        VMAF
        <input
          type="number"
          min="70"
          max="100"
          step="0.5"
          bind:value={targetVmaf}
          onchange={persist}
        />
      </label>
      <label
        class="mono dim bars-label"
        title="Irreversible: stable black bars are cropped away permanently"
      >
        <input type="checkbox" bind:checked={removeBars} onchange={persist} />
        remove bars<span class="warn-mark" aria-hidden="true"> ⚠</span>
      </label>
      <button onclick={() => enqueue('scan')} disabled={busy}>Scan</button>
      <button onclick={() => enqueue('plan')} disabled={busy}>Plan</button>
      <button
        onclick={() => enqueue('encode')}
        disabled={busy}
        class="primary"
        title={removeBars ? 'Irreversible: bars are cropped away permanently' : ''}
      >
        Encode
      </button>
      <a class="mono" href="/report" target="_blank" rel="noopener">REPORT ↗</a>
    </div>

    <!-- plan results: the tool's unique answer, no longer discarded -->
    {#if showPlans && planEntries.length > 0}
      <div class="panel">
        <div class="panel-head">
          <span class="legend">PLAN RESULT</span>
          <span class="dim note">
            {planEntries.filter((p) => p.state === 'encode').length} to encode ·
            {planEntries.filter((p) => p.state === 'skip').length} skipped
          </span>
        </div>
        <table class="plan-table">
          <tbody>
            {#each planEntries as entry (entry.movie)}
              <tr>
                <td class="name">{entry.movie}</td>
                <td>
                  {#if entry.state === 'encode'}
                    <span class="chip done"><span class="swatch"></span>ENCODE</span>
                  {:else if entry.state === 'skip'}
                    <span class="chip skip"><span class="swatch"></span>SKIP</span>
                  {:else}
                    <span class="chip failed"><span class="swatch"></span>{entry.state.toUpperCase()}</span>
                  {/if}
                </td>
                <td class="mono num">{entry.crf ?? '—'}</td>
                <td class="mono num">{entry.vmaf?.toFixed(2) ?? '—'}</td>
                <td class="mono num">{fmtGb(entry.estimated_size)}</td>
                <td class="mono num">{fmtSaved(entry.saved_percent)}</td>
                <td class="dim reason">{entry.reason ?? entry.error ?? ''}</td>
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
    {/if}

    <!-- encode results of the last run -->
    {#if !showPlans && encodeEntries.length > 0}
      <div class="panel">
        <div class="panel-head">
          <span class="legend">LAST ENCODE RUN</span>
          <span class="dim note">
            {encodeEntries.filter((o) => o.state === 'encoded').length} encoded ·
            {encodeEntries.filter((o) => o.state === 'failed').length} failed
          </span>
        </div>
        <table class="plan-table">
          <tbody>
            {#each encodeEntries as entry (entry.movie)}
              <tr>
                <td class="name">{entry.movie}</td>
                <td>
                  <span class="chip {entry.state === 'encoded' ? 'done' : entry.state === 'skipped' ? 'skip' : 'failed'}">
                    <span class="swatch"></span>{entry.state.toUpperCase()}
                  </span>
                </td>
                <td class="mono num">{fmtGb(entry.input_size)}</td>
                <td class="mono num">{fmtGb(entry.output_size)}</td>
                <td class="mono num">{entry.crf ?? '—'}</td>
                <td class="mono num">{entry.vmaf?.toFixed(2) ?? '—'}</td>
                <td class="dim reason">{entry.error ?? ''}</td>
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
    {/if}

    <!-- library -->
    <LibraryTable {movies} onsettings={() => (showSettings = true)} />

    {#if showSettings}
      <Settings onclose={() => (showSettings = false)} />
    {/if}
  </div>
</div>

<style>
  .app {
    min-height: 100vh;
    background: var(--ground);
    color: var(--ink);
    display: flex;
    flex-direction: column;
    font-size: var(--fs-body);
  }
  .strip-loading {
    height: 52px;
    border-bottom: 1px solid var(--seam);
    display: flex;
    align-items: center;
    padding: 0 20px;
  }
  .dim { color: var(--ink-dim); }
  .bad { color: var(--bad); }
  .content {
    flex: 1;
    display: flex;
    flex-direction: column;
    gap: 14px;
    padding: 18px 20px;
    max-width: 1400px;
    width: 100%;
    margin: 0 auto;
  }
  .jobs { display: flex; flex-direction: column; gap: 8px; }
  .idle {
    border: 1px dashed var(--seam);
    padding: 14px;
    text-align: center;
    font-size: var(--fs-data);
  }
  .api-error {
    border: 1px solid var(--bad);
    color: var(--bad);
    padding: 8px 14px;
    font-size: var(--fs-data);
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 12px;
    word-break: break-word;
  }
  .dismiss {
    background: transparent;
    border: none;
    color: var(--bad);
    cursor: pointer;
    font-size: 12px;
  }
  .failed-row {
    display: flex;
    gap: 12px;
    align-items: center;
    border: 1px solid var(--bad);
    padding: 8px 14px;
    background: var(--panel);
    flex-wrap: wrap;
  }
  .error-text {
    flex: 1;
    min-width: 200px;
    word-break: break-word;
  }
  .retry {
    background: transparent;
    border: 1px solid var(--seam);
    color: var(--ink);
    padding: 2px 10px;
    cursor: pointer;
    font-size: 10px;
    letter-spacing: var(--track-legend);
  }
  .retry:hover { border-color: var(--ok); color: var(--ok); }
  .actions {
    display: flex;
    gap: 10px;
    align-items: center;
    flex-wrap: wrap;
  }
  .actions input:not([type='checkbox']):not([type='number']) {
    background: var(--panel);
    border: 1px solid var(--seam);
    color: var(--ink);
    padding: 6px 10px;
    min-width: 220px;
  }
  .actions input[type='number'] {
    background: var(--panel);
    border: 1px solid var(--seam);
    color: var(--ink);
    width: 64px;
    padding: 6px 8px;
  }
  .bars-label { display: inline-flex; align-items: center; gap: 6px; }
  .warn-mark { color: var(--warn); }
  .actions button {
    background: var(--panel-active);
    border: 1px solid var(--seam);
    color: var(--ink);
    padding: 6px 16px;
    cursor: pointer;
    font-size: var(--fs-legend);
    letter-spacing: var(--track-legend);
    text-transform: uppercase;
  }
  .actions button:hover:not(:disabled) { border-color: var(--ink-dim); }
  .actions button.primary { border-color: var(--ok); color: var(--ok); }
  .actions button:disabled { opacity: 0.4; cursor: default; }
  .actions a { color: var(--magenta); text-decoration: none; font-size: var(--fs-legend); }
  .panel {
    border: 1px solid var(--seam);
    background: var(--panel);
  }
  .panel-head {
    display: flex;
    justify-content: space-between;
    padding: 8px 16px;
    border-bottom: 1px solid var(--seam);
  }
  .note { font-size: 11px; }
  .plan-table { width: 100%; border-collapse: collapse; }
  .plan-table td {
    padding: 7px 16px;
    border-bottom: 1px solid var(--row-line);
    white-space: nowrap;
  }
  .plan-table tr:last-child td { border-bottom: none; }
  .plan-table td.num { text-align: right; }
  .plan-table td.name { font-weight: 600; max-width: 300px; overflow: hidden; text-overflow: ellipsis; }
  .reason { max-width: 280px; overflow: hidden; text-overflow: ellipsis; font-size: 11px; }
</style>
