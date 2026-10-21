<script lang="ts">
  import { onMount } from 'svelte'
  import { api, type Movie, type Status, type Job, fmtGb, fmtSaved } from './lib/api'
  import StatusStrip from './lib/StatusStrip.svelte'
  import ZoneRamp from './lib/ZoneRamp.svelte'
  import JobSheet from './lib/JobSheet.svelte'
  import LibraryTable from './lib/LibraryTable.svelte'
  import Settings from './lib/Settings.svelte'
  import Duplicates from './lib/Duplicates.svelte'

  let status: Status | null = $state(null)
  let movies: Movie[] = $state([])
  let history: Job[] = $state([])
  let error = $state<string | null>(null)
  let busy = $state(false)
  let showSettings = $state(false)
  let showDuplicates = $state(false)

  // result panels: always present, collapsible, visibility from data (never
  // from a hidden flag that a reload forgets)
  let planOpen = $state(true)
  let encodeOpen = $state(true)

  // per-run arming of the irreversible bar crop — never persisted, so a
  // fresh session always starts unarmed
  let confirmBars = $state(false)

  // persisted defaults (recognition over recall). removeBars is deliberately
  // not persisted: the irreversible option must be armed per run.
  let scanPaths = $state<string[]>(
    JSON.parse(localStorage.getItem('ms.paths') ?? '["D:\\\\Movies"]')
  )
  let newPath = $state('')
  let outputDir = $state(localStorage.getItem('ms.out') ?? 'D:\\Encoded')
  let targetVmaf = $state(Number(localStorage.getItem('ms.vmaf') ?? 95))
  let removeBars = $state(false)

  function persist() {
    localStorage.setItem('ms.paths', JSON.stringify(scanPaths))
    localStorage.setItem('ms.out', outputDir)
    localStorage.setItem('ms.vmaf', String(targetVmaf))
  }

  // movies inside any scan path (case-insensitive prefix) — the blast radius
  // the bars confirmation names
  const barsAffected = $derived(
    movies.filter((m) =>
      scanPaths.some((p) => m.path.toLowerCase().startsWith(p.toLowerCase()))
    ).length
  )

  function addPath() {
    const value = newPath.trim()
    if (!value) return
    if (!scanPaths.some((p) => p.toLowerCase() === value.toLowerCase())) {
      scanPaths = [...scanPaths, value]
      persist()
    }
    newPath = ''
  }

  function removePath(path: string) {
    scanPaths = scanPaths.filter((p) => p !== path)
    persist()
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
    saved_percent?: number
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
  const encodeSavedTotal = $derived(
    encodeEntries
      .filter((o) => o.state === 'encoded' && o.saved_percent != null)
      .reduce((sum, o) => sum + (o.saved_percent ?? 0), 0)
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

  async function enqueue(kind: 'scan' | 'plan' | 'encode', confirmed = false) {
    if (scanPaths.length === 0) {
      error = 'at least one movie folder is required'
      return
    }
    if (kind === 'encode' && removeBars && !confirmed && !confirmBars) {
      // irreversible work demands explicit per-run confirmation
      confirmBars = true
      return
    }
    busy = true
    persist()
    confirmBars = false
    try {
      await api.enqueue({
        kind,
        path: scanPaths[0],
        paths: scanPaths.slice(1),
        output_dir: kind === 'encode' ? outputDir || undefined : undefined,
        quality: kind !== 'scan',
        target_vmaf: Math.min(100, Math.max(70, Number(targetVmaf) || 95)),
        remove_bars: removeBars,
      })
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
        await api.watch('start', scanPaths[0], outputDir)
      }
      await refresh()
    } catch (e) {
      error = e instanceof Error ? e.message : String(e)
    }
  }

  async function retryJob(job: Job) {
    await enqueue(job.kind)
  }

  function dismissFailed(id: number | null) {
    if (id === null) return
    dismissed = new Set([...dismissed, id])
  }

  onMount(() => {
    refresh()
    refreshDuplicates()
    const timer = setInterval(refresh, 3000)
    const dupTimer = setInterval(refreshDuplicates, 30000)
    return () => {
      clearInterval(timer)
      clearInterval(dupTimer)
    }
  })

  const runningJobs = $derived(
    history.filter((j) => j.state === 'running' || j.state === 'queued')
  )
  const failedJobs = $derived(
    history
      .filter((j) => j.state === 'failed' && !dismissed.has(j.id ?? -1))
      .slice(0, 3)
  )
  // dismissed failures of this browser session (the db keeps the history)
  let dismissed = $state<Set<number>>(new Set())

  // duplicate count for the toolbar badge (fetched with the status refresh)
  let dupCount = $state(0)
  async function refreshDuplicates() {
    try {
      const groups = await fetch('/api/duplicates').then((r) => r.json())
      dupCount = groups.length
    } catch {
      dupCount = 0
    }
  }
</script>

<div class="app">
  {#if status}
    <StatusStrip {status} onwatch={toggleWatcher} />
  {:else}
    <div class="strip-loading dim">connecting…</div>
  {/if}

  <div class="content">
    <ZoneRamp {movies} {targetVmaf} />

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

    <!-- failed jobs with recovery (dismissable per session) -->
    {#each failedJobs as job (job.id)}
      <div class="failed-row">
        <span class="mono bad">#{job.id} {job.kind} failed</span>
        <span class="mono dim error-text">{job.error}</span>
        <button class="retry mono" onclick={() => dismissFailed(job.id)}>DISMISS</button>
        <button class="retry mono" onclick={() => retryJob(job)}>RETRY</button>
      </div>
    {/each}

    <!-- actions -->
    <div class="actions">
      <div class="paths">
        {#each scanPaths as path (path)}
          <span class="path-chip mono">
            {path}
            <button class="path-x" onclick={() => removePath(path)} aria-label="Remove {path}">x</button>
          </span>
        {/each}
        <input
          class="mono"
          bind:value={newPath}
          placeholder="add folder + Enter"
          aria-label="Add movie folder"
          onkeydown={(e) => e.key === 'Enter' && addPath()}
          onblur={addPath}
        />
      </div>
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
        class:armed={removeBars}
        title={removeBars ? 'Irreversible: bars are cropped away permanently' : ''}
      >
        {removeBars ? 'Encode · bars' : 'Encode'}
      </button>
      <a class="mono" href="/report" target="_blank" rel="noopener">REPORT ↗</a>
    </div>

    <!-- irreversible-work confirmation strip: names the blast radius -->
    {#if confirmBars}
      <div class="bars-confirm" role="alertdialog" aria-label="Confirm irreversible bar removal">
        <div class="bars-text">
          <strong>BARS WILL BE CROPPED PERMANENTLY.</strong>
          {barsAffected} movie{barsAffected === 1 ? '' : 's'} in {scanPaths.join(', ')} — there
          is no undo.
        </div>
        <div class="bars-buttons">
          <button class="mono confirm" onclick={() => enqueue('encode', true)}>
            CROP {barsAffected} MOVIE{barsAffected === 1 ? '' : 'S'}
          </button>
          <button class="mono deny" onclick={() => (confirmBars = false)}>KEEP BARS</button>
        </div>
      </div>
    {/if}

    <!-- plan results: the tool's unique answer, always present when a plan
         exists in the job history (visibility from data, not hidden state) -->
    {#if planEntries.length > 0}
      <div class="panel">
        <button
          class="panel-head"
          onclick={() => (planOpen = !planOpen)}
          aria-expanded={planOpen}
        >
          <span class="legend">PLAN RESULT — RUN #{lastPlan?.id ?? '—'}</span>
          <span class="dim note">
            {planEntries.filter((p) => p.state === 'encode').length} to encode ·
            {planEntries.filter((p) => p.state === 'skip').length} skipped ·
            {planOpen ? 'HIDE ▲' : 'SHOW ▼'}
          </span>
        </button>
        {#if planOpen}
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
        {/if}
      </div>
    {/if}

    <!-- encode results of the latest run, same data-driven presence -->
    {#if encodeEntries.length > 0}
      <div class="panel">
        <button
          class="panel-head"
          onclick={() => (encodeOpen = !encodeOpen)}
          aria-expanded={encodeOpen}
        >
          <span class="legend">LAST ENCODE RUN — #{lastEncode?.id ?? '—'} · SAVED {encodeSavedTotal}</span>
          <span class="dim note">
            {encodeEntries.filter((o) => o.state === 'encoded').length} encoded ·
            {encodeEntries.filter((o) => o.state === 'failed').length} failed ·
            {encodeOpen ? 'HIDE ▲' : 'SHOW ▼'}
          </span>
        </button>
        {#if encodeOpen}
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
        {/if}
      </div>
    {/if}

    <!-- library -->
    <LibraryTable
      {movies}
      onsettings={() => (showSettings = true)}
      onduplicates={() => (showDuplicates = true)}
      duplicateCount={dupCount}
    />

    {#if showSettings}
      <Settings onclose={() => (showSettings = false)} />
    {/if}
    {#if showDuplicates}
      <Duplicates onclose={() => (showDuplicates = false)} />
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
  .paths {
    display: flex;
    gap: 6px;
    align-items: center;
    flex-wrap: wrap;
  }
  .path-chip {
    background: var(--panel-active);
    border: 1px solid var(--seam);
    padding: 4px 10px;
    display: inline-flex;
    align-items: center;
    gap: 8px;
    max-width: 280px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-size: var(--fs-data);
  }
  .path-x {
    background: transparent;
    border: none;
    color: var(--ink-dim);
    cursor: pointer;
    font-size: 10px;
    padding: 0;
  }
  .path-x:hover { color: var(--bad); }
  .paths input {
    background: transparent;
    border: 1px dashed var(--seam);
    color: var(--ink);
    padding: 4px 10px;
    min-width: 200px;
    font-size: var(--fs-data);
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
  .actions button.primary.armed {
    border-color: var(--warn);
    color: var(--warn);
  }
  .actions button:disabled { opacity: 0.4; cursor: default; }
  .actions a { color: var(--magenta); text-decoration: none; font-size: var(--fs-legend); }
  .panel {
    border: 1px solid var(--seam);
    background: var(--panel);
  }
  .panel-head {
    display: flex;
    justify-content: space-between;
    align-items: center;
    width: 100%;
    padding: 8px 16px;
    border-bottom: 1px solid var(--seam);
    background: transparent;
    border-top: none;
    border-left: none;
    border-right: none;
    color: inherit;
    font: inherit;
    cursor: pointer;
    text-align: left;
  }
  .panel-head:hover .legend { color: var(--ink); }
  .bars-confirm {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 14px;
    flex-wrap: wrap;
    border: 1px solid var(--warn);
    background: var(--panel);
    padding: 10px 14px;
  }
  .bars-text { font-size: var(--fs-data); }
  .bars-buttons { display: flex; gap: 8px; flex-wrap: wrap; }
  .bars-buttons button {
    background: transparent;
    border: 1px solid var(--seam);
    color: var(--ink);
    padding: 5px 14px;
    cursor: pointer;
    font-size: var(--fs-legend);
    letter-spacing: var(--track-legend);
  }
  .bars-buttons .confirm {
    border-color: var(--warn);
    color: var(--warn);
  }
  .bars-buttons .confirm:hover { background: var(--warn); color: var(--ground); }
  .bars-buttons .deny:hover { border-color: var(--ink-dim); }
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
