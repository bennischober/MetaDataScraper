<script lang="ts">
  import { fmtGb, fmtSaved, type Movie } from '../api'
  import PosterCard from './PosterCard.svelte'

  let {
    movies,
    selectedPaths,
    targetVmaf,
    removeBars,
    scanning = false,
    pending = false,
    runningMovie = null,
    runningPercent = null,
    plannedPaths = [],
    onenqueue,
    onopenmovie,
    ontoggle,
    onselectall,
    onclear,
  }: {
    movies: Movie[]
    selectedPaths: Set<string>
    targetVmaf: number
    removeBars: boolean
    scanning?: boolean
    pending?: boolean
    runningMovie?: string | null
    runningPercent?: number | null
    plannedPaths?: string[]
    onenqueue: (kind: 'scan' | 'plan' | 'encode', paths?: string[]) => void
    onopenmovie: (movie: Movie) => void
    ontoggle: (path: string, checked: boolean) => void
    onselectall: (checked: boolean, paths: string[]) => void
    onclear: () => void
  } = $props()

  // ── search + category (a select, not an exploding chip row) ──
  let query = $state('')
  let category = $state<string | null>(null)
  let view = $state<'grid' | 'table'>('grid')
  let hideMissing = $state(false)

  const categories = $derived([...new Set(movies.map((m) => m.category))].sort())

  const filtered = $derived.by(() => {
    const q = query.trim().toLowerCase()
    return movies.filter((m) => {
      if (category && m.category !== category) return false
      if (hideMissing && m.missing) return false
      if (q && !m.name.toLowerCase().includes(q)) return false
      return true
    })
  })

  const selectedCount = $derived(filtered.filter((m) => selectedPaths.has(m.path)).length)
  const selectedMovies = $derived(filtered.filter((m) => selectedPaths.has(m.path)))
  const selectedBase = $derived(
    selectedMovies.reduce((sum, m) => sum + (m.encode?.output_size ?? m.size ?? 0), 0)
  )
  const selectedEstimate = $derived(
    selectedMovies.filter((m) => !m.encode).reduce((sum, m) => sum + (m.size ?? 0) * 0.45, 0)
  )

  function decisionOf(movie: Movie): { label: string; cls: string } {
    const preset = movie.encode?.preset
    if (movie.missing) return { label: 'MISSING', cls: 'missing' }
    if (preset?.startsWith('skipped')) return { label: 'SKIPPED', cls: 'skip' }
    if (preset) return { label: 'ENCODED', cls: 'done' }
    if (plannedPaths.includes(movie.path)) return { label: 'PLANNED', cls: 'planned' }
    return { label: 'UNTOUCHED', cls: 'untouched' }
  }
</script>

<div class="page page-in">
  <header class="page-head">
    <h2>Library</h2>
    <p class="legend">{filtered.length} OF {movies.length} TITLES · TARGET VMAF {targetVmaf}</p>
  </header>

  <!-- ══ toolbar: search + category + view toggle + scan ══ -->
  <div class="toolbar">
    <input
      class="search mono"
      type="search"
      placeholder="search titles…"
      bind:value={query}
      aria-label="Search titles"
    />
    <select class="cat mono" bind:value={category} aria-label="Filter by category">
      <option value={null}>All categories ({movies.length})</option>
      {#each categories as cat (cat)}
        <option value={cat}>{cat} ({movies.filter((m) => m.category === cat).length})</option>
      {/each}
    </select>
    {#if movies.some((m) => m.missing)}
      <label class="missing-toggle mono dim">
        <input type="checkbox" bind:checked={hideMissing} />
        hide missing
      </label>
    {/if}
    <span class="spacer"></span>
    <div class="view-toggle" role="group" aria-label="View">
      <button class="vt" class:active={view === 'grid'} onclick={() => (view = 'grid')} aria-pressed={view === 'grid'}>GRID</button>
      <button class="vt" class:active={view === 'table'} onclick={() => (view = 'table')} aria-pressed={view === 'table'}>TABLE</button>
    </div>
    <button class="act scan" onclick={() => onenqueue('scan')} disabled={scanning || pending}>
      {scanning ? 'SCANNING…' : 'RESCAN ALL'}
    </button>
  </div>

  <!-- ══ bulk bar (only when a selection exists) ══ -->
  {#if selectedCount > 0}
    <div class="bulk-bar leader-in">
      <span class="mono sel-count">
        <input
          type="checkbox"
          checked={selectedCount === filtered.length}
          onchange={(e) => onselectall(e.currentTarget.checked, filtered.map((m) => m.path))}
          aria-label="Select all filtered"
        />
        {selectedCount} SELECTED
      </span>
      <span class="leader-note mono">→ EST. −{(selectedEstimate / 1024 ** 3).toFixed(1)} GB · {fmtGb(selectedBase)} BASE</span>
      <span class="spacer"></span>
      <button class="act" onclick={() => onenqueue('plan', selectedMovies.map((m) => m.path))} disabled={selectedCount === 0 || pending}>
        PLAN SELECTED
      </button>
      <button class="act primary" onclick={() => onenqueue('encode', selectedMovies.map((m) => m.path))} disabled={selectedCount === 0 || pending}>
        ENCODE SELECTED
      </button>
      <button class="act quiet" onclick={onclear}>CLEAR</button>
    </div>
  {:else}
    <div class="bulk-bar idle">
      <span class="mono dim">{filtered.length} titles — tick checkboxes for bulk work, or</span>
      <span class="spacer"></span>
      <button class="act" onclick={() => onenqueue('plan')} disabled={pending}>PLAN ALL</button>
      <button class="act primary" onclick={() => onenqueue('encode')} disabled={pending}>ENCODE ALL</button>
    </div>
  {/if}

  <!-- ══ grid view ══ -->
  {#if view === 'grid'}
    <div class="grid">
      {#each filtered as movie (movie.path)}
        {@const decision = decisionOf(movie)}
        <PosterCard
          {movie}
          {decision}
          selected={selectedPaths.has(movie.path)}
          inWork={runningMovie != null && movie.path === runningMovie}
          inWorkPercent={runningPercent}
          planned={plannedPaths.includes(movie.path)}
          onopen={() => onopenmovie(movie)}
          ontoggle={(c) => ontoggle(movie.path, c)}
        />
      {:else}
        <div class="empty dim">no titles match — adjust the search or run a scan</div>
      {/each}
    </div>
  {:else}
    <!-- ══ table view ══ -->
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th class="chk">
              <input
                type="checkbox"
                checked={filtered.length > 0 && filtered.every((m) => selectedPaths.has(m.path))}
                onchange={(e) => onselectall(e.currentTarget.checked, filtered.map((m) => m.path))}
                aria-label="Select all filtered"
              />
            </th>
            <th class="legend">Title</th>
            <th class="legend">Category</th>
            <th class="legend num">Size</th>
            <th class="legend">Decision</th>
            <th class="legend num">CRF</th>
            <th class="legend num">VMAF</th>
            <th class="legend num">Saved</th>
          </tr>
        </thead>
        <tbody>
          {#each filtered as movie (movie.path)}
            {@const decision = decisionOf(movie)}
            <tr
              class:row-selected={selectedPaths.has(movie.path)}
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
              <td>
                <button class="name-btn" onclick={() => onopenmovie(movie)}>
                  {movie.name}
                  {#if movie.hdr}<span class="mono hdr">HDR</span>{/if}
                </button>
              </td>
              <td class="dim">{movie.category}</td>
              <td class="mono num">{fmtGb(movie.size)}</td>
              <td>
                {#if runningMovie != null && movie.path === runningMovie}
                  <span class="chip progress"><span class="swatch"></span>IN WORK {runningPercent != null ? `${runningPercent}%` : ''}</span>
                {:else}
                  <span class="chip {decision.cls}"><span class="swatch"></span>{decision.label}</span>
                {/if}
              </td>
              <td class="mono num">{movie.encode?.crf ?? '—'}</td>
              <td class="mono num measured">{movie.encode?.vmaf?.toFixed(2) ?? '—'}</td>
              <td class="mono num" class:ok={movie.encode?.saved_percent != null && movie.encode.saved_percent > 0}>
                {movie.encode ? fmtSaved(movie.encode.saved_percent) : '—'}
              </td>
            </tr>
          {:else}
            <tr><td colspan="8" class="empty dim">no titles match — adjust the search or run a scan</td></tr>
          {/each}
        </tbody>
      </table>
    </div>
  {/if}
</div>

<style>
  .page { display: flex; flex-direction: column; gap: 12px; min-height: 0; }
  .page-head h2 { font-size: var(--fs-page); font-weight: 700; }
  .page-head .legend { margin-top: 2px; }
  .dim { color: var(--ink-dim); }
  .warn { color: var(--amber); }
  .bad { color: var(--red); }
  .ok { color: var(--teal-ink); }

  .toolbar {
    display: flex;
    gap: 8px;
    align-items: center;
    flex-wrap: wrap;
  }
  .search {
    flex: 0 1 260px;
    background: var(--panel);
    border: 1px solid var(--seam);
    border-radius: var(--radius);
    color: var(--ink);
    padding: 6px 12px;
  }
  .search:focus { border-color: var(--teal); outline: none; }
  .cat {
    background: var(--panel);
    border: 1px solid var(--seam);
    border-radius: var(--radius);
    color: var(--ink);
    padding: 6px 10px;
    max-width: 260px;
  }
  .missing-toggle { display: inline-flex; align-items: center; gap: 6px; cursor: pointer; }
  .missing-toggle input { accent-color: var(--red); }
  .view-toggle { display: inline-flex; border: 1px solid var(--seam); border-radius: var(--radius); overflow: hidden; }
  .vt {
    background: var(--panel);
    border: none;
    color: var(--ink-dim);
    padding: 6px 14px;
    cursor: pointer;
    font-size: var(--fs-legend);
    letter-spacing: var(--track-legend);
  }
  .vt.active { background: var(--panel-active); color: var(--teal-ink); }
  .act.scan {
    background: var(--panel-raised);
    border: 1px solid var(--ink);
    border-radius: var(--radius);
    color: var(--ink);
    padding: 6px 14px;
    cursor: pointer;
    font-size: var(--fs-legend);
    letter-spacing: var(--track-legend);
    text-transform: uppercase;
  }
  .act.scan:hover:not(:disabled) { border-color: var(--teal); color: var(--teal-ink); }
  .act.scan:disabled { opacity: 0.5; cursor: default; }
  .spacer { flex: 1; }

  .bulk-bar {
    display: flex;
    align-items: center;
    gap: 12px;
    flex-wrap: wrap;
    border: 1px solid var(--teal);
    border-radius: var(--radius);
    background: var(--panel-active);
    padding: 8px 14px;
  }
  .bulk-bar.idle { border-color: var(--seam); background: var(--panel); }
  .sel-count { display: inline-flex; align-items: center; gap: 8px; font-weight: 600; }
  .sel-count input { accent-color: var(--teal); }
  .leader-note { color: var(--teal-ink); }
  .spacer { flex: 1; }

  .act {
    background: var(--panel-raised);
    border: 1px solid var(--ink);
    border-radius: var(--radius);
    color: var(--ink);
    padding: 5px 14px;
    cursor: pointer;
    font-size: var(--fs-legend);
    letter-spacing: var(--track-legend);
    text-transform: uppercase;
  }
  .act:hover:not(:disabled) { border-color: var(--teal); color: var(--teal-ink); }
  .act.primary { background: var(--teal); border-color: var(--teal); color: var(--ground); }
  .act.primary:hover:not(:disabled) { filter: brightness(1.08); color: var(--ground); }
  .act.quiet { border-color: var(--seam); color: var(--ink-dim); }
  .act:disabled { opacity: 0.4; cursor: default; }

  /* grid */
  .grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
    gap: 12px;
  }
  .empty { padding: 40px; text-align: center; grid-column: 1 / -1; }

  /* table */
  .table-wrap {
    flex: 1;
    min-height: 0;
    border: 1px solid var(--seam);
    border-radius: var(--radius);
    background: var(--panel);
    overflow: auto;
  }
  table { width: 100%; border-collapse: collapse; }
  thead tr { border-bottom: 1px solid var(--seam); }
  th {
    padding: 8px 12px;
    text-align: left;
    position: sticky;
    top: 0;
    background: var(--panel);
    z-index: 1;
  }
  th.num { text-align: right; }
  td {
    padding: 7px 12px;
    border-bottom: 1px solid var(--row-line);
    white-space: nowrap;
  }
  td.num { text-align: right; }
  td.chk { text-align: center; width: 34px; }
  td.chk input { accent-color: var(--teal); }
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
  tr.in-work td { background: var(--hatch-progress); }
  .measured { color: var(--teal-ink); font-weight: 600; }
  .hdr {
    margin-left: 8px;
    color: var(--amber);
    font-size: 10px;
    border: 1px solid var(--seam);
    padding: 0 4px;
    border-radius: 2px;
  }
</style>
