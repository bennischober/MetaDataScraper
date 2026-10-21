<script lang="ts">
  import { onMount } from 'svelte'
  import { api, type Movie, type Status, type Job, fmtGb, fmtSaved } from './lib/api'
  import EvidenceWall from './lib/EvidenceWall.svelte'
  import LibrarySheet from './lib/LibrarySheet.svelte'
  import DuplicatesSheet from './lib/DuplicatesSheet.svelte'
  import JobsSheet from './lib/JobsSheet.svelte'
  import SettingsSheet from './lib/SettingsSheet.svelte'

  type Domain = 'library' | 'duplicates' | 'jobs' | 'settings'

  let status: Status | null = $state(null)
  let movies: Movie[] = $state([])
  let history: Job[] = $state([])
  let duplicates: DuplicateGroup[] = $state([])
  let error = $state<string | null>(null)
  let busy = $state(false)
  let domain = $state<Domain>('library')

  // ── selection (bulk operations are the primary verbs) ──
  let selectedPaths = $state<Set<string>>(new Set())

  const selectedCount = $derived(selectedPaths.size)
  const selectedMovies = $derived(movies.filter((m) => selectedPaths.has(m.path)))

  // annotated estimate: the drawing sheet talks back
  const selectedGb = $derived(
    selectedMovies.reduce((sum, m) => sum + (m.encode?.output_size ?? m.size ?? 0), 0)
  )
  const selectedSavedEstimate = $derived(
    selectedMovies
      .filter((m) => !m.encode)
      .reduce((sum, m) => sum + (m.size ?? 0) * 0.45, 0)
  )

  function toggleSelect(path: string, checked: boolean) {
    const next = new Set(selectedPaths)
    if (checked) next.add(path)
    else next.delete(path)
    selectedPaths = next
  }
  function selectAll(checked: boolean) {
    selectedPaths = checked ? new Set(movies.map((m) => m.path)) : new Set()
  }
  function selectAllFiltered(checked: boolean, paths: string[]) {
    const next = new Set(selectedPaths)
    if (checked) paths.forEach((p) => next.add(p))
    else paths.forEach((p) => next.delete(p))
    selectedPaths = next
  }
  function clearSelection() {
    selectedPaths = new Set()
  }

  // ── job parameters (persisted defaults; bars never persist) ──
  let scanPaths = $state<string[]>(
    JSON.parse(localStorage.getItem('ms.paths') ?? '["D:\\\\Movies"]')
  )
  let newPath = $state('')
  let outputDir = $state(localStorage.getItem('ms.out') ?? 'D:\\Encoded')
  let targetVmaf = $state(Number(localStorage.getItem('ms.vmaf') ?? 95))
  let removeBars = $state(false)

  // per-run arming of the irreversible bar crop — never persisted
  let confirmBars = $state(false)
  const barsAffected = $derived(
    movies.filter((m) =>
      scanPaths.some((p) => m.path.toLowerCase().startsWith(p.toLowerCase()))
    ).length
  )

  function persist() {
    localStorage.setItem('ms.paths', JSON.stringify(scanPaths))
    localStorage.setItem('ms.out', outputDir)
    localStorage.setItem('ms.vmaf', String(targetVmaf))
  }

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

  // ── plan/encode results of the latest finished jobs ──
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

  interface DuplicateFile {
    path: string
    name: string
    category: string
    size: number
    duration: number | null
    codec: string | null
    hash: string | null
    resolution: [number, number] | null
  }
  interface DuplicateGroup {
    reason: string
    files: DuplicateFile[]
    wasted_bytes: number
  }
  const duplicateWasted = $derived(duplicates.reduce((sum, g) => sum + g.wasted_bytes, 0))

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

  async function refreshDuplicates() {
    try {
      const r = await fetch('/api/duplicates')
      if (r.ok) duplicates = await r.json()
    } catch {
      /* duplicates are auxiliary; library reads stay live */
    }
  }

  // ── enqueue: global (all) or selection-scoped ──
  async function enqueue(
    kind: 'scan' | 'plan' | 'encode',
    scope: 'all' | 'selected' = 'all',
    confirmed = false
  ) {
    const targets =
      scope === 'selected' ? selectedMovies.map((m) => m.path) : scanPaths
    if (targets.length === 0) {
      error = scope === 'selected' ? 'select at least one movie' : 'at least one movie folder is required'
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
      if (scope === 'selected') {
        // per-movie jobs, one per selected title (the backend takes one path per job)
        for (const t of targets) {
          await api.enqueue({
            kind,
            path: t,
            output_dir: kind === 'encode' ? outputDir || undefined : undefined,
            quality: kind !== 'scan',
            target_vmaf: Math.min(100, Math.max(70, Number(targetVmaf) || 95)),
            remove_bars: removeBars,
          })
        }
      } else {
        await api.enqueue({
          kind,
          path: targets[0],
          paths: targets.slice(1),
          output_dir: kind === 'encode' ? outputDir || undefined : undefined,
          quality: kind !== 'scan',
          target_vmaf: Math.min(100, Math.max(70, Number(targetVmaf) || 95)),
          remove_bars: removeBars,
        })
      }
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

  function dismissFailed(id: number | null) {
    if (id === null) return
    dismissed = new Set([...dismissed, id])
  }

  // encode a single movie straight from its detail callout
  async function encodeOne(path: string) {
    busy = true
    try {
      await api.enqueue({
        kind: 'encode',
        path,
        output_dir: outputDir || undefined,
        quality: true,
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

  let dismissed = $state<Set<number>>(new Set())
  const runningJobs = $derived(
    history.filter((j) => (j.state === 'running' || j.state === 'queued') && j.id !== null && !dismissed.has(j.id))
  )
  const failedJobs = $derived(
    history.filter((j) => j.state === 'failed' && j.id !== null && !dismissed.has(j.id))
  )
  // the figure in progress: the movie the running job is currently encoding
  const runningJob = $derived(runningJobs.find((j) => j.state === 'running') ?? null)
  const runningProgress = $derived(
    (runningJob?.result as { progress?: { movie?: string; percent?: number } } | null)?.progress ?? null
  )

  onMount(() => {
    refresh()
    refreshDuplicates()
    const timer = setInterval(refresh, 3000)
    return () => clearInterval(timer)
  })

  const measuredCount = $derived(movies.filter((m) => m.encode?.vmaf != null).length)
  const savedTotalGb = $derived(
    movies.reduce((sum, m) => {
      if (!m.encode || m.encode.saved_percent == null) return sum
      return sum + ((m.encode.input_size ?? 0) - (m.encode.output_size ?? 0))
    }, 0) / 1024 ** 3
  )
</script>

<div class="sheet sheet-grid">
  <!-- ══ title block ══ -->
  <header class="title-block">
    <div class="tb-main">
      <h1>SHRINKRAY</h1>
      <p class="legend">LIBRARY SURVEY · 219 TITLES · VMAF TARGET {targetVmaf}</p>
    </div>
    <div class="tb-cells">
      <div class="tb-cell">
        <span class="tb-k">SHEET</span>
        <span class="tb-v">{domain === 'library' ? '01 · LIBRARY' : domain === 'duplicates' ? '02 · DUPLICATES' : domain === 'jobs' ? '03 · JOBS' : '04 · CONFIG'}</span>
      </div>
      <div class="tb-cell">
        <span class="tb-k">SAVED</span>
        <span class="tb-v mono">{savedTotalGb.toFixed(1)} GB</span>
      </div>
      <button class="tb-cell tb-btn" onclick={toggleWatcher}>
        <span class="tb-k">WATCHER</span>
        <span class="tb-v" class:teal={status?.watcher === 'running'}>
          {status?.watcher === 'running' ? '● RUNNING' : '○ STOPPED'}
        </span>
      </button>
      <div class="tb-cell">
        <span class="tb-k">QUEUE</span>
        <span class="tb-v mono">{runningJobs.length}</span>
      </div>
    </div>
  </header>

  {#if error}
    <div class="api-error mono" role="alert">
      {error}
      <button class="dismiss" onclick={() => (error = null)} aria-label="Dismiss error">✕</button>
    </div>
  {/if}

  <!-- ══ FIG. 1 — the evidence wall ══ -->
  <EvidenceWall {movies} {targetVmaf} />

  <!-- ══ domain tabs ══ -->
  <nav class="tabs" aria-label="Data domains">
    <button class="tab" class:active={domain === 'library'} onclick={() => (domain = 'library')}>
      LIBRARY <span class="tab-n mono">{movies.length}</span>
    </button>
    <button class="tab" class:active={domain === 'duplicates'} onclick={() => { domain = 'duplicates'; refreshDuplicates() }}>
      DUPLICATES <span class="tab-n mono" class:warn={duplicates.length > 0}>{duplicates.length}</span>
    </button>
    <button class="tab" class:active={domain === 'jobs'} onclick={() => (domain = 'jobs')}>
      JOBS <span class="tab-n mono" class:warn={runningJobs.length > 0}>{history.length}</span>
    </button>
    <span class="tab-spacer"></span>
    <button class="tab" class:active={domain === 'settings'} onclick={() => (domain = 'settings')}>
      CONFIG
    </button>
  </nav>

  <!-- ══ bulk bar: the sheet's action strip (above the work, always visible) ══ -->
  <div class="bulk-bar">
    <div class="bulk-left">
      <label class="sel-all">
        <input
          type="checkbox"
          checked={selectedCount > 0 && selectedCount === movies.length}
          indeterminate={selectedCount > 0 && selectedCount < movies.length}
          onchange={(e) => selectAll(e.currentTarget.checked)}
          aria-label="Select all movies"
        />
        <span class="mono">{selectedCount} SELECTED</span>
      </label>
      <button class="act" onclick={() => enqueue('encode', 'selected')} disabled={busy || selectedCount === 0}>
        ENCODE SELECTED
      </button>
      <button class="act" onclick={() => enqueue('plan', 'selected')} disabled={busy || selectedCount === 0}>
        PLAN SELECTED
      </button>
      <button class="act quiet" onclick={clearSelection} disabled={selectedCount === 0}>CLEAR</button>
      <span class="leader-note mono leader-in" class:hidden={selectedCount === 0}>
        → EST. −{(selectedSavedEstimate / 1024 ** 3).toFixed(1)} GB · {fmtGb(selectedGb)} BASE
      </span>
    </div>
    <div class="bulk-right">
      <label class="mono dim" title="VMAF quality floor (70-100)">
        TARGET
        <input class="vmaf-input mono" type="number" min="70" max="100" step="0.5" bind:value={targetVmaf} onchange={persist} />
      </label>
      <button class="act" onclick={() => enqueue('scan')} disabled={busy}>SCAN ALL</button>
      <button class="act" onclick={() => enqueue('plan')} disabled={busy}>PLAN ALL</button>
      <button class="act primary" onclick={() => enqueue('encode')} disabled={busy}>ENCODE ALL</button>
    </div>
  </div>

  <!-- ══ domain sheets ══ -->
  {#if domain === 'library'}
    <LibrarySheet
      {movies}
      {selectedPaths}
      runningMovie={runningProgress?.movie ?? null}
      runningPercent={runningProgress?.percent ?? null}
      plannedPaths={planEntries.filter((p) => p.state === 'encode').map((p) => p.movie)}
      ontoggle={toggleSelect}
      onselectall={selectAllFiltered}
      onencode={(p) => encodeOne(p)}
      {targetVmaf}
    />
  {:else if domain === 'duplicates'}
    <DuplicatesSheet {duplicates} onrefresh={refreshDuplicates} />
  {:else if domain === 'jobs'}
    <JobsSheet {history} oncancel={cancelJob} onretry={(j) => enqueue(j.kind, 'all')} />
  {:else}
    <SettingsSheet onclose={() => (domain = 'library')} />
  {/if}

  <!-- ══ scan paths (the drawing's source note) ══ -->
  <div class="paths mono">
    <span class="legend">SOURCES</span>
    {#each scanPaths as path (path)}
      <span class="path-chip mono">
        {path}
        <button class="path-x" onclick={() => removePath(path)} aria-label="Remove {path}">✕</button>
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
    <label class="mono dim bars-label" title="Irreversible: stable black bars are cropped away permanently">
      <input type="checkbox" bind:checked={removeBars} />
      REMOVE BARS<span class="bars-warn" aria-hidden="true"></span>
    </label>
  </div>

  <!-- ══ irreversible-work confirmation strip ══ -->
  {#if confirmBars}
    <div class="bars-confirm" role="alertdialog" aria-label="Confirm irreversible bar removal">
      <div class="bars-text">
        <strong>BARS WILL BE CROPPED PERMANENTLY.</strong>
        {barsAffected} movie{barsAffected === 1 ? '' : 's'} in {scanPaths.join(', ')} — there is no undo.
      </div>
      <div class="bars-buttons">
        <button class="mono confirm" onclick={() => enqueue('encode', 'all', true)}>
          CROP {barsAffected} MOVIE{barsAffected === 1 ? '' : 'S'}
        </button>
        <button class="mono deny" onclick={() => (confirmBars = false)}>KEEP BARS</button>
      </div>
    </div>
  {/if}

  <!-- ══ running + failed jobs: the figure in progress ══ -->
  {#if domain === 'library'}
    <div class="live-area">
      {#each runningJobs as job (job.id)}
        <div class="job-band">
          <span class="job-state mono amber">IN WORK</span>
          <span class="job-name">{(job.payload?.path as string)?.split(/[\\/]/).pop() || job.kind}</span>
          <span class="mono dim">{job.kind} · {job.state}</span>
          <button class="act quiet" onclick={() => cancelJob(job.id)}>CANCEL</button>
        </div>
      {:else}
        <span class="mono dim idle-note">no active work — the sheet is quiet</span>
      {/each}
      {#each failedJobs.slice(0, 3) as job (job.id)}
        <div class="job-band failed">
          <span class="job-state mono red">FAILED #{job.id}</span>
          <span class="mono dim">{job.error}</span>
          <button class="act quiet" onclick={() => dismissFailed(job.id)}>DISMISS</button>
        </div>
      {/each}
    </div>
  {/if}

  <footer class="colophon legend">
    SHRINKRAY · SOURCES NEVER MODIFIED IN PLACE · ENCODES VERIFIED AGAINST TARGET BEFORE A PLAN ENTRY CLOSES
  </footer>
</div>

<style>
  .sheet {
    min-height: 100vh;
    background: var(--ground);
    color: var(--ink);
    display: flex;
    flex-direction: column;
    gap: 14px;
    padding: 18px 26px 30px;
    font-size: var(--fs-body);
  }

  /* ══ title block ══ */
  .title-block {
    display: flex;
    justify-content: space-between;
    align-items: stretch;
    gap: 18px;
    border-bottom: 2px solid var(--ink);
    padding-bottom: 12px;
  }
  .tb-main h1 {
    font-size: 19px;
    font-weight: 700;
    letter-spacing: 3px;
  }
  .tb-main .legend { margin-top: 2px; }
  .tb-cells {
    display: flex;
    border: 1px solid var(--ink);
  }
  .tb-cell {
    display: flex;
    flex-direction: column;
    gap: 2px;
    padding: 6px 14px;
    border-left: 1px solid var(--seam);
    text-align: left;
    background: transparent;
    font: inherit;
    color: inherit;
  }
  .tb-cell:first-child { border-left: none; }
  .tb-k { font-size: 9px; letter-spacing: var(--track-legend); color: var(--ink-dim); text-transform: uppercase; }
  .tb-v { font-size: var(--fs-data); font-weight: 600; }
  .tb-btn { cursor: pointer; }
  .tb-btn:hover { background: var(--panel-active); }
  .tb-v.teal { color: var(--teal-ink); }

  /* ══ tabs ══ */
  .tabs {
    display: flex;
    align-items: center;
    gap: 4px;
    border-bottom: 1px solid var(--seam);
  }
  .tab {
    background: transparent;
    border: 1px solid transparent;
    border-bottom: none;
    color: var(--ink-dim);
    padding: 6px 14px;
    cursor: pointer;
    font-size: var(--fs-legend);
    letter-spacing: var(--track-legend);
    text-transform: uppercase;
  }
  .tab:hover { color: var(--ink); }
  .tab.active {
    color: var(--teal-ink);
    border-color: var(--seam);
    background: var(--panel);
    border-bottom: 2px solid var(--teal);
    margin-bottom: -1px;
  }
  .tab-n {
    margin-left: 4px;
    color: var(--ink-dim);
  }
  .tab-n.warn { color: var(--amber); }
  .tab-spacer { flex: 1; }

  /* ══ bulk bar ══ */
  .bulk-bar {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 14px;
    flex-wrap: wrap;
    border: 1px solid var(--ink);
    background: var(--panel);
    padding: 8px 14px;
  }
  .bulk-left, .bulk-right { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
  .sel-all { display: inline-flex; align-items: center; gap: 8px; cursor: pointer; }
  .sel-all input { accent-color: var(--teal); width: 14px; height: 14px; }
  .leader-note { color: var(--teal-ink); white-space: nowrap; }
  .leader-note.hidden { display: none; }
  .act {
    background: var(--panel);
    border: 1px solid var(--ink);
    color: var(--ink);
    padding: 5px 14px;
    cursor: pointer;
    font-size: var(--fs-legend);
    letter-spacing: var(--track-legend);
    text-transform: uppercase;
  }
  .act:hover:not(:disabled) { background: var(--panel-active); border-color: var(--teal-ink); color: var(--teal-ink); }
  .act.primary { background: var(--teal); border-color: var(--teal); color: #fff; }
  .act.primary:hover:not(:disabled) { background: var(--teal-ink); color: #fff; }
  .act.quiet { border-color: var(--seam); color: var(--ink-dim); }
  .act:disabled { opacity: 0.4; cursor: default; }
  .dim { color: var(--ink-dim); }
  .vmaf-input {
    background: var(--panel);
    border: 1px solid var(--seam);
    color: var(--ink);
    width: 58px;
    padding: 4px 6px;
  }

  /* ══ sources strip ══ */
  .paths {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
  }
  .path-chip {
    background: var(--panel);
    border: 1px solid var(--seam);
    padding: 3px 10px;
    display: inline-flex;
    align-items: center;
    gap: 8px;
    max-width: 300px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    direction: rtl; /* truncate the start: the tail names the folder */
  }
  .path-x {
    background: transparent;
    border: none;
    color: var(--ink-faint);
    cursor: pointer;
    font-size: 11px;
    padding: 0;
  }
  .path-x:hover { color: var(--red); }
  .paths input:not([type='checkbox']) {
    background: transparent;
    border: 1px dashed var(--seam);
    color: var(--ink);
    padding: 3px 10px;
    min-width: 200px;
  }
  .bars-label { display: inline-flex; align-items: center; gap: 6px; margin-left: auto; }
  /* drawn warning mark: two bold strokes, not a unicode glyph */
  .bars-warn {
    display: inline-block;
    width: 10px;
    height: 10px;
    background:
      linear-gradient(to bottom right, transparent calc(50% - 1.5px), var(--amber) calc(50% - 1.5px), var(--amber) calc(50% + 1.5px), transparent calc(50% + 1.5px)),
      linear-gradient(to bottom left, transparent calc(50% - 1.5px), var(--amber) calc(50% - 1.5px), var(--amber) calc(50% + 1.5px), transparent calc(50% + 1.5px));
  }

  /* ══ confirmation strip ══ */
  .bars-confirm {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 14px;
    flex-wrap: wrap;
    border: 1px solid var(--red);
    background: repeating-linear-gradient(45deg, rgba(179, 56, 44, 0.07) 0 4px, transparent 4px 8px), var(--panel);
    padding: 10px 14px;
  }
  .bars-text { font-size: var(--fs-data); }
  .bars-buttons { display: flex; gap: 8px; }
  .bars-buttons button {
    background: transparent;
    border: 1px solid var(--seam);
    color: var(--ink);
    padding: 5px 14px;
    cursor: pointer;
    font-size: var(--fs-legend);
    letter-spacing: var(--track-legend);
  }
  .bars-buttons .confirm { border-color: var(--red); color: var(--red); }
  .bars-buttons .confirm:hover { background: var(--red); color: #fff; }
  .bars-buttons .deny:hover { border-color: var(--ink-dim); }

  /* ══ live job bands ══ */
  .live-area { display: flex; flex-direction: column; gap: 6px; }
  .job-band {
    display: flex;
    align-items: center;
    gap: 12px;
    border: 1px solid var(--seam);
    background: var(--panel);
    padding: 7px 12px;
  }
  .job-band.failed { border-color: var(--red); }
  .job-band .job-state.amber { color: var(--amber); }
  .job-state { font-size: var(--fs-legend); letter-spacing: var(--track-legend); }
  .amber { color: var(--amber); }
  .red { color: var(--red); }
  .job-name { font-weight: 600; flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .idle-note { padding: 4px 0; }
  .api-error {
    border: 1px solid var(--red);
    color: var(--red);
    background: repeating-linear-gradient(45deg, rgba(179, 56, 44, 0.06) 0 4px, transparent 4px 8px), var(--panel);
    padding: 8px 14px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 12px;
    word-break: break-word;
  }
  .dismiss {
    background: transparent;
    border: none;
    color: var(--red);
    cursor: pointer;
    font-size: 12px;
  }

  /* ══ colophon ══ */
  .colophon {
    margin-top: auto;
    border-top: 1px solid var(--seam);
    padding-top: 10px;
    text-align: center;
  }

  @media (max-width: 760px) {
    .sheet { padding: 12px; }
    .tb-cells {
      width: 100%;
      justify-content: flex-end;
    }
    .tb-cell { padding: 4px 10px; }
    .bulk-bar { flex-direction: column; align-items: stretch; }
    .bulk-right { justify-content: flex-end; }
  }
</style>
