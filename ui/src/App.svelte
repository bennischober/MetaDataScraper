<script lang="ts">
  import { onMount } from 'svelte'
  import { api, jobPaths, type Movie, type Status, type Job } from './lib/api'
  import OverviewPage from './lib/pages/OverviewPage.svelte'
  import LibraryPage from './lib/pages/LibraryPage.svelte'
  import DuplicatesPage from './lib/pages/DuplicatesPage.svelte'
  import JobsPage from './lib/pages/JobsPage.svelte'
  import ConfigPage from './lib/pages/ConfigPage.svelte'
  import MovieDetailPage from './lib/pages/MovieDetailPage.svelte'

  type Page = 'overview' | 'library' | 'duplicates' | 'jobs' | 'config'

  let status: Status | null = $state(null)
  let movies: Movie[] = $state([])
  let history: Job[] = $state([])
  let duplicates: DuplicateGroup[] = $state([])
  let error = $state<string | null>(null)

  // theme: dark default, toggle persisted
  let theme = $state<'dark' | 'light'>(
    localStorage.getItem('ms.theme') === 'light' ? 'light' : 'dark'
  )
  $effect(() => {
    document.documentElement.dataset.theme = theme
    localStorage.setItem('ms.theme', theme)
  })

  // ── routing: hash-based, so reload/bookmark keeps the position ──
  const PAGES: Page[] = ['overview', 'library', 'duplicates', 'jobs', 'config']
  function pageFromHash(): Page {
    const h = location.hash.replace(/^#\/?/, '')
    return (PAGES as string[]).includes(h) ? (h as Page) : 'overview'
  }
  let page = $state<Page>(pageFromHash())
  // the single navigation gate: any page change closes the movie detail
  // (the detail page replaces the page stack, so a rail click that only
  // changed `page` behind the overlay looked like nothing happened)
  let detailMovie = $state<Movie | null>(null)
  // where the detail was opened from — the back button returns there
  let detailFrom = $state<'overview' | 'library'>('overview')
  function goto(next: Page) {
    detailMovie = null
    page = next
    location.hash = `/${next}`
  }
  function openDetail(movie: Movie, from: 'overview' | 'library') {
    detailMovie = movie
    detailFrom = from
  }
  function closeDetail() {
    detailMovie = null
    page = detailFrom
    location.hash = `/${detailFrom}`
  }

  // back/forward support
  window.addEventListener('hashchange', () => (page = pageFromHash()))

  // ── keyboard layer: 1-5 switch pages, / focuses search ──
  function handleShortcuts(e: KeyboardEvent) {
    // never hijack typing in inputs
    const t = e.target as HTMLElement
    if (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT') return
    if (e.metaKey || e.ctrlKey || e.altKey) return
    const map: Record<string, Page> = { '1': 'overview', '2': 'library', '3': 'duplicates', '4': 'jobs', '5': 'config' }
    if (map[e.key]) {
      goto(map[e.key])
    } else if (e.key === '/') {
      e.preventDefault()
      goto('library')
      queueMicrotask(() => document.querySelector<HTMLInputElement>('.search')?.focus())
    }
  }

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

  // ── job parameters (persisted defaults; bars never persist) ──
  let scanPaths = $state<string[]>(
    JSON.parse(localStorage.getItem('ms.paths') ?? '["D:\\\\Movies"]')
  )
  let outputDir = $state(localStorage.getItem('ms.out') ?? 'D:\\Encoded')
  let targetVmaf = $state(Number(localStorage.getItem('ms.vmaf') ?? 95))
  let workers = $state<number | null>(
    localStorage.getItem('ms.workers') ? Number(localStorage.getItem('ms.workers')) : null
  )
  // per-run arming of the irreversible bar crop — never persisted, so a
  // fresh session always starts unarmed
  let removeBars = $state(false)

  function persist() {
    localStorage.setItem('ms.paths', JSON.stringify(scanPaths))
    localStorage.setItem('ms.out', outputDir)
    localStorage.setItem('ms.vmaf', String(targetVmaf))
    if (workers != null) localStorage.setItem('ms.workers', String(workers))
    else localStorage.removeItem('ms.workers')
  }

  // movies inside any scan path (case-insensitive prefix) — used for the
  // bars blast-radius when the targets are the whole library
  const barsAffected = $derived(
    movies.filter((m) =>
      scanPaths.some((p) => m.path.toLowerCase().startsWith(p.toLowerCase()))
    ).length
  )

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
  const lastPlan = $derived(
    history.find((j) => j.kind === 'plan' && j.state === 'done' && j.result)
  )
  const planEntries = $derived(
    (lastPlan?.result as { plans?: PlanEntry[] } | null)?.plans ?? []
  )

  // ── selection (bulk ops live on the library page) ──
  let selectedPaths = $state<Set<string>>(new Set())

  // ── refresh loop ──
  async function refresh() {
    try {
      const snap = await api.snapshot()
      status = snap.status
      history = snap.jobs
      if (snap.library !== null) movies = snap.library // unchanged: keep the current array
      error = null
    } catch (e) {
      error = e instanceof Error ? e.message : String(e)
    }
  }

  // adaptive polling: one snapshot request per tick, fast (1s) while work
  // is queued/running, relaxed (3s) when idle, paused entirely while the
  // tab is hidden (the browser throttles it anyway — this is explicit)
  const POLL_ACTIVE = 1000
  const POLL_IDLE = 3000
  let pollTimer: ReturnType<typeof setTimeout> | null = null
  async function poll() {
    await refresh()
    const busy = history.some((j) => j.state === 'queued' || j.state === 'running')
    const hidden = document.hidden
    schedule(busy ? POLL_ACTIVE : POLL_IDLE, hidden)
  }
  function schedule(interval: number, hidden: boolean) {
    pollTimer = setTimeout(poll, hidden ? 10000 : interval)
  }

  async function refreshDuplicates() {
    try {
      const r = await fetch('/api/duplicates')
      if (r.ok) duplicates = await r.json()
    } catch {
      /* auxiliary; library reads stay live */
    }
  }

  // pending: global click-guard + per-kind queue guard (no job spam: a kind
  // already queued/running disables its enqueue buttons)
  let pendingKind = $state<'scan' | 'plan' | 'encode' | 'dedupe' | 'cancel' | null>(null)
  const kindQueued = $derived(
    (kind: 'scan' | 'plan' | 'encode' | 'dedupe') =>
      history.some((j) => j.kind === kind && (j.state === 'queued' || j.state === 'running'))
  )

  // ── the bars ceremony: the single gate for EVERY encode ──
  // The confirmation carries the actual target list (never re-derived at
  // confirm time) and is the only place an encode with bars armed passes
  // through — movie page, bulk selection and ENCODE ALL all route here.
  let pendingBarsTargets = $state<string[] | null>(null)
  let barsConfirmEl: HTMLDivElement | null = $state(null)
  $effect(() => {
    if (pendingBarsTargets) barsConfirmEl?.focus()
  })
  const barsAffectedList = $derived(
    (pendingBarsTargets ?? []).filter((t) =>
      scanPaths.some((p) => t.toLowerCase().startsWith(p.toLowerCase()))
    )
  )

  function requestEncode(paths: string[] | undefined, confirmed = false) {
    const targets = paths ?? scanPaths
    if (removeBars && !confirmed) {
      // irreversible work demands explicit per-run confirmation naming
      // the exact targets
      pendingBarsTargets = targets
      return
    }
    void enqueue('encode', paths, true)
  }

  async function enqueue(
    kind: 'scan' | 'plan' | 'encode' | 'dedupe',
    paths?: string[],
    confirmed = false
  ) {
    if (pendingKind !== null) return
    if (kindQueued(kind)) {
      error = `${kind} is already queued — it runs next`
      return
    }
    const targets = paths ?? scanPaths
    if (kind !== 'dedupe' && targets.length === 0) {
      error = paths ? 'select at least one movie' : 'at least one movie folder is required'
      return
    }
    if (kind === 'encode' && !confirmed) {
      // every encode routes through the ceremony
      requestEncode(paths, false)
      return
    }
    pendingKind = kind
    persist()
    pendingBarsTargets = null
    try {
      if (kind === 'dedupe') {
        await api.enqueue({ kind })
      } else {
        await api.enqueue({
          kind,
          path: targets[0],
          paths: targets.slice(1),
          output_dir: kind === 'encode' ? outputDir || undefined : undefined,
          quality: kind !== 'scan',
          target_vmaf: Math.min(100, Math.max(70, Number(targetVmaf) || 95)),
          remove_bars: removeBars,
          workers: kind === 'scan' ? workers : null,
        })
      }
      await refresh()
    } catch (e) {
      error = e instanceof Error ? e.message : String(e)
    } finally {
      pendingKind = null
    }
  }

  async function cancelJob(id: number | null) {
    if (id === null || pendingKind !== null) return
    pendingKind = 'cancel'
    try {
      await api.cancel(id)
      await refresh()
    } catch (e) {
      error = e instanceof Error ? e.message : String(e)
    } finally {
      pendingKind = null
    }
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

  // retry: re-enqueue the job with ITS OWN scope and parameters (never the
  // current form — a failed single-movie encode must not become a
  // whole-library run); remove_bars routes through the ceremony
  async function retryJob(job: Job) {
    if (pendingKind !== null) return
    if (job.kind === 'dedupe') {
      await enqueue('dedupe')
      return
    }
    const payload = job.payload as Record<string, unknown>
    const paths = jobPaths(job)
    if (payload.remove_bars === true) {
      requestEncode(paths)
      return
    }
    await enqueue(job.kind, paths)
  }

  // per-movie actions (encode/plan a single title from its page)
  async function encodeOne(path: string) {
    if (pendingKind !== null) return
    requestEncode([path])
  }

  let dismissed = $state<Set<number>>(new Set())
  const runningJobs = $derived(
    history.filter((j) => (j.state === 'running' || j.state === 'queued') && j.id !== null && !dismissed.has(j.id))
  )
  const failedJobs = $derived(
    history.filter((j) => j.state === 'failed' && j.id !== null && !dismissed.has(j.id))
  )
  function dismissFailed(id: number | null) {
    if (id === null) return
    dismissed = new Set([...dismissed, id])
  }
  const runningJob = $derived(runningJobs.find((j) => j.state === 'running') ?? null)
  const runningProgress = $derived(
    (runningJob?.result as { progress?: { movie?: string; percent?: number; done?: number; total?: number } } | null)?.progress ?? null
  )

  const missingMovies = $derived(movies.filter((m) => m.missing))

  onMount(() => {
    poll()
    refreshDuplicates()
    return () => {
      if (pollTimer !== null) clearTimeout(pollTimer)
    }
  })

  const savedTotalGb = $derived(
    movies.reduce((sum, m) => {
      if (!m.encode || m.encode.saved_percent == null) return sum
      return sum + ((m.encode.input_size ?? 0) - (m.encode.output_size ?? 0))
    }, 0) / 1024 ** 3
  )
  const measuredCount = $derived(movies.filter((m) => m.encode?.vmaf != null).length)
</script>

<svelte:window onkeydown={handleShortcuts} />

<div class="console">
  <!-- ══ sidebar rail ══ -->
  <aside class="rail">
    <div class="brand">
      <h1>SHRINKRAY</h1>
      <p class="legend">LIBRARY CONSOLE</p>
    </div>
    <nav class="rail-nav" aria-label="Sections">
      <button class="rail-item" class:active={page === 'overview'} onclick={() => goto('overview')}>
        <span class="rail-label">Overview</span>
        {#if failedJobs.length > 0}<span class="rail-badge bad mono">{failedJobs.length}</span>{/if}
      </button>
      <button class="rail-item" class:active={page === 'library'} onclick={() => goto('library')}>
        <span class="rail-label">Library</span>
        <span class="rail-badge mono">{movies.length}</span>
      </button>
      <button class="rail-item" class:active={page === 'duplicates'} onclick={() => { goto('duplicates'); refreshDuplicates() }}>
        <span class="rail-label">Duplicates</span>
        {#if duplicates.length > 0}<span class="rail-badge warn mono">{duplicates.length}</span>{/if}
      </button>
      <button class="rail-item" class:active={page === 'jobs'} onclick={() => goto('jobs')}>
        <span class="rail-label">Jobs</span>
        {#if runningJobs.length > 0}<span class="rail-badge warn mono">{runningJobs.length}</span>{/if}
      </button>
      <button class="rail-item" class:active={page === 'config'} onclick={() => goto('config')}>
        <span class="rail-label">Config</span>
      </button>
    </nav>
    <div class="rail-foot">
      {#if removeBars}
        <span class="armed-chip mono" title="Remove-bars is armed for this session (set in Config). Encodes will crop bars after confirmation.">
          ⚠ BARS ARMED
        </span>
      {/if}
      <button class="theme-toggle legend" onclick={() => (theme = theme === 'dark' ? 'light' : 'dark')}
        title={theme === 'dark' ? 'Switch to the light working bench' : 'Switch to the dark cinema ground'}>
        {theme === 'dark' ? '◐ DARK' : '◑ LIGHT'}
      </button>
      <button class="legend watcher" class:on={status?.watcher === 'running'} onclick={toggleWatcher}>
        {status?.watcher === 'running' ? '● WATCHER' : '○ WATCHER'}
      </button>
    </div>
  </aside>

  <!-- ══ main column ══ -->
  <main class="main">
    {#if error}
      <div class="api-error mono" role="alert">
        {error}
        <button class="dismiss" onclick={() => (error = null)} aria-label="Dismiss error">✕</button>
      </div>
    {/if}

    {#if detailMovie}
      <MovieDetailPage
        movie={detailMovie}
        {targetVmaf}
        onclose={closeDetail}
        onencode={encodeOne}
        onplan={(p) => enqueue('plan', [p])}
        backTo={detailFrom}
      />
    {:else}
      <div class="pages" class:hidden={page !== 'overview'}>
        <OverviewPage
          {movies}
          {history}
          {status}
          {targetVmaf}
          {savedTotalGb}
          {measuredCount}
          duplicates={duplicates.length}
          duplicateWastedGb={duplicateWasted / 1024 ** 3}
          {missingMovies}
          {runningJobs}
          {failedJobs}
          {scanPaths}
          {outputDir}
          ondismiss={dismissFailed}
          ongoto={goto}
          onopenmovie={(m) => openDetail(m, 'overview')}
          oncancel={cancelJob}
          cancelling={pendingKind === 'cancel'}
        />
      </div>
      <div class="pages" class:hidden={page !== 'library'}>
        <LibraryPage
          {movies}
          {selectedPaths}
          {targetVmaf}
          {removeBars}
          scanning={kindQueued('scan')}
          pending={pendingKind !== null}
          runningMovie={runningProgress?.movie ?? null}
          runningPercent={runningProgress?.percent ?? null}
          plannedPaths={planEntries.filter((p) => p.state === 'encode').map((p) => p.movie)}
          onenqueue={enqueue}
          onopenmovie={(m) => openDetail(m, 'library')}
          ontoggle={(path, checked) => {
            const next = new Set(selectedPaths)
            if (checked) next.add(path)
            else next.delete(path)
            selectedPaths = next
          }}
          onselectall={(checked, paths) => {
            const next = new Set(selectedPaths)
            if (checked) paths.forEach((p) => next.add(p))
            else paths.forEach((p) => next.delete(p))
            selectedPaths = next
          }}
          onclear={() => (selectedPaths = new Set())}
        />
      </div>
      <div class="pages" class:hidden={page !== 'duplicates'}>
        <DuplicatesPage
          {duplicates}
          analyzing={kindQueued('dedupe')}
          pending={pendingKind !== null}
          onanalyze={() => enqueue('dedupe')}
          onrefresh={refreshDuplicates}
        />
      </div>
      <div class="pages" class:hidden={page !== 'jobs'}>
        <JobsPage {history} oncancel={cancelJob} onretry={retryJob} />
      </div>
      <div class="pages" class:hidden={page !== 'config'}>
        <ConfigPage
          {scanPaths}
          {outputDir}
          {removeBars}
          {targetVmaf}
          {workers}
          onpathsChange={(p) => { scanPaths = p; persist() }}
          onoutputChange={(o) => { outputDir = o; persist() }}
          onbarsChange={(armed) => { removeBars = armed }}
          onvmafChange={(v) => { targetVmaf = v; persist() }}
          onworkersChange={(w) => { workers = w; persist() }}
        />
      </div>
    {/if}
  </main>
</div>

<!-- ══ irreversible-work confirmation strip (global; echoes the ACTUAL targets) ══ -->
{#if pendingBarsTargets}
  <div class="bars-confirm" role="alertdialog" aria-label="Confirm irreversible bar removal" tabindex="-1" bind:this={barsConfirmEl}>
    <div class="bars-text">
      <strong>BARS WILL BE CROPPED PERMANENTLY.</strong>
      {#if pendingBarsTargets.length <= 3}
        {pendingBarsTargets.length} movie{pendingBarsTargets.length === 1 ? '' : 's'}: {pendingBarsTargets.map((t) => t.split(/[\\/]/).pop()).join(' · ')}
      {:else}
        {pendingBarsTargets.length} movies across {scanPaths.length} folder{scanPaths.length === 1 ? '' : 's'}
      {/if}
      — there is no undo.
    </div>
    <div class="bars-buttons">
      <button class="mono confirm" onclick={() => { const t = pendingBarsTargets!; pendingBarsTargets = null; enqueue('encode', t, true) }}>
        CROP {pendingBarsTargets.length} MOVIE{pendingBarsTargets.length === 1 ? '' : 'S'}
      </button>
      <button class="mono deny" onclick={() => (pendingBarsTargets = null)}>KEEP BARS</button>
    </div>
  </div>
{/if}

<style>
  .console {
    display: flex;
    min-height: 100vh;
    background: var(--ground);
    color: var(--ink);
    font-size: var(--fs-body);
  }

  /* ══ sidebar rail ══ */
  .rail {
    width: 190px;
    flex-shrink: 0;
    background: var(--rail);
    border-right: 1px solid var(--seam);
    display: flex;
    flex-direction: column;
    padding: 18px 12px;
    gap: 20px;
    position: sticky;
    top: 0;
    height: 100vh;
  }
  .brand h1 {
    font-size: 16px;
    font-weight: 700;
    letter-spacing: 3px;
  }
  .brand .legend { margin-top: 2px; }
  .rail-nav {
    display: flex;
    flex-direction: column;
    gap: 2px;
  }
  .rail-item {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    background: transparent;
    border: none;
    border-radius: var(--radius);
    color: var(--ink-dim);
    padding: 7px 12px;
    cursor: pointer;
    font-size: 13px;
    font-weight: 500;
    text-align: left;
  }
  .rail-item:hover { color: var(--ink); background: var(--panel); }
  .rail-item.active {
    color: var(--ink);
    background: var(--panel-active);
  }
  .rail-badge {
    font-size: 10.5px;
    color: var(--ink-dim);
  }
  .rail-badge.warn { color: var(--amber); }
  .rail-badge.bad { color: var(--red); }
  .rail-foot {
    margin-top: auto;
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  .theme-toggle, .watcher {
    background: transparent;
    border: 1px solid var(--seam);
    border-radius: var(--radius);
    color: var(--ink-dim);
    padding: 5px 10px;
    cursor: pointer;
    text-align: left;
  }
  .armed-chip {
    color: var(--red);
    border: 1px solid var(--red);
    border-radius: var(--radius);
    padding: 5px 10px;
    font-size: 10px;
    letter-spacing: 1px;
    text-align: center;
    background: repeating-linear-gradient(45deg, rgba(242, 114, 101, 0.1) 0 4px, transparent 4px 8px), var(--panel);
  }
  .theme-toggle:hover { color: var(--ink); border-color: var(--ink-dim); }
  .watcher.on { color: var(--teal-ink); border-color: var(--teal); }
  .watcher:not(.on):hover { color: var(--ink); }

  /* ══ main column ══ */
  .main {
    flex: 1;
    min-width: 0;
    padding: 20px 26px 40px;
    display: flex;
    flex-direction: column;
    gap: 14px;
    max-width: 1500px;
  }
  /* pages stay mounted (state survives tab switches); only one visible */
  .pages { display: flex; flex-direction: column; gap: 14px; min-height: 0; }
  .pages.hidden { display: none; }

  .api-error {
    border: 1px solid var(--red);
    color: var(--red);
    background: repeating-linear-gradient(45deg, rgba(242, 114, 101, 0.06) 0 4px, transparent 4px 8px), var(--panel);
    padding: 8px 14px;
    border-radius: var(--radius);
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

  /* ══ confirmation strip ══ */
  .bars-confirm {
    position: fixed;
    left: 190px;
    right: 0;
    bottom: 0;
    z-index: 60;
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 14px;
    flex-wrap: wrap;
    border-top: 1px solid var(--red);
    background: repeating-linear-gradient(45deg, rgba(242, 114, 101, 0.08) 0 4px, transparent 4px 8px), var(--panel);
    padding: 12px 26px;
  }
  .bars-text { font-size: var(--fs-data); }
  .bars-buttons { display: flex; gap: 8px; }
  .bars-buttons button {
    background: transparent;
    border: 1px solid var(--seam);
    color: var(--ink);
    padding: 5px 14px;
    cursor: pointer;
    border-radius: var(--radius);
    font-size: var(--fs-legend);
    letter-spacing: var(--track-legend);
  }
  .bars-buttons .confirm { border-color: var(--red); color: var(--red); }
  .bars-buttons .confirm:hover { background: var(--red); color: var(--ground); }
  .bars-buttons .deny:hover { border-color: var(--ink-dim); }

  @media (max-width: 900px) {
    .console { flex-direction: column; }
    .rail {
      width: 100%;
      height: auto;
      position: static;
      flex-direction: row;
      align-items: center;
      padding: 10px 14px;
      gap: 12px;
    }
    .brand .legend { display: none; }
    .rail-nav { flex-direction: row; flex: 1; overflow-x: auto; }
    .rail-item { padding: 6px 10px; white-space: nowrap; }
    .rail-item.active { box-shadow: inset 0 -2px 0 var(--teal); }
    .rail-foot { margin-top: 0; flex-direction: row; }
    .main { padding: 14px; }
    .bars-confirm { left: 0; }
  }
</style>
