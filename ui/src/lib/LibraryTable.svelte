<script lang="ts">
  import { api, type Movie, type Job } from './api'
  import { fmtGb, fmtSaved } from './api'

  let { movies, onsettings, onduplicates, duplicateCount = 0 }: {
    movies: Movie[]
    onsettings: () => void
    onduplicates: () => void
    duplicateCount?: number
  } = $props()

  // ── filtering + sorting state ──
  let categoryFilter = $state<string | null>(null)
  let sortKey = $state<'name' | 'size' | 'saved' | 'vmaf' | 'category'>('category')
  let sortAsc = $state(true)
  let selected = $state<Movie | null>(null)
  let selectedHistory = $state<Job[]>([])
  let loadingHistory = $state(false)

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

  function sortMark(key: string): string {
    if (sortKey !== key) return ''
    return sortAsc ? '▲' : '▼'
  }

  function decisionOf(movie: Movie): { label: string; cls: string } {
    const preset = movie.encode?.preset
    if (preset?.startsWith('skipped')) return { label: 'SKIPPED', cls: 'skip' }
    if (preset) return { label: 'ENCODED', cls: 'done' }
    return { label: 'UNTOUCHED', cls: 'untouched' }
  }

  function savedOf(movie: Movie): number {
    return movie.encode?.saved_percent ?? -1
  }

  async function openDetail(movie: Movie) {
    selected = movie
    loadingHistory = true
    selectedHistory = []
    try {
      // job history touching this movie's path (payload.path matches scans
      // and encodes of the whole folder it lives in)
      const all = await api.jobs(100)
      selectedHistory = all.filter(
        (j) => (j.payload as { path?: string }).path === movie.path
      )
    } finally {
      loadingHistory = false
    }
  }
</script>

<div class="block">
  <!-- filter + settings row -->
  <div class="toolbar">
    <div class="chips">
      <button
        class="cat-chip mono"
        class:active={categoryFilter === null}
        onclick={() => (categoryFilter = null)}
      >
        ALL {movies.length}
      </button>
      {#each categories as cat (cat)}
        <button
          class="cat-chip mono"
          class:active={categoryFilter === cat}
          onclick={() => (categoryFilter = categoryFilter === cat ? null : cat)}
        >
          {cat} {movies.filter((m) => m.category === cat).length}
        </button>
      {/each}
    </div>
    <div class="right">
      {#if duplicateCount > 0}
        <button class="dup-btn legend" onclick={onduplicates}>
          DUPLICATES {duplicateCount}
        </button>
      {/if}
      <button class="settings-btn legend" onclick={onsettings}>CONFIG ⚙</button>
    </div>
  </div>

  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th><button class="sort legend" onclick={() => toggleSort('name')}>Movie {sortMark('name')}</button></th>
          <th><button class="sort legend" onclick={() => toggleSort('category')}>Category {sortMark('category')}</button></th>
          <th class="legend">Resolution</th>
          <th class="num"><button class="sort legend" onclick={() => toggleSort('size')}>Size {sortMark('size')}</button></th>
          <th class="legend">Decision</th>
          <th class="legend num">CRF</th>
          <th class="num"><button class="sort legend" onclick={() => toggleSort('vmaf')}>VMAF {sortMark('vmaf')}</button></th>
          <th class="num"><button class="sort legend" onclick={() => toggleSort('saved')}>Saved {sortMark('saved')}</button></th>
        </tr>
      </thead>
      <tbody>
        {#each sorted as movie (movie.path)}
          {@const decision = decisionOf(movie)}
          <tr class:selected={selected?.path === movie.path}>
            <td class="name-cell">
              <button class="name-btn" onclick={() => openDetail(movie)}>
                {movie.name}
                {#if movie.hdr}<span class="mono hdr-tag">HDR</span>{/if}
              </button>
            </td>
            <td class="dim">{movie.category}</td>
            <td class="mono">{movie.resolution ? `${movie.resolution[0]}x${movie.resolution[1]}` : '—'}</td>
            <td class="mono num">{fmtGb(movie.size)}</td>
            <td>
              <span class="chip {decision.cls}"><span class="swatch"></span>{decision.label}</span>
            </td>
            <td class="mono num">{movie.encode?.crf ?? '—'}</td>
            <td class="mono num">{movie.encode?.vmaf?.toFixed(2) ?? '—'}</td>
            <td class="mono num saved" class:ok={savedOf(movie) > 0}>
              {movie.encode ? fmtSaved(movie.encode.saved_percent) : '—'}
            </td>
          </tr>
        {:else}
          <tr><td colspan="8" class="empty dim">No movies scanned yet — run a scan.</td></tr>
        {/each}
      </tbody>
    </table>
  </div>
</div>

<!-- detail drawer -->
{#if selected}
  <div
    class="overlay"
    role="presentation"
    onclick={(e) => e.target === e.currentTarget && (selected = null)}
    onkeydown={(e) => e.key === 'Escape' && (selected = null)}
    tabindex="-1"
  >
    <div class="drawer">
      <div class="head">
        <span class="title">{selected.name}</span>
        <button class="close" onclick={() => (selected = null)} aria-label="Close details">X</button>
      </div>
      <div class="path mono dim">{selected.path}</div>

      <div class="facts">
        <div class="fact"><span class="legend">CATEGORY</span><span>{selected.category}</span></div>
        <div class="fact"><span class="legend">DURATION</span><span class="mono">{selected.duration ?? '—'}</span></div>
        <div class="fact"><span class="legend">CODEC</span><span class="mono">{selected.codec ?? '—'}</span></div>
        <div class="fact"><span class="legend">RESOLUTION</span><span class="mono">{selected.resolution?.join('x') ?? '—'}</span></div>
        <div class="fact"><span class="legend">SIZE</span><span class="mono">{fmtGb(selected.size)}</span></div>
        <div class="fact"><span class="legend">HDR</span><span class="mono">{selected.hdr ? 'yes' : 'no'}</span></div>
      </div>

      {#if selected.encode}
        <div class="encode-box">
          <div class="legend">LATEST ENCODE</div>
          <div class="facts">
            <div class="fact"><span class="legend">PRESET</span><span class="mono">{selected.encode.preset}</span></div>
            <div class="fact"><span class="legend">CRF</span><span class="mono">{selected.encode.crf ?? '—'}</span></div>
            <div class="fact"><span class="legend">VMAF</span><span class="mono">{selected.encode.vmaf?.toFixed(2) ?? '—'}</span></div>
            <div class="fact"><span class="legend">NEW SIZE</span><span class="mono">{fmtGb(selected.encode.output_size)}</span></div>
            <div class="fact"><span class="legend">SAVED</span><span class="mono">{fmtSaved(selected.encode.saved_percent)}</span></div>
          </div>
        </div>
      {/if}

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
            <span class="dim">{job.error ?? ''}</span>
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
  .toolbar {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 12px;
    flex-wrap: wrap;
  }
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
    color: var(--ink);
    border-color: var(--ink-dim);
    background: var(--panel-active);
  }
  .right { display: flex; gap: 8px; align-items: center; }
  .dup-btn {
    background: transparent;
    border: 1px solid var(--warn);
    color: var(--warn);
    padding: 4px 12px;
    cursor: pointer;
  }
  .dup-btn:hover { background: rgba(210, 153, 34, 0.12); }
  .settings-btn {
    background: transparent;
    border: 1px solid var(--seam);
    color: var(--ink-dim);
    padding: 4px 12px;
    cursor: pointer;
  }
  .settings-btn:hover { color: var(--ink); border-color: var(--ink-dim); }
  .table-wrap {
    flex: 1;
    border: 1px solid var(--seam);
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
  }
  th.num { text-align: right; }
  .sort {
    background: transparent;
    border: none;
    color: inherit;
    font-size: inherit;
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
  .name-btn:hover { color: var(--magenta); }
  tr.selected td { background: var(--panel-active); }
  .dim { color: var(--ink-dim); }
  .saved.ok { color: var(--ok); }
  .hdr-tag {
    margin-left: 8px;
    color: var(--magenta);
    font-size: 10px;
    border: 1px solid var(--seam);
    padding: 0 4px;
    border-radius: 2px;
  }
  .empty { text-align: center; padding: 24px; }
  .overlay {
    position: fixed;
    inset: 0;
    background: rgba(12, 13, 15, 0.72);
    display: flex;
    justify-content: flex-end;
    z-index: 40;
  }
  .drawer {
    width: min(460px, 92vw);
    background: var(--panel);
    border-left: 1px solid var(--seam);
    padding: 18px 22px;
    overflow: auto;
  }
  .head { display: flex; justify-content: space-between; align-items: center; }
  .title { font-size: var(--fs-title); font-weight: 700; }
  .close {
    background: transparent;
    border: 1px solid var(--seam);
    color: var(--ink-dim);
    cursor: pointer;
    padding: 2px 8px;
  }
  .close:hover { color: var(--ink); }
  .path { margin: 4px 0 14px; word-break: break-all; font-size: 11px; }
  .facts { display: flex; flex-direction: column; gap: 4px; margin-bottom: 14px; }
  .fact { display: flex; justify-content: space-between; gap: 12px; }
  .encode-box { border: 1px solid var(--seam); padding: 12px 14px; margin-bottom: 14px; }
  .encode-box .legend { display: block; margin-bottom: 8px; }
  .hist-head { display: block; margin: 6px 0; }
  .hist-row {
    display: flex;
    gap: 14px;
    padding: 4px 0;
    border-bottom: 1px solid var(--row-line);
    font-size: var(--fs-data);
  }
  .hist-row:last-child { border-bottom: none; }
  .ok { color: var(--ok); }
  .bad { color: var(--bad); }
</style>
