<script lang="ts">
  import type { Movie, Status, Job } from '../api'
  import { fmtGb, fmtSeconds } from '../api'

  let {
    movies,
    history,
    status,
    targetVmaf,
    savedTotalGb,
    measuredCount,
    duplicates,
    duplicateWastedGb,
    missingMovies,
    runningJobs,
    failedJobs,
    scanPaths,
    outputDir,
    ondismiss,
    ongoto,
    onopenmovie,
  }: {
    movies: Movie[]
    history: Job[]
    status: Status | null
    targetVmaf: number
    savedTotalGb: number
    measuredCount: number
    duplicates: number
    duplicateWastedGb: number
    missingMovies: Movie[]
    runningJobs: Job[]
    failedJobs: Job[]
    scanPaths: string[]
    outputDir: string
    ondismiss: (id: number | null) => void
    ongoto: (page: 'overview' | 'library' | 'duplicates' | 'jobs' | 'config') => void
    onopenmovie: (movie: Movie) => void
  } = $props()

  const encodedCount = $derived(movies.filter((m) => m.encode && !m.encode.preset?.startsWith('skipped')).length)
  const untouchedCount = $derived(movies.filter((m) => !m.encode).length)
  const totalSizeGb = $derived(movies.reduce((sum, m) => sum + (m.size ?? 0), 0) / 1024 ** 3)

  // mean measured VMAF
  const measured = $derived(movies.filter((m) => m.encode?.vmaf != null))
  const meanVmaf = $derived(
    measured.length
      ? measured.reduce((sum, m) => sum + (m.encode?.vmaf ?? 0), 0) / measured.length
      : null
  )

  // encode cost statistics (schema v5)
  const withDuration = $derived(movies.filter((m) => m.encode?.encode_seconds != null))
  const totalEncodeHours = $derived(
    withDuration.reduce((sum, m) => sum + (m.encode?.encode_seconds ?? 0), 0) / 3600
  )

  const runningJob = $derived(runningJobs.find((j) => j.state === 'running') ?? null)
  const runningProgress = $derived(
    (runningJob?.result as { progress?: { movie?: string; percent?: number; done?: number; total?: number; detail?: string } } | null)?.progress ?? null
  )
</script>

<div class="page page-in">
  <header class="page-head">
    <h2>Overview</h2>
    <p class="legend">THE LIBRARY AT A GLANCE · VMAF TARGET {targetVmaf}</p>
  </header>

  <!-- system line: the console's own telemetry -->
  <div class="sysline mono dim">
    <span>POLL 3s</span>
    <span>DB {status?.database?.split(/[\\/]/).pop() ?? '—'}</span>
    <span>{movies.length} TITLES · {measuredCount} MEASURED</span>
    <span>Q {runningJobs.length} · FAIL {failedJobs.length} · DUPES {duplicates}</span>
    <span class="spacer"></span>
    <span>{scanPaths.length} SOURCE{scanPaths.length === 1 ? '' : 'S'} → {outputDir || '—'}</span>
  </div>

  <!-- ══ stat band ══ -->
  <div class="stats">
    <button class="stat" onclick={() => ongoto('library')}>
      <span class="legend">TITLES</span>
      <span class="stat-v mono">{movies.length}</span>
      <span class="stat-sub mono dim">{encodedCount} encoded · {untouchedCount} untouched</span>
    </button>
    <button class="stat" onclick={() => ongoto('library')}>
      <span class="legend">RECLAIMED</span>
      <span class="stat-v mono">{savedTotalGb.toFixed(1)} GB</span>
      <span class="stat-sub mono dim">of {totalSizeGb.toFixed(0)} GB source</span>
    </button>
    <button class="stat">
      <span class="legend">MEAN VMAF</span>
      <span class="stat-v mono">{meanVmaf ? meanVmaf.toFixed(1) : '—'}</span>
      <span class="stat-sub mono dim">{measuredCount} measured</span>
    </button>
    <button class="stat" onclick={() => ongoto('duplicates')}>
      <span class="legend">DUPLICATES</span>
      <span class="stat-v mono" class:warn={duplicates > 0}>{duplicates}</span>
      <span class="stat-sub mono dim">{duplicateWastedGb.toFixed(2)} GB wasted</span>
    </button>
    {#if missingMovies.length > 0}
      <button class="stat" onclick={() => ongoto('library')}>
        <span class="legend">MISSING</span>
        <span class="stat-v mono bad">{missingMovies.length}</span>
        <span class="stat-sub mono dim">flagged by the last scan</span>
      </button>
    {/if}
    {#if totalEncodeHours > 0}
      <div class="stat">
        <span class="legend">ENCODE TIME</span>
        <span class="stat-v mono">{totalEncodeHours.toFixed(1)} h</span>
        <span class="stat-sub mono dim">{withDuration.length} runs measured</span>
      </div>
    {/if}
  </div>

  <!-- ══ the figure in progress ══ -->
  {#if runningJob}
    <div class="running">
      <div class="running-head">
        <span class="legend amber">IN WORK · {runningJob.kind.toUpperCase()}</span>
        <span class="mono dim">{runningProgress?.done != null && runningProgress?.total != null ? `${runningProgress.done}/${runningProgress.total} · ` : ''}{runningProgress?.percent != null ? `${runningProgress.percent}%` : ''}</span>
      </div>
      <div class="running-title">{runningProgress?.movie || (runningJob.payload?.path as string)?.split(/[\\/]/).pop() || runningJob.kind}</div>
      <div class="trace" role="progressbar" aria-valuenow={runningProgress?.percent ?? 0} aria-valuemin="0" aria-valuemax="100" aria-label="encode progress">
        <div class="fill" style="width: {runningProgress?.percent ?? 2}%"></div>
      </div>
      <div class="mono dim detail">{runningProgress?.detail ?? 'working…'}</div>
    </div>
  {:else}
    <div class="quiet-strip">
      <span class="mono dim">no active work</span>
      <button class="act" onclick={() => ongoto('library')}>GO TO LIBRARY</button>
    </div>
  {/if}

  <div class="cols">
    <!-- ══ failures ══ -->
    <section class="panel">
      <div class="panel-head">
        <span class="legend">ATTENTION — FAILURES</span>
        {#if failedJobs.length > 0}<button class="link legend" onclick={() => ongoto('jobs')}>ALL JOBS →</button>{/if}
      </div>
      {#if failedJobs.length === 0}
        <div class="dim pad">nothing failed — the last runs are clean</div>
      {:else}
        {#each failedJobs.slice(0, 4) as job (job.id)}
          <div class="fail-row">
            <span class="mono bad">#{job.id} {job.kind}</span>
            <span class="mono dim err">{job.error}</span>
            <button class="link mono" onclick={() => ondismiss(job.id)}>dismiss</button>
          </div>
        {/each}
      {/if}
    </section>

    <!-- ══ missing files ══ -->
    <section class="panel">
      <div class="panel-head">
        <span class="legend">MISSING FILES</span>
      </div>
      {#if missingMovies.length === 0}
        <div class="dim pad">every file accounted for at the last scan</div>
      {:else}
        {#each missingMovies.slice(0, 4) as movie (movie.path)}
          <div class="fail-row">
            <span class="mono bad">{movie.name}</span>
            <span class="mono dim err">absent since {movie.missing_since?.slice(0, 10) ?? 'last scan'}</span>
            <button class="link mono" onclick={() => onopenmovie(movie)}>details</button>
          </div>
        {/each}
        {#if missingMovies.length > 4}
          <button class="link legend more" onclick={() => ongoto('library')}>{missingMovies.length - 4} more →</button>
        {/if}
      {/if}
    </section>

    <!-- ══ setup ══ -->
    <section class="panel">
      <div class="panel-head">
        <span class="legend">SETUP</span>
        <button class="link legend" onclick={() => ongoto('config')}>CONFIG →</button>
      </div>
      <div class="setup mono">
        <div class="fact"><span class="legend">SCAN FROM</span><span class="src">{scanPaths.join(' · ') || '—'}</span></div>
        <div class="fact"><span class="legend">WRITE TO</span><span class="src">{outputDir || '—'}</span></div>
        <div class="fact"><span class="legend">DATABASE</span><span class="src">{status?.database ?? '—'}</span></div>
        <div class="fact"><span class="legend">WATCHER</span><span class="src">{status?.watcher ?? '—'}</span></div>
      </div>
    </section>
  </div>
</div>

<style>
  .page { display: flex; flex-direction: column; gap: 14px; }
  .page-head h2 { font-size: var(--fs-page); font-weight: 700; }
  .page-head .legend { margin-top: 2px; }
  .dim { color: var(--ink-dim); }
  .warn { color: var(--amber); }
  .bad { color: var(--red); }
  .amber { color: var(--amber); }

  .sysline {
    display: flex;
    gap: 16px;
    align-items: baseline;
    font-size: 10.5px;
    letter-spacing: 0.4px;
    padding: 0 2px;
    flex-wrap: wrap;
  }
  .sysline .spacer { flex: 1; }

  /* stat band — dense, tile-sized, data-first */
  .stats {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(128px, 1fr));
    gap: 1px;
    background: var(--seam);
    border: 1px solid var(--seam);
    border-radius: var(--radius);
    overflow: hidden;
  }
  .stat {
    display: flex;
    flex-direction: column;
    gap: 2px;
    background: var(--panel);
    padding: 9px 12px;
    text-align: left;
    color: inherit;
    font: inherit;
    cursor: pointer;
    border: none;
  }
  .stat:hover { background: var(--panel-raised); }
  .stat-v { font-size: 17px; font-weight: 600; }
  .stat-sub { font-size: 10px; }

  /* running band */
  .running {
    border: 1px solid var(--seam);
    border-radius: var(--radius);
    background: repeating-linear-gradient(45deg, var(--teal-glow) 0 4px, transparent 4px 8px), var(--panel);
    padding: 12px 16px;
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  .running-head { display: flex; justify-content: space-between; align-items: center; }
  .running-title { font-weight: 600; font-size: 14px; }
  .trace {
    height: 10px;
    border-radius: 5px;
    background: var(--hatch-work);
    overflow: hidden;
  }
  .fill { height: 100%; background: var(--teal); }
  .detail { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .quiet-strip {
    display: flex;
    justify-content: space-between;
    align-items: center;
    border: 1px dashed var(--seam);
    border-radius: var(--radius);
    padding: 10px 16px;
  }

  /* columns */
  .cols {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
    gap: 10px;
  }
  .panel {
    border: 1px solid var(--seam);
    border-radius: var(--radius);
    background: var(--panel);
    display: flex;
    flex-direction: column;
  }
  .panel-head {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 9px 14px;
    border-bottom: 1px solid var(--row-line);
  }
  .pad { padding: 16px 14px; }
  .fail-row {
    display: flex;
    align-items: baseline;
    gap: 10px;
    padding: 7px 14px;
    border-bottom: 1px solid var(--row-line);
    font-size: var(--fs-data);
  }
  .fail-row:last-of-type { border-bottom: none; }
  .err { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .link {
    background: transparent;
    border: none;
    color: var(--teal-ink);
    cursor: pointer;
    padding: 0;
    font-size: inherit;
    letter-spacing: inherit;
    text-transform: inherit;
  }
  .link:hover { text-decoration: underline; }
  .more { padding: 8px 14px; text-align: left; }
  .setup { display: flex; flex-direction: column; gap: 6px; padding: 10px 14px 14px; }
  .fact { display: flex; justify-content: space-between; gap: 12px; }
  .src { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 320px; }

  .act {
    background: var(--panel-raised);
    border: 1px solid var(--seam);
    border-radius: var(--radius);
    color: var(--ink);
    padding: 4px 12px;
    cursor: pointer;
    font-size: var(--fs-legend);
    letter-spacing: var(--track-legend);
    text-transform: uppercase;
  }
  .act:hover { border-color: var(--teal); color: var(--teal-ink); }
</style>
