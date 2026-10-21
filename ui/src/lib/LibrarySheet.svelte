<script lang="ts">
  import { api, fmtGb, fmtSaved, jobPaths, type Movie, type Job } from './api'

  let {
    movies,
    selectedPaths,
    runningMovie = null,
    runningPercent = null,
    plannedPaths = [],
    ontoggle,
    onselectall,
    onencode,
    targetVmaf = 95,
  }: {
    movies: Movie[]
    selectedPaths: Set<string>
    runningMovie?: string | null
    runningPercent?: number | null
    plannedPaths?: string[]
    ontoggle: (path: string, checked: boolean) => void
    onselectall: (checked: boolean, paths: string[]) => void
    onencode: (path: string) => void
    targetVmaf?: number
  } = $props()

  // ── filter + sort ──
  let categoryFilter = $state<string | null>(null)
  let sortKey = $state<'name' | 'size' | 'saved' | 'vmaf' | 'category'>('category')
  let sortAsc = $state(true)

  const categories = $derived([...new Set(movies.map((m) => m.category))])
  const filtered = $derived(
    categoryFilter ? movies.filter((m) => m.category === categoryFilter) : movies
  )
  const sorted = $derived.by(() => {
    const list = [...filtered]
    const dir = sortAsc ? 1 : -1
    list.sort((a, b) => {
      switch (sortKey) {
        case 'name':
          return a.name.localeCompare(b.name) * dir
        case 'category':
          return a.category.localeCompare(b.category) * dir
        case 'size':
          return ((a.size ?? 0) - (b.size ?? 0)) * dir
        case 'saved':
          return ((a.encode?.saved_percent ?? -1) - (b.encode?.saved_percent ?? -1)) * dir
        case 'vmaf':
          return ((a.encode?.vmaf ?? -1) - (b.encode?.vmaf ?? -1)) * dir
      }
    })
    return list
  })

  function toggleSort(key: typeof sortKey) {
    if (sortKey === key) {
      sortAsc = !sortAsc
    } else {
      sortKey = key
      sortAsc = true
    }
  }
  function ariaSort(key: string): 'ascending' | 'descending' | 'none' {
    if (sortKey !== key) return 'none'
    return sortAsc ? 'ascending' : 'descending'
  }

  function sortMark(key: string): string {
    if (sortKey !== key) return ''
    return sortAsc ? '▲' : '▼'
  }

  function decisionOf(movie: Movie): { label: string; cls: string } {
    const preset = movie.encode?.preset
    if (preset?.startsWith('skipped')) return { label: 'SKIPPED', cls: 'skip' }
    if (preset) return { label: 'ENCODED', cls: 'done' }
    if (plannedPaths.includes(movie.path)) return { label: 'PLANNED', cls: 'planned' }
    return { label: 'UNTOUCHED', cls: 'untouched' }
  }

  // ── detail callout (level 2: provenance; level 3: raw measurement) ──
  let selected = $state<Movie | null>(null)
  let selectedHistory = $state<Job[]>([])
  let loadingHistory = $state(false)
  let rawOpen = $state(false)
  let calloutEl: HTMLDivElement | null = $state(null)
  let opener: HTMLButtonElement | null = null

  async function openDetail(movie: Movie, ev: MouseEvent | null) {
    opener = ev?.currentTarget as HTMLButtonElement | null
    selected = movie
    rawOpen = false
    loadingHistory = true
    selectedHistory = []
    try {
      // jobs touching this movie: match against all payload paths
      // (multi-folder batches) — jobPaths() exists for exactly this
      const all = await api.jobs(100)
      selectedHistory = all.filter((j) => jobPaths(j).includes(movie.path))
    } finally {
      loadingHistory = false
    }
    // move focus into the callout (keyboard operators stay oriented)
    queueMicrotask(() => calloutEl?.focus())
  }

  function closeDetail() {
    selected = null
    opener?.focus()
    opener = null
  }

  function handleKeydown(e: KeyboardEvent) {
    if (e.key === 'Escape') {
      closeDetail()
      return
    }
    if (e.key === 'Tab' && calloutEl) {
      // focus trap: cycle within the callout while it is open
      const focusables = calloutEl.querySelectorAll<HTMLElement>(
        'button, input, [tabindex]:not([tabindex="-1"])'
      )
      if (focusables.length === 0) return
      const first = focusables[0]
      const last = focusables[focusables.length - 1]
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault()
        last.focus()
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault()
        first.focus()
      }
    }
  }

  async function movieAction(kind: 'plan' | 'encode') {
    if (!selected) return
    try {
      await api.enqueue({
        kind,
        path: selected.path,
        output_dir: kind === 'encode' ? localStorage.getItem('ms.out') || undefined : undefined,
        quality: true,
        target_vmaf: Math.min(100, Math.max(70, Number(targetVmaf) || 95)),
      })
    } finally {
      /* the job list refreshes on the next poll */
    }
  }

  function revealInExplorer() {
    if (!selected) return
    const dir = selected.path.replace(/[\\/][^\\/]+$/, '')
    window.open(`file:///${dir.replace(/\\/g, '/')}`)
  }
</script>

<div class="block">
  <!-- filter row -->
  <div class="toolbar">
    <div class="chips" role="group" aria-label="Filter by category">
      <button
        class="cat-chip mono"
        class:active={categoryFilter === null}
        aria-pressed={categoryFilter === null}
        onclick={() => (categoryFilter = null)}
      >
        ALL {movies.length}
      </button>
      {#each categories as cat (cat)}
        <button
          class="cat-chip mono"
          class:active={categoryFilter === cat}
          aria-pressed={categoryFilter === cat}
          onclick={() => (categoryFilter = categoryFilter === cat ? null : cat)}
        >
          {cat} {movies.filter((m) => m.category === cat).length}
        </button>
      {/each}
    </div>
  </div>

  <div class="panel-frame corner-ticks">
    <div class="table-wrap sheet-grid">
    <table>
      <thead>
        <tr>
          <th class="chk">
            <input
              type="checkbox"
              checked={sorted.length > 0 && sorted.every((m) => selectedPaths.has(m.path))}
              onchange={(e) => onselectall(e.currentTarget.checked, sorted.map((m) => m.path))}
              aria-label="Select all filtered movies"
            />
          </th>
          <th aria-sort={ariaSort('name')}>
            <button class="sort legend" onclick={() => toggleSort('name')}>Movie {sortMark('name')}</button>
          </th>
          <th aria-sort={ariaSort('category')}>
            <button class="sort legend" onclick={() => toggleSort('category')}>Category {sortMark('category')}</button>
          </th>
          <th class="legend">Resolution</th>
          <th class="num" aria-sort={ariaSort('size')}>
            <button class="sort legend" onclick={() => toggleSort('size')}>Size {sortMark('size')}</button>
          </th>
          <th class="legend">Decision</th>
          <th class="legend num">CRF</th>
          <th class="num" aria-sort={ariaSort('vmaf')}>
            <button class="sort legend" onclick={() => toggleSort('vmaf')}>VMAF {sortMark('vmaf')}</button>
          </th>
          <th class="num" aria-sort={ariaSort('saved')}>
            <button class="sort legend" onclick={() => toggleSort('saved')}>Saved {sortMark('saved')}</button>
          </th>
        </tr>
      </thead>
      <tbody>
        {#each sorted as movie (movie.path)}
          {@const decision = decisionOf(movie)}
          <tr
            class:row-selected={selectedPaths.has(movie.path)}
            class:detail-open={selected?.path === movie.path}
            class:in-work={runningMovie != null && movie.path === runningMovie}
          >
            <td class="chk">
              <input
                type="checkbox"
                checked={selectedPaths.has(movie.path)}
                onchange={(e) => ontoggle(movie.path, e.currentTarget.checked)}
                aria-label="Select {movie.name}"
              />
            </td>
            <td class="name-cell">
              <button class="name-btn" onclick={(e) => openDetail(movie, e)}>
                {movie.name}
                {#if movie.hdr}<span class="mono hdr-tag">HDR</span>{/if}
                {#if movie.missing}<span class="mono missing-tag">MISSING</span>{/if}
              </button>
            </td>
            <td class="dim">{movie.category}</td>
            <td class="mono">{movie.resolution ? `${movie.resolution[0]}×${movie.resolution[1]}` : '—'}</td>
            <td class="mono num">{fmtGb(movie.size)}</td>
            <td>
              {#if runningMovie != null && movie.path === runningMovie}
                <span class="chip progress"><span class="swatch"></span>IN WORK {runningPercent != null ? `${runningPercent}%` : ''}</span>
              {:else}
                <span class="chip {decision.cls}"><span class="swatch"></span>{decision.label}</span>
              {/if}
            </td>
            <td class="mono num">{movie.encode?.crf ?? '—'}</td>
            <td class="mono num measured" class:teal={movie.encode?.vmaf != null}>
              {movie.encode?.vmaf?.toFixed(2) ?? '—'}
            </td>
            <td class="mono num" class:ok={movie.encode?.saved_percent != null && movie.encode.saved_percent > 0}>
              {movie.encode ? fmtSaved(movie.encode.saved_percent) : '—'}
            </td>
          </tr>
        {:else}
          <tr><td colspan="9" class="empty dim">No movies scanned yet — the sheet awaits its survey.</td></tr>
        {/each}
      </tbody>
    </table>
    </div>
  </div>
</div>

<!-- detail callout: a margin annotation, not a modal -->
{#if selected}
  <div class="overlay" role="presentation" onclick={(e) => e.target === e.currentTarget && closeDetail()}>
    <div
      class="callout"
      role="dialog"
      aria-modal="true"
      aria-label="Details for {selected.name}"
      tabindex="-1"
      bind:this={calloutEl}
      onkeydown={handleKeydown}
    >
      <div class="co-head">
        <span class="legend">MOVIE — PROVENANCE</span>
        <button class="close" onclick={closeDetail} aria-label="Close details">✕</button>
      </div>
      <h2 class="co-title">{selected.name}</h2>
      <div class="path mono dim">{selected.path}</div>

      <div class="facts">
        <div class="fact"><span class="legend">CATEGORY</span><span>{selected.category}</span></div>
        <div class="fact"><span class="legend">DURATION</span><span class="mono">{selected.duration ?? '—'}</span></div>
        <div class="fact"><span class="legend">CODEC</span><span class="mono">{selected.codec ?? '—'}</span></div>
        <div class="fact"><span class="legend">RESOLUTION</span><span class="mono">{selected.resolution?.join('×') ?? '—'}</span></div>
        <div class="fact"><span class="legend">SIZE</span><span class="mono">{fmtGb(selected.size)}</span></div>
        <div class="fact"><span class="legend">HDR</span><span class="mono">{selected.hdr ? 'yes' : 'no'}</span></div>
      </div>

      {#if selected.encode}
        <div class="raw-box">
          <button class="raw-toggle" onclick={() => (rawOpen = !rawOpen)} aria-expanded={rawOpen}>
            <span class="legend">LATEST ENCODE — MEASUREMENT {rawOpen ? '▲' : '▼'}</span>
          </button>
          {#if rawOpen}
            <div class="facts">
              <div class="fact"><span class="legend">PRESET</span><span class="mono">{selected.encode.preset}</span></div>
              <div class="fact"><span class="legend">CRF</span><span class="mono">{selected.encode.crf ?? '—'}</span></div>
              <div class="fact"><span class="legend">VMAF</span><span class="mono">{selected.encode.vmaf?.toFixed(2) ?? '—'}</span></div>
              <div class="fact"><span class="legend">NEW SIZE</span><span class="mono">{fmtGb(selected.encode.output_size)}</span></div>
              <div class="fact"><span class="legend">SAVED</span><span class="mono">{fmtSaved(selected.encode.saved_percent)}</span></div>
            </div>
          {/if}
        </div>
      {/if}

      <div class="co-actions">
        <button class="act primary mono" onclick={() => movieAction('plan')}>PLAN</button>
        <button class="act primary mono" onclick={() => movieAction('encode')}>ENCODE</button>
        <button class="act mono" onclick={revealInExplorer}>FOLDER</button>
      </div>

      <div class="legend hist-head">JOB HISTORY</div>
      {#if loadingHistory}
        <div class="dim mono">loading…</div>
      {:else if selectedHistory.length === 0}
        <div class="dim mono">no jobs for this movie yet</div>
      {:else}
        {#each selectedHistory as job (job.id)}
          <div class="hist-row mono">
            <span>#{job.id}</span>
            <span>{job.kind}</span>
            <span class={job.state === 'done' ? 'ok' : job.state === 'failed' ? 'bad' : 'dim'}>
              {job.state}
            </span>
            <span class="dim err">{job.error ?? ''}</span>
          </div>
        {/each}
      {/if}
    </div>
  </div>
{/if}

<style>
  .block {
    display: flex;
    flex-direction: column;
    gap: 8px;
    flex: 1;
    min-height: 0;
  }
  .toolbar { display: flex; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
  .chips { display: flex; gap: 6px; flex-wrap: wrap; }
  .cat-chip {
    background: var(--panel);
    border: 1px solid var(--seam);
    color: var(--ink-dim);
    padding: 3px 10px;
    cursor: pointer;
    font-size: var(--fs-data);
  }
  .cat-chip.active {
    color: var(--teal-ink);
    border-color: var(--teal);
    background: var(--panel-active);
  }
  .table-wrap {
    flex: 1;
    border: 1px solid var(--ink);
    background: var(--panel);
    overflow: auto;
  }
  .panel-frame {
    flex: 1;
    min-height: 0;
    display: flex;
    flex-direction: column;
  }
  table { width: 100%; border-collapse: collapse; }
  thead tr { border-bottom: 2px solid var(--ink); }
  th {
    padding: 8px 12px;
    text-align: left;
    position: sticky;
    top: 0;
    background: var(--panel);
    z-index: 1;
  }
  th.num { text-align: right; }
  th.chk { width: 34px; }
  .sort {
    background: transparent;
    border: none;
    color: inherit;
    font: inherit;
    letter-spacing: inherit;
    text-transform: inherit;
    cursor: pointer;
    padding: 0;
    width: 100%;
    text-align: inherit;
  }
  .sort:hover { color: var(--ink); }
  td {
    padding: 7px 12px;
    border-bottom: 1px solid var(--row-line);
    white-space: nowrap;
  }
  td.num { text-align: right; }
  td.chk { text-align: center; }
  td.chk input { accent-color: var(--teal); width: 13px; height: 13px; cursor: pointer; }
  .name-cell { max-width: 340px; }
  .name-btn {
    background: transparent;
    border: none;
    color: inherit;
    font: inherit;
    font-weight: 600;
    cursor: pointer;
    padding: 0;
    max-width: 340px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .name-btn:hover { color: var(--teal-ink); text-decoration: underline; }
  tr.row-selected td { background: var(--hatch-select); }
  tr.row-selected td.chk { background: var(--panel-active); }
  tr.detail-open td { background: var(--panel-active); }
  tr.in-work td { background: var(--hatch-progress); }
  tr.in-work td.chk { background: var(--panel); }
  .dim { color: var(--ink-dim); }
  .measured.teal { color: var(--teal-ink); font-weight: 600; }
  .ok { color: var(--teal-ink); }
  .hdr-tag {
    margin-left: 8px;
    color: var(--amber);
    font-size: 10px;
    border: 1px solid var(--seam);
    padding: 0 4px;
  }
  .missing-tag {
    margin-left: 8px;
    color: var(--red);
    font-size: 10px;
    border: 1px solid var(--seam);
    padding: 0 4px;
  }
  .empty { text-align: center; padding: 24px; }

  /* ── the callout: margin annotation ── */
  .overlay {
    position: fixed;
    inset: 0;
    background: rgba(30, 35, 40, 0.45);
    display: flex;
    justify-content: flex-end;
    z-index: 40;
  }
  .callout {
    width: min(480px, 94vw);
    background: var(--panel);
    border-left: 2px solid var(--teal);
    padding: 16px 22px;
    overflow: auto;
    outline: none;
  }
  .co-head { display: flex; justify-content: space-between; align-items: center; }
  .co-title { font-size: var(--fs-title); font-weight: 700; margin-top: 6px; }
  .path { margin: 4px 0 14px; word-break: break-all; font-size: 11px; }
  .close {
    background: transparent;
    border: 1px solid var(--seam);
    color: var(--ink-dim);
    cursor: pointer;
    padding: 3px 9px;
  }
  .close:hover { color: var(--ink); border-color: var(--ink-dim); }
  .facts { display: flex; flex-direction: column; gap: 4px; margin-bottom: 14px; }
  .fact { display: flex; justify-content: space-between; gap: 12px; }
  .raw-box { border: 1px solid var(--seam); padding: 10px 14px; margin-bottom: 14px; }
  .raw-toggle {
    background: transparent;
    border: none;
    padding: 0;
    cursor: pointer;
    width: 100%;
    text-align: left;
    color: inherit;
    font: inherit;
  }
  .co-actions { display: flex; gap: 8px; margin-bottom: 16px; }
  .co-actions .act {
    background: var(--panel);
    border: 1px solid var(--ink);
    color: var(--ink);
    padding: 6px 16px;
    cursor: pointer;
    font-size: var(--fs-legend);
    letter-spacing: var(--track-legend);
    text-transform: uppercase;
  }
  .co-actions .act:hover { background: var(--panel-active); border-color: var(--teal-ink); color: var(--teal-ink); }
  .co-actions .act.primary { background: var(--teal); border-color: var(--teal); color: #fff; }
  .co-actions .act.primary:hover { background: var(--teal-ink); color: #fff; }
  .hist-head { display: block; margin: 6px 0; }
  .hist-row {
    display: flex;
    gap: 14px;
    padding: 4px 0;
    border-bottom: 1px solid var(--row-line);
    font-size: var(--fs-data);
  }
  .hist-row:last-child { border-bottom: none; }
  .hist-row .err { flex: 1; overflow: hidden; text-overflow: ellipsis; }
  .ok { color: var(--teal-ink); }
  .bad { color: var(--red); }
</style>
